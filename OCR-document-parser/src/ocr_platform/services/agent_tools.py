import re
import socket
import concurrent.futures
from functools import lru_cache
import requests
from bs4 import BeautifulSoup
from pydantic_ai import RunContext

from ocr_platform.observability.logging import get_logger

logger = get_logger(__name__)

DDGS_TIMEOUT = 15

_inn_search_cache: dict[str, str] = {}
_name_search_cache: dict[str, str] = {}


def _extract_inn_from_text(text: str) -> str | None:
    """Извлечь ИНН из произвольного текста."""
    # Сначала ищем явно с меткой ИНН
    match = re.search(r"(?:ИНН|инн)[:\s]*(\d{10}|\d{12})\b", text, re.IGNORECASE)
    if match:
        return match.group(1)
    # Потом просто 10- или 12-значное число
    match = re.search(r"\b(\d{10}|\d{12})\b", text)
    if match:
        return match.group(1)
    return None


def _fetch_page_text(url: str, timeout: int = 8) -> str | None:
    """Загрузить страницу и вернуть её plain-text, None при ошибке."""
    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        }
        resp = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "html.parser")
            return soup.get_text(" ", strip=True)
    except Exception as exc:
        logger.debug("fetch_page_failed", url=url, error=str(exc))
    return None


def search_creditor_inn(ctx: RunContext[str], creditor_name: str) -> str:
    """Search for the INN of a creditor by their name using zachestnyibiznes.ru and DuckDuckGo fallback."""
    cached = _inn_search_cache.get(creditor_name)
    if cached is not None:
        logger.info("web_search_creditor_inn_cache_hit", creditor_name=creditor_name, inn=cached)
        return cached

    logger.info("web_search_creditor_inn", creditor_name=creditor_name)

    # --- Шаг 1: Параллельный поиск через DuckDuckGo, Yahoo и DaData ---
    try:
        from duckduckgo_search import DDGS

        def _do_ddg_search_inn(q: str) -> list:
            with DDGS() as ddg:
                return list(ddg.text(q, max_results=5, safesearch="off"))

        def _do_yahoo_search_inn(q: str) -> str:
            import requests
            from bs4 import BeautifulSoup
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
            resp = requests.get(f"https://search.yahoo.com/search?p={q}", headers=headers, timeout=5)
            return BeautifulSoup(resp.text, "html.parser").get_text(separator=" ")

        def _do_dadata_search_inn(q: str) -> str:
            from ocr_platform.config.settings import get_settings
            import requests
            api_key = get_settings().dadata_api_key
            if not api_key:
                return ""
            headers = {"Content-Type": "application/json", "Accept": "application/json", "Authorization": f"Token {api_key}"}
            resp = requests.post("https://suggestions.dadata.ru/suggestions/api/4_1/rs/suggest/party", json={"query": q}, headers=headers, timeout=5)
            if resp.status_code == 200:
                results = resp.json().get("suggestions", [])
                if results and results[0].get("data", {}).get("inn"):
                    return results[0]["data"]["inn"]
            return ""

        query = f"{creditor_name} ИНН"
        pool = concurrent.futures.ThreadPoolExecutor()
        try:
            future_ddg = pool.submit(_do_ddg_search_inn, query)
            future_yahoo = pool.submit(_do_yahoo_search_inn, query)
            future_dadata = pool.submit(_do_dadata_search_inn, creditor_name)
            
            # Быстрая проверка DaData (наиболее точный результат)
            try:
                dadata_inn = future_dadata.result(timeout=5)
                if dadata_inn:
                    logger.info("inn_found_via_dadata", inn=dadata_inn)
                    _inn_search_cache[creditor_name] = f"Found INN: {dadata_inn}"
                    return _inn_search_cache[creditor_name]
            except Exception as e:
                logger.warning("dadata_search_failed", error=str(e))

            # Быстрая проверка Yahoo
            try:
                yahoo_text = future_yahoo.result(timeout=5)
                inn = _extract_inn_from_text(yahoo_text)
                if inn:
                    logger.info("inn_found_via_yahoo", inn=inn)
                    _inn_search_cache[creditor_name] = f"Found INN: {inn}"
                    return _inn_search_cache[creditor_name]
            except Exception as e:
                logger.warning("yahoo_search_failed", error=str(e))

            # Проверка DuckDuckGo
            results = future_ddg.result(timeout=DDGS_TIMEOUT)
        except concurrent.futures.TimeoutError:
            logger.warning("search_pool_timed_out", creditor_name=creditor_name)
            results = []
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

        logger.info("ddg_search_results", count=len(results), query=query)

        # Ищем ИНН прямо в сниппетах (быстрый путь)
        for r in results:
            snippet = f"{r.get('title', '')} {r.get('body', '')}"
            inn = _extract_inn_from_text(snippet)
            if inn:
                logger.info("inn_found_in_snippet", inn=inn, url=r.get("href"))
                _inn_search_cache[creditor_name] = f"Found INN: {inn}"
                return _inn_search_cache[creditor_name]

        # Заходим на первые 3 страницы и ищем там
        for r in results[:3]:
            page_url = r.get("href", "")
            if not page_url:
                continue
            page_text = _fetch_page_text(page_url)
            if page_text:
                inn = _extract_inn_from_text(page_text)
                if inn:
                    logger.info("inn_found_on_page", inn=inn, url=page_url)
                    _inn_search_cache[creditor_name] = f"Found INN: {inn}"
                    return _inn_search_cache[creditor_name]

    except concurrent.futures.TimeoutError:
        logger.warning("ddg_search_timed_out", creditor_name=creditor_name)
    except ImportError:
        logger.warning("duckduckgo_search_not_installed")
    except Exception as exc:
        logger.warning("ddg_search_failed", error=str(exc))

    # --- Шаг 2: Фоллбэк на прямой HTML запрос к DuckDuckGo ---
    logger.info("fallback_to_ddg_html_for_inn", creditor_name=creditor_name)
    try:
        query = f"{creditor_name} ИНН"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        resp = requests.post(
            "https://html.duckduckgo.com/html/",
            data={"q": query},
            headers=headers,
            timeout=10,
        )
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "html.parser")
            for a in soup.find_all("a", class_="result__snippet")[:5]:
                snippet = a.text
                inn = _extract_inn_from_text(snippet)
                if inn:
                    logger.info("inn_found_in_html_snippet", inn=inn)
                    _inn_search_cache[creditor_name] = f"Found INN: {inn}"
                    return _inn_search_cache[creditor_name]
    except Exception as exc:
        logger.warning("ddg_html_fallback_failed", error=str(exc))

    _inn_search_cache[creditor_name] = "INN not found. Could not locate the INN for the given creditor name via web search."
    return _inn_search_cache[creditor_name]


def search_creditor_name(ctx: RunContext[str], inn: str) -> str:
    """Tool for LLM: search for company name by its INN using zachestnyibiznes.ru and DuckDuckGo."""
    cached = _name_search_cache.get(inn)
    if cached is not None:
        logger.info("search_creditor_name_cache_hit", inn=inn)
        return cached
    result = _search_by_inn(inn)
    _name_search_cache[inn] = result or "Company name not found"
    return _name_search_cache[inn]


@lru_cache(maxsize=128)
def _search_by_inn(inn: str) -> str | None:
    """Internal: search for company details/name by INN using DaData API."""
    logger.info("search_by_inn", inn=inn, provider="dadata")
    
    from ocr_platform.config.settings import get_settings
    settings = get_settings()
    
    api_key = settings.dadata_api_key
    if not api_key:
        logger.warning("dadata_api_key_missing", inn=inn)
        return None
        
    url = "https://suggestions.dadata.ru/suggestions/api/4_1/rs/findById/party"
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"Token {api_key}"
    }
    data = {"query": inn}
    
    try:
        resp = requests.post(url, json=data, headers=headers, timeout=10)
        if resp.status_code == 200:
            results = resp.json().get("suggestions", [])
            if not results:
                logger.info("dadata_search_empty", inn=inn)
                return None
                
            # Take the first best match
            best_match = results[0]
            data_fields = best_match.get("data", {})
            name = best_match.get("value", "")
            
            # Use full name if available, fallback to short name or value
            full_name = data_fields.get("name", {}).get("full") or name
            short_name = data_fields.get("name", {}).get("short") or name
            status = data_fields.get("state", {}).get("status", "UNKNOWN")
            
            texts = [
                f"Title: {short_name}",
                f"Full Name: {full_name}",
                f"Status: {status}",
                f"Snippet: Организация с ИНН {inn} найдена через DaData.",
                "Source URL: https://dadata.ru/"
            ]
            return "\n".join(texts)
        else:
            logger.warning("dadata_api_error", status_code=resp.status_code, error=resp.text)
    except Exception as exc:
        logger.warning("dadata_request_failed", error=str(exc))
        
    return None

import re
import socket
import concurrent.futures
import requests
from bs4 import BeautifulSoup
from pydantic_ai import RunContext

from ocr_platform.observability.logging import get_logger

logger = get_logger(__name__)

DDGS_TIMEOUT = 15


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
    logger.info("web_search_creditor_inn", creditor_name=creditor_name)

    # --- Шаг 1: DuckDuckGo поиск (с таймаутом) ---
    try:
        from duckduckgo_search import DDGS

        def _do_ddg_search_inn(q: str) -> list:
            with DDGS() as ddg:
                return list(ddg.text(q, max_results=5, safesearch="off"))

        query = f"{creditor_name} ИНН"
        pool = concurrent.futures.ThreadPoolExecutor()
        try:
            future = pool.submit(_do_ddg_search_inn, query)
            results = future.result(timeout=DDGS_TIMEOUT)
        except concurrent.futures.TimeoutError:
            logger.warning("ddg_search_timed_out", creditor_name=creditor_name)
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
                return f"Found INN: {inn}"

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
                    return f"Found INN: {inn}"

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
                    return f"Found INN: {inn}"
    except Exception as exc:
        logger.warning("ddg_html_fallback_failed", error=str(exc))

    return "INN not found. Could not locate the INN for the given creditor name via web search."


def search_creditor_name(ctx: RunContext[str], inn: str) -> str:
    """Tool for LLM: search for company name by its INN using zachestnyibiznes.ru and DuckDuckGo."""
    result = _search_by_inn(inn)
    return result or "Company name not found"


def _search_by_inn(inn: str) -> str | None:
    """Internal: search for company details/name by INN."""
    logger.info("search_by_inn", inn=inn)
    texts = []

    # --- Step 1: DuckDuckGo search (with timeout) ---
    try:
        from duckduckgo_search import DDGS

        def _do_ddg_search(q: str) -> list:
            with DDGS() as ddg:
                return list(ddg.text(q, max_results=3, safesearch="off"))

        query = f"ИНН {inn} реквизиты организация"
        pool = concurrent.futures.ThreadPoolExecutor()
        try:
            future = pool.submit(_do_ddg_search, query)
            results = future.result(timeout=DDGS_TIMEOUT)
        except concurrent.futures.TimeoutError:
            logger.warning("ddg_search_timed_out_in_name_search", inn=inn)
            results = []
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

        for r in results:
            snippet = f"Title: {r.get('title', '')}\nSnippet: {r.get('body', '')}\n"
            texts.append(snippet)
            page_url = r.get("href", "")
            if page_url:
                texts.append(f"Source URL: {page_url}\n")

        # Validate that the search result is relevant (contains 'инн' or the actual inn number)
        if texts:
            combined_text = "".join(texts).lower()
            if "инн" not in combined_text and inn not in combined_text:
                logger.info(
                    "ddg_search_results_rejected",
                    reason="Missing 'инн' keyword and INN number",
                    inn=inn,
                )
                texts = []

    except Exception as exc:
        logger.warning("ddg_search_failed_in_name_search", error=str(exc))

    # --- Step 2: Fallback to direct DuckDuckGo HTML ---
    if not texts:
        logger.info("fallback_to_ddg_html_for_name", inn=inn)
        try:
            query = f"ИНН {inn} реквизиты организация"
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
            socket.setdefaulttimeout(10)
            try:
                resp = requests.post(
                    "https://html.duckduckgo.com/html/",
                    data={"q": query},
                    headers=headers,
                    timeout=10,
                )
            finally:
                socket.setdefaulttimeout(None)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, "html.parser")
                results = soup.find_all("div", class_="result")
                for res in results[:3]:
                    title_elem = res.find("h2", class_="result__title")
                    snippet_elem = res.find("a", class_="result__snippet")
                    if title_elem and snippet_elem:
                        title = title_elem.text.strip()
                        snippet = snippet_elem.text.strip()
                        texts.append(f"Title: {title}\nSnippet: {snippet}\n")

                # Validate HTML fallback results
                if texts:
                    combined_text = "".join(texts).lower()
                    if "инн" not in combined_text and inn not in combined_text:
                        logger.info(
                            "ddg_html_results_rejected",
                            reason="Missing 'инн' keyword and INN number",
                            inn=inn,
                        )
                        texts = []

        except Exception as exc:
            logger.warning("ddg_html_fallback_failed", error=str(exc))

    # --- Step 3: Прямой запрос к list-org (всегда, как дополнительный источник) ---
    logger.info("fetching_list_org_for_name", inn=inn)
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        url = f"https://www.list-org.com/search?type=inn&val={inn}"
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            import re

            soup = BeautifulSoup(resp.text, "html.parser")
            org_names = set()
            for match in re.finditer(
                r"(ООО|ПКО|АО|ЗАО|ОАО)\s[^<]{3,80}(?=<)", resp.text
            ):
                name = match.group(0).strip()
                if name not in org_names:
                    org_names.add(name)
                    texts.append(f"Title: {name}\nSnippet: Организация с ИНН {inn}\n")
                    texts.append(
                        f"Source URL: https://www.list-org.com/search?type=inn&val={inn}\n"
                    )
            if not org_names:
                h1 = soup.find("h1")
                if h1:
                    texts.append(
                        f"Title: {h1.get_text(strip=True)}\nSnippet: Результат поиска по ИНН {inn}\n"
                    )
                    texts.append(
                        f"Source URL: https://www.list-org.com/search?type=inn&val={inn}\n"
                    )
    except Exception as exc:
        logger.warning("list_org_fallback_failed", error=str(exc))

    if not texts:
        return None

    return "\n\n=== NEW SOURCE ===\n\n".join(texts)

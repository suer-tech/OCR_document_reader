import re
import requests
from bs4 import BeautifulSoup
from pydantic_ai import RunContext

from ocr_platform.observability.logging import get_logger

logger = get_logger(__name__)


def _extract_inn_from_text(text: str) -> str | None:
    """Извлечь ИНН из произвольного текста."""
    # Сначала ищем явно с меткой ИНН
    match = re.search(r'(?:ИНН|инн)[:\s]*(\d{10}|\d{12})\b', text, re.IGNORECASE)
    if match:
        return match.group(1)
    # Потом просто 10- или 12-значное число
    match = re.search(r'\b(\d{10}|\d{12})\b', text)
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

    # --- Шаг 1: DuckDuckGo поиск ---
    try:
        from duckduckgo_search import DDGS

        query = f"{creditor_name} ИНН"
        with DDGS() as ddg:
            results = list(ddg.text(query, max_results=5, safesearch="off"))

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

    except ImportError:
        logger.warning("duckduckgo_search_not_installed")
    except Exception as exc:
        logger.warning("ddg_search_failed", error=str(exc))

    return "INN not found. Could not locate the INN for the given creditor name via web search."


def search_creditor_name(ctx: RunContext[str], inn: str) -> str:
    """Tool for LLM: search for company name by its INN using zachestnyibiznes.ru and DuckDuckGo."""
    result = _search_by_inn(inn)
    return result or "Company name not found"


def _search_by_inn(inn: str) -> str | None:
    """Internal: search for company details/name by INN."""
    logger.info("search_by_inn", inn=inn)
    texts = []

    # --- Step 1: DuckDuckGo search ---
    try:
        from duckduckgo_search import DDGS
        query = f"ИНН {inn} реквизиты организация"
        with DDGS() as ddg:
            results = list(ddg.text(query, max_results=3, safesearch="off"))

        for r in results:
            snippet = f"Title: {r.get('title', '')}\nSnippet: {r.get('body', '')}\n"
            texts.append(snippet)
            page_url = r.get("href", "")
            if page_url:
                page_text = _fetch_page_text(page_url)
                if page_text:
                    texts.append(f"Source URL: {page_url}\n{page_text[:2000]}")
    except Exception as exc:
        logger.warning("ddg_search_failed_in_name_search", error=str(exc))

    if not texts:
        return None

    return "\n\n=== NEW SOURCE ===\n\n".join(texts)


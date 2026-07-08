from __future__ import annotations

import os
from typing import Any

from ocr_platform.observability.logging import get_logger

logger = get_logger(__name__)


_EXTARCTION_SYSTEM_DEFAULT = (
    "You are an expert legal document analyst. "
    "Extract the requested field accurately based on the provided text and instructions. "
    "IMPORTANT: You MUST respond ONLY with raw, valid JSON matching the requested schema. "
    "Do not wrap the JSON in markdown blocks like ```json ... ```. "
    "Do not include any other text."
)

_DOC_TYPE_DETECTION_DEFAULT = (
    "Определи тип документа по тексту. "
    "Верни JSON строго по переданной схеме. "
    "Если не уверен, верни unknown."
)

_COMPANY_NAME_EXTRACTION_DEFAULT = (
    "You are an expert business registrar analyst. "
    "Analyze only the provided search results to find the official company name or organization name "
    "corresponding to the given INN. "
    "Use standard Russian legal-form abbreviations such as ООО, ПАО, АО, and ПКО, "
    "but do not abbreviate the entity's own name. "
    "Return the name clearly in the company_name field. If no company name is found, return null."
)

_COMPANY_COMPARISON_DEFAULT = (
    "You are an expert entity resolution system. "
    "Compare two organization names: one extracted from the document via OCR, "
    "and the other found in the official registry/internet by INN. "
    "Determine if they represent the same legal entity/organization. "
    "Classify the difference as:\n"
    "- 'exact': the names are identical or have only minor formatting differences "
    "(e.g. quotes, lowercase/uppercase, spacing).\n"
    "- 'minor': there are small typos/OCR errors "
    "(e.g. one or a few characters differ) or minor abbreviation differences "
    "(e.g. ООО vs Общество с ограниченной ответственностью), "
    "but they clearly refer to the same entity.\n"
    "- 'critical': the names are completely different and refer to different entities."
)

_VISION_FALLBACK_DEFAULT = (
    "Этот документ был извлечён с помощью OCR, и в тексте есть искажения/ошибки. "
    "Посмотри на оригинальный PDF-документ и верни ПОЛНЫЙ текст документа без искажений, "
    "исправляя все ошибки OCR. Сохрани структуру документа."
)


class PromptProvider:
    _instance: PromptProvider | None = None

    def __init__(self) -> None:
        self._client: Any = None
        self._enabled = False
        self._try_init()

    def _try_init(self) -> None:
        pk = os.environ.get("LANGFUSE_PUBLIC_KEY")
        sk = os.environ.get("LANGFUSE_SECRET_KEY")
        if not pk or not sk:
            self._enabled = False
            return
        try:
            from langfuse import Langfuse

            self._client = Langfuse()
            self._enabled = True
        except Exception as exc:
            logger.warning("langfuse_prompt_init_failed", error=str(exc))
            self._enabled = False

    def get_prompt(
        self, name: str, default: str = "", *, cache_ttl: int = 300, **kwargs: Any
    ) -> str:
        if not self._enabled or not self._client:
            return default
        try:
            prompt = self._client.get_prompt(
                name, type="text", cache_ttl_seconds=cache_ttl
            )
            if prompt is not None:
                if kwargs:
                    return prompt.compile(**kwargs)
                return prompt.prompt
        except Exception:
            pass
        return default

    def get_prompt_version(self, name: str) -> int | None:
        if not self._enabled or not self._client:
            return None
        try:
            prompt = self._client.get_prompt(name, type="text")
            return prompt.version
        except Exception:
            return None


_provider: PromptProvider | None = None


def get_provider() -> PromptProvider:
    global _provider
    if _provider is None:
        _provider = PromptProvider()
    return _provider


def get_extraction_system_prompt() -> str:
    provider = get_provider()
    return provider.get_prompt("extraction_system", default=_EXTARCTION_SYSTEM_DEFAULT)


def get_doc_type_detection_prompt() -> str:
    provider = get_provider()
    return provider.get_prompt(
        "doc_type_detection", default=_DOC_TYPE_DETECTION_DEFAULT
    )


def get_company_name_extraction_prompt() -> str:
    provider = get_provider()
    return provider.get_prompt(
        "company_name_extraction", default=_COMPANY_NAME_EXTRACTION_DEFAULT
    )


def get_company_comparison_prompt() -> str:
    provider = get_provider()
    return provider.get_prompt(
        "company_comparison", default=_COMPANY_COMPARISON_DEFAULT
    )


def get_vision_fallback_prompt() -> str:
    provider = get_provider()
    return provider.get_prompt("vision_fallback", default=_VISION_FALLBACK_DEFAULT)


def get_field_instruction(profile_id: str, field_name: str, default: str = "") -> str:
    provider = get_provider()
    prompt_name = f"field_instruction_{profile_id}_{field_name}"
    return provider.get_prompt(prompt_name, default=default)


# Prompts to sync to Langfuse (name -> (content, description))
SYNC_PROMPTS: dict[str, tuple[str, str]] = {
    "extraction_system": (
        _EXTARCTION_SYSTEM_DEFAULT,
        "System prompt for all extraction agents (legal document analyst)",
    ),
    "doc_type_detection": (
        _DOC_TYPE_DETECTION_DEFAULT,
        "Prompt for document type classification via LLM",
    ),
    "company_name_extraction": (
        _COMPANY_NAME_EXTRACTION_DEFAULT,
        "Prompt for extracting company name from web search results",
    ),
    "company_comparison": (
        _COMPANY_COMPARISON_DEFAULT,
        "Prompt for comparing two company names (OCR vs registry)",
    ),
    "vision_fallback": (
        _VISION_FALLBACK_DEFAULT,
        "Prompt for vision-based OCR correction fallback",
    ),
}

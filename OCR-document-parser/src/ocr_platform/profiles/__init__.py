"""Реестр обработчиков профилей: profile_id -> ProfileHandler."""

from __future__ import annotations

from typing import Any, Dict

from ocr_platform.observability.logging import get_logger
from ocr_platform.profiles.court_decision_nlp import CourtDecisionNlpExtractor
from ocr_platform.profiles.court_decision_ru import CourtDecisionRuExtractor
from ocr_platform.profiles.unknown import UnknownExtractor

logger = get_logger(__name__)

_REGISTRY: Dict[str, Any] = {
    "court_decision_ru": CourtDecisionRuExtractor(),
    "unknown": UnknownExtractor(),
}

_EXTRACTORS: Dict[str, Any] = {
    "llm": CourtDecisionRuExtractor(),
    "nlp": CourtDecisionNlpExtractor(),
}


def get_profile_handler(profile_id: str, profile_config: dict[str, Any] | None = None) -> Any:
    """
    Получить обработчик профиля по profile_id.

    Для court_decision_ru выбор экстрактора (llm/nlp) — из profile_config["extractor"].
    По умолчанию llm. Если профиль не зарегистрирован, возвращается unknown.
    """
    if profile_id == "court_decision_ru" and profile_config:
        extractor_type = (profile_config.get("extractor") or "llm").lower().strip()
        handler = _EXTRACTORS.get(extractor_type)
        if handler is not None:
            return handler
        logger.warning(
            "court_decision_ru_unknown_extractor",
            extractor=extractor_type,
            fallback="llm",
        )
        return _EXTRACTORS["llm"]

    handler = _REGISTRY.get(profile_id)
    if handler is None:
        logger.warning(
            "profile_handler_not_found",
            profile_id=profile_id,
            fallback="unknown",
        )
        handler = _REGISTRY["unknown"]
    return handler

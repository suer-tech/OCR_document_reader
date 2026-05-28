"""Реестр обработчиков профилей: profile_id -> ProfileHandler."""

from __future__ import annotations

from typing import Any, Dict

from ocr_platform.observability.logging import get_logger
from ocr_platform.profiles.court_decision_nlp import CourtDecisionNlpExtractor
from ocr_platform.profiles.court_decision_ru import CourtDecisionRuExtractor
from ocr_platform.profiles.unknown import UnknownExtractor
from ocr_platform.profiles.rtk import RtkExtractor
from ocr_platform.profiles.rtk_nlp import RtkNlpExtractor

logger = get_logger(__name__)

_REGISTRY: Dict[str, Any] = {
    "court_decision_ru": CourtDecisionRuExtractor(),
    "rtk": RtkExtractor(),
    "unknown": UnknownExtractor(),
}

_EXTRACTORS: Dict[str, Any] = {
    "court_decision_ru": {
        "llm": CourtDecisionRuExtractor(),
        "nlp": CourtDecisionNlpExtractor(),
    },
    "rtk": {
        "llm": RtkExtractor(),
        "nlp": RtkNlpExtractor(),
    }
}


def get_profile_handler(profile_id: str, profile_config: dict[str, Any] | None = None) -> Any:
    """
    Получить обработчик профиля по profile_id.

    Выбор экстрактора (llm/nlp) из profile_config["extractor"].
    По умолчанию llm. Если профиль не зарегистрирован, возвращается unknown.
    """
    if profile_id in _EXTRACTORS and profile_config:
        extractor_type = (profile_config.get("extractor") or "llm").lower().strip()
        handler = _EXTRACTORS[profile_id].get(extractor_type)
        if handler is not None:
            return handler
        logger.warning(
            f"{profile_id}_unknown_extractor",
            extractor=extractor_type,
            fallback="llm",
        )
        return _EXTRACTORS[profile_id]["llm"]

    handler = _REGISTRY.get(profile_id)
    if handler is None:
        logger.warning(
            "profile_handler_not_found",
            profile_id=profile_id,
            fallback="unknown",
        )
        handler = _REGISTRY["unknown"]
    return handler

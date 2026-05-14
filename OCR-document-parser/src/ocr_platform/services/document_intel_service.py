"""Сервис экстракции полей: единая точка входа, диспетчеризация по профилям."""

from __future__ import annotations

from typing import Any, Dict

from ocr_platform.profiles import get_profile_handler


def simple_extract_fields(
    text: str,
    profile_config: dict[str, Any],
    profile_id: str,
    pipeline_run_id: str,
    document_id: str,
) -> Dict[str, dict]:
    """
    Извлечь структурированные поля из текста документа.

    Вызывает обработчик профиля по profile_id из реестра.
    Сохранение, метрики и MLflow остаются в orchestration/run_processor.
    """
    handler = get_profile_handler(profile_id, profile_config)
    return handler.extract(
        text=text,
        profile_config=profile_config,
        profile_id=profile_id,
        pipeline_run_id=pipeline_run_id,
        document_id=document_id,
    )

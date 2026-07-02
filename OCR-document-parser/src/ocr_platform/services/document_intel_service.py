from __future__ import annotations

from typing import Any, Dict


async def simple_extract_fields(
    text: str,
    profile_config: dict[str, Any],
    profile_id: str,
    pipeline_run_id: str,
    document_id: str,
    storage_path: str | None = None,
) -> Dict[str, dict]:
    """
    Извлечь структурированные поля из текста документа.

    Вызывает обработчик профиля по profile_id из реестра.
    Сохранение, метрики и MLflow остаются в orchestration/run_processor.
    """
    from ocr_platform.services.extraction_agent import run_agent_extraction

    fields_config = profile_config.get("fields", {})
    return await run_agent_extraction(
        text,
        fields_config,
        profile_id=profile_id,
        profile_config=profile_config,
        storage_path=storage_path,
    )

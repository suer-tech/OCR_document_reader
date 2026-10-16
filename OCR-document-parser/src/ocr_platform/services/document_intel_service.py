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
    Извлечь структурированные поля из текста или оригинальных страниц документа.

    Для direct_vision передаёт оригинал модели без предварительного OCR.
    Для остальных профилей использует существующее текстовое извлечение.
    Сохранение, метрики и MLflow остаются в orchestration/run_processor.
    """
    from ocr_platform.services import passport_vision_service

    if passport_vision_service.is_direct_vision_profile(profile_config):
        if not storage_path:
            raise ValueError("Passport direct vision requires the original document")
        return await passport_vision_service.extract_passport_fields(storage_path, profile_config)

    from ocr_platform.services.extraction_agent import run_agent_extraction

    fields_config = profile_config.get("fields", {})
    return await run_agent_extraction(
        text,
        fields_config,
        profile_id=profile_id,
        profile_config=profile_config,
        storage_path=storage_path,
    )

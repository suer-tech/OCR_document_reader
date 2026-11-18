from __future__ import annotations

from typing import Dict, Tuple

from ocr_platform.services.field_retries import empty_value, required_names


def compute_quality_scores(
    text: str, fields: Dict[str, dict], *,
    required_fields: set[str] | None = None, required_value_fields: set[str] | None = None,
) -> Tuple[float, float, float]:
    """
    Простейшая эвристика качества для MVP:
    - техническое качество считаем условно высоким, если текст не пустой;
    - семантическое качество привязываем к количеству заполненных полей;
    - итоговый скор — среднее из двух.
    """
    technical = 0.2
    if text.strip():
        technical = 0.8

    if required_value_fields is not None:
        filled = sum(
            isinstance(info := fields.get(name), dict)
            and not empty_value(info.get("value"))
            and not info.get("validation_issue")
            for name in required_value_fields
        )
        semantic = filled / len(required_value_fields) if required_value_fields else 0.0
        return technical, semantic, (technical + semantic) / 2.0

    if required_fields:
        scores = []
        for name in required_fields:
            info = fields.get(name, {})
            score = info.get("confidence") if info.get("value") is not None and not info.get("validation_issue") else None
            scores.append(score if score is not None else 0.0)
        semantic = sum(scores) / len(required_fields)
        return technical, semantic, (technical + semantic) / 2.0

    total_fields = len(fields)
    filled_fields = sum(
        1
        for name, f in fields.items()
        if isinstance(f, dict) and (
            f.get("value")
            or (name in {"early_report_required", "review_required"} and f.get("value") is False)
        )
    )
    semantic = (filled_fields / total_fields) if total_fields > 0 else 0.0

    overall = (technical + semantic) / 2.0
    return technical, semantic, overall


def compute_rtk3_quality_scores(text: str, fields: dict, fields_config: dict) -> tuple[float, float, float]:
    return compute_quality_scores(text, fields, required_value_fields=required_names(fields_config))

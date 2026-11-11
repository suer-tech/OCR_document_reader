from __future__ import annotations

from typing import Dict, Tuple


def compute_quality_scores(
    text: str, fields: Dict[str, dict], *, required_fields: set[str] | None = None,
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

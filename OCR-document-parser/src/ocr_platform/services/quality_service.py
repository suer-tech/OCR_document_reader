from __future__ import annotations

from typing import Dict, Tuple


def compute_quality_scores(text: str, fields: Dict[str, dict]) -> Tuple[float, float, float]:
    """
    Простейшая эвристика качества для MVP:
    - техническое качество считаем условно высоким, если текст не пустой;
    - семантическое качество привязываем к количеству заполненных полей;
    - итоговый скор — среднее из двух.
    """
    technical = 0.2
    if text.strip():
        technical = 0.8

    total_fields = len(fields)
    filled_fields = sum(1 for f in fields.values() if f.get("value"))
    semantic = (filled_fields / total_fields) if total_fields > 0 else 0.0

    overall = (technical + semantic) / 2.0
    return technical, semantic, overall


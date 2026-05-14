from __future__ import annotations

from typing import Dict, List, Tuple

from ocr_platform.api.schemas import ValidationIssue


def validate_fields(fields: Dict[str, dict]) -> Tuple[str, List[ValidationIssue]]:
    """
    Базовая валидация: проверка наличия обязательных полей.
    Конкретный список обязательных полей для профиля фиксируется здесь
    для MVP и должен быть вынесен в конфиг в будущем.
    """
    required_fields = [
        "debtor_full_name",
        "debtor_inn",
        "case_number",
        "judge_full_name",
        "court_name",
        "decision_date",
    ]

    issues: List[ValidationIssue] = []

    for name in required_fields:
        if name not in fields or fields[name].get("value") in (None, "", []):
            issues.append(
                ValidationIssue(
                    code="missing_required_field",
                    message=f"Отсутствует обязательное поле: {name}",
                    field_name=name,
                    severity="error",
                )
            )

    if not issues:
        status = "ok"
    else:
        status = "errors"

    return status, issues


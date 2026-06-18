from __future__ import annotations

from typing import Dict, List, Tuple, Any

from ocr_platform.api.schemas import ValidationIssue


def validate_fields(
    fields: Dict[str, dict],
    profile_id: str | None = None,
    profile_config: dict[str, Any] | None = None,
) -> Tuple[str, List[ValidationIssue]]:
    """
    Базовая валидация: проверка наличия обязательных полей.
    Динамически загружает список обязательных полей из profile_config.
    """
    required_fields = []
    if profile_config:
        extractor_type = (profile_config.get("extractor") or "llm").lower().strip()
        fields_section = f"fields_{extractor_type}"
        fields_cfg = profile_config.get(fields_section, {})
        if not fields_cfg:
            fields_cfg = (
                profile_config.get("fields_llm")
                or profile_config.get("fields_nlp")
                or {}
            )
        for field_name, cfg in fields_cfg.items():
            if isinstance(cfg, dict) and cfg.get("required", False):
                required_fields.append(field_name)
    else:
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


from __future__ import annotations

from typing import Any

from ocr_platform.api.schemas import ValidationIssue
from ocr_platform.services.court_decision_additional import (
    ADDITIONAL_FIELDS,
    COURT_ISSUE_CODE,
)
from ocr_platform.services.court_field_rules import FIELD_RULES as COURT_FIELDS
from ocr_platform.services.rtk3_claims import (
    CLAIMED_AMOUNT_REVIEW_CODES,
    claimed_amount_issues,
)
from ocr_platform.services.rtk3_validation import is_v2, rtk3_issues

PASSPORT_PROFILES = {"passport_main", "passport_registration"}


def _required_field_names(profile_config: dict[str, Any]) -> list[str]:
    extractor_type = (profile_config.get("extractor") or "llm").lower().strip()
    fields_cfg = (
        profile_config.get(f"fields_{extractor_type}")
        or profile_config.get("fields_llm")
        or profile_config.get("fields_nlp")
        or profile_config.get("fields")
        or {}
    )
    return [name for name, cfg in fields_cfg.items()
            if isinstance(cfg, dict) and cfg.get("required", False)]


def passport_field_issues(
    fields: dict, profile_id: str, profile_config: dict[str, Any] | None = None,
) -> list[ValidationIssue]:
    if profile_config is None:
        from ocr_platform.orchestration.router import load_profile

        profile_config = load_profile(profile_id)
    issues = []
    for name in _required_field_names(profile_config):
        field = fields.get(name)
        value = field.get("value") if isinstance(field, dict) else field
        if value is None or value == [] or value == {} or (isinstance(value, str) and not value.strip()):
            issues.append(ValidationIssue(
                code="passport_requires_review", message=f"Отсутствует обязательное поле: {name}",
                field_name=name, severity="error",
            ))
    return issues


def court_field_issues(fields: dict) -> list[ValidationIssue]:
    return [
        ValidationIssue(code=COURT_ISSUE_CODE, message=str(info["validation_issue"]),
                        field_name=name, severity="error")
        for name, info in fields.items()
        if isinstance(info, dict) and info.get("validation_issue") and (
            name in ADDITIONAL_FIELDS or (name in COURT_FIELDS and (info.get("extraction_method") or info.get("source")) in {
                "regex_jev", "court_llm_fallback"}))
    ]


def profile_field_issues(
    fields: dict, profile_id: str | None = None, profile_config: dict[str, Any] | None = None,
) -> list[ValidationIssue]:
    issues = court_field_issues(fields)
    if profile_id in PASSPORT_PROFILES:
        issues.extend(passport_field_issues(fields, profile_id, profile_config))
    if profile_id == "rtk2":
        from ocr_platform.services.rtk2_contract import rtk2_issues

        issues.extend(rtk2_issues(fields))
    if profile_id == "rtk3":
        if not is_v2(fields):
            issues.extend(claimed_amount_issues(fields))
        issues.extend(rtk3_issues(fields))
    return issues


def review_requirement(overall: float | None, issues: list[ValidationIssue]) -> tuple[bool, str | None]:
    if any(issue.code == COURT_ISSUE_CODE for issue in issues):
        return True, "court_field_requires_review"
    for issue in issues:
        if issue.code == "passport_requires_review":
            return True, issue.code
        if issue.code == "rtk2_requires_review":
            return True, issue.code
        if issue.code == "rtk3_requires_review":
            return True, issue.code
        if issue.code in CLAIMED_AMOUNT_REVIEW_CODES:
            return True, issue.code
    if overall is not None and overall >= 0.75:
        return False, None
    return True, "low_quality_or_missing_fields"


def validate_fields(
    fields: dict[str, dict],
    profile_id: str | None = None,
    profile_config: dict[str, Any] | None = None,
) -> tuple[str, list[ValidationIssue]]:
    """
    Базовая валидация: проверка наличия обязательных полей.
    Динамически загружает список обязательных полей из profile_config.
    """
    required_fields = []
    if profile_config:
        required_fields = _required_field_names(profile_config)
    else:
        required_fields = [
            "debtor_full_name",
            "debtor_inn",
            "case_number",
            "judge_full_name",
            "court_name",
            "decision_date",
        ]

    issues: list[ValidationIssue] = profile_field_issues(fields, profile_id, profile_config)

    if profile_id in PASSPORT_PROFILES:
        # The same completeness checks run for workers and historical GET results.
        required_fields = []
    elif profile_id == "rtk2":
        # Centralized checks also run when reading an already stored result.
        required_fields = []
    elif profile_id == "rtk3" and is_v2(fields):
        # RTK3 v2 checks completeness centrally, including in GET results.
        required_fields = []
    elif profile_id == "rtk3":
        # Historical results keep the original optional-field contract.
        required_fields = [name for name in required_fields if name in {
            "inclusion_date", "creditor", "secured_by_pledge"}]

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

from ocr_platform.orchestration.router import load_profile
from ocr_platform.services.rtk2_postprocessing import guard_rtk2_results
from ocr_platform.services.validation_service import validate_fields


def test_no_hearing_does_not_become_calculated_date() -> None:
    fields = {"hearing_date": {"value": "10.10.2026", "confidence": 0.7}}
    text = "Рассмотреть требование кредитора без проведения судебного заседания."
    result = guard_rtk2_results(text, fields)
    assert result["hearing_date"]["value"] is None


def test_explicitly_scheduled_hearing_is_kept() -> None:
    fields = {"hearing_date": {"value": "30.11.2026", "confidence": 0.9}}
    text = (
        "По общему правилу без проведения судебного заседания. "
        "Судебное заседание назначено на 30.11.2026."
    )
    assert guard_rtk2_results(text, fields)["hearing_date"]["value"] == "30.11.2026"


def test_scheduled_review_is_kept_despite_general_no_hearing_rule() -> None:
    fields = {"hearing_date": {"value": "30.11.2026", "confidence": 0.9}}
    text = (
        "Обычно требования рассматриваются без проведения судебного заседания. "
        "Рассмотрение требования назначить на 30.11.2026."
    )
    assert guard_rtk2_results(text, fields)["hearing_date"]["value"] == "30.11.2026"


def test_initials_are_not_full_name() -> None:
    fields = {"financial_manager_full_name": {"value": "Багиева В.А.", "confidence": 0.8}}
    result = guard_rtk2_results("Финансовым управляющим утвержден Багиева В.А.", fields)
    assert result["financial_manager_full_name"]["value"] is None
    assert "Багиева В.А." in result["financial_manager_full_name"]["reasoning"]


def test_full_name_is_kept() -> None:
    fields = {"financial_manager_full_name": {"value": "Закиров Тимур Назифович", "confidence": 0.9}}
    assert guard_rtk2_results("Финансовым управляющим суд утвердил Закирова Тимура Назифовича.", fields)["financial_manager_full_name"]["value"] == "Закиров Тимур Назифович"


def test_rtk2_validation_requires_debtor_but_allows_no_hearing() -> None:
    profile = load_profile("rtk2")
    fields = {
        "case_number": {"value": "А41-15440/2026"},
        "decision_date": {"value": "10.09.2026"},
        "procedure_type": {"value": "реализация имущества"},
        "hearing_date": {"value": None},
        "review_required": {"value": True},
    }
    status, issues = validate_fields(fields, "rtk2", profile)
    assert status == "errors"
    assert [issue.field_name for issue in issues] == ["debtor_full_name"]

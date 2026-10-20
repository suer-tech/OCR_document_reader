"""Report calculation uses the verified next date, including its end fallback."""
import asyncio
from types import SimpleNamespace

import pytest

from ocr_platform.services import court_regex_candidates as registry
from ocr_platform.services import court_regex_jev as routing
from ocr_platform.services import court_selective_llm as selective
from ocr_platform.services.court_field_rules import FIELD_RULES


HEADER = "РЕШЕНИЕ\n01 октября 2026 года\nРЕШИЛ:\n"


@pytest.mark.parametrize("marker", ["заблаговременно", "за 3 дня до заседания", "за пять дней до заседания"])
@pytest.mark.parametrize("end", ["22.03.2027", None])
def test_report_uses_explicit_next_date_even_with_different_or_missing_end(monkeypatch, marker, end):
    monkeypatch.setattr(registry.legacy, "extract_procedure_end_date_with_meta", lambda text: (end, False))
    text = (HEADER + "Назначить судебное заседание на 15.03.2027. "
            + f"Финансовому управляющему {marker} представить в суд отчет.")
    # Request order and omitted public base fields must not change the result.
    result = registry.extract_court_candidates(text, ["early_report_deadline"])
    report = result.fields["early_report_deadline"]
    assert report.value == "05.03.2027"
    assert report.dependencies == ("next_session_date",)
    assert report.metadata["base_value"] == "15.03.2027"
    assert text[report.start:report.end] == report.quote


def test_report_uses_end_only_through_next_date_fallback(monkeypatch):
    monkeypatch.setattr(registry.legacy, "extract_procedure_end_date_with_meta", lambda text: ("22.03.2027", False))
    result = registry.extract_court_candidates(
        HEADER + "Финансовому управляющему заблаговременно представить в суд отчет.",
        ["early_report_deadline", "next_session_date"])
    assert result.fields["early_report_deadline"].value == "12.03.2027"
    assert result.fields["early_report_deadline"].dependencies == ("next_session_date",)
    assert result.fields["next_session_date"].dependencies == ("procedure_end_date",)


def test_no_next_or_end_means_no_report_calculation(monkeypatch):
    monkeypatch.setattr(registry.legacy, "extract_procedure_end_date_with_meta", lambda text: (None, False))
    result = registry.extract_court_candidates(
        HEADER + "Финансовому управляющему заблаговременно представить в суд отчет.", ["early_report_deadline"])
    assert "early_report_deadline" not in result.fields
    assert result.problems["early_report_deadline"] == "missing_next_session_date"


@pytest.mark.parametrize("explicit, rejected, expected_fallback", [
    (True, "procedure_end_date", ["procedure_end_date"]),
    (True, "next_session_date", ["next_session_date", "early_report_deadline"]),
    (False, "procedure_end_date", ["procedure_end_date", "next_session_date", "early_report_deadline"]),
])
def test_real_registry_dependency_rejection_and_llm_reference(monkeypatch, explicit, rejected, expected_fallback):
    monkeypatch.setattr(registry.legacy, "extract_procedure_end_date_with_meta", lambda text: ("22.03.2027", False))
    text = HEADER + ("Назначить судебное заседание на 15.03.2027. " if explicit else "")
    text += "Финансовому управляющему заблаговременно представить в суд отчет."
    calls = {"jev": [], "llm": []}

    def jev(**kwargs):
        calls["jev"].append(kwargs)
        return SimpleNamespace(parsed={"answers": {name: {"type": "noul", "noul": .1 if name == rejected else .99}
                                                   for name in kwargs["decisions_questions"]}},
                               winner_model="typesafe/jev-1.14")

    async def llm(text, fields, config, **kwargs):
        calls["llm"].append((list(fields), kwargs.get("reference_fields")))
        return {name: {"value": None, "confidence": 0, "source": "court_llm_fallback"} for name in fields}

    monkeypatch.setattr(routing, "call_llm_json_with_fallback", jev)
    monkeypatch.setattr(routing, "extract_court_llm_fields", llm)
    config = {"fields": {name: {} for name in ["procedure_end_date", "next_session_date", "early_report_deadline"]},
              "models": {"verification": {"threshold": .95, "question_style": "concrete"}}}
    output = asyncio.run(routing.extract_court_regex_jev(text, config, document_id="test", pipeline_run_id="run"))
    assert len(calls["jev"]) == 1
    assert calls["llm"][0][0] == expected_fallback
    if explicit and rejected == "procedure_end_date":
        assert output["early_report_deadline"]["value"] == "05.03.2027"
        assert calls["llm"][0][1]["next_session_date"]["value"] == "15.03.2027"
    else:
        assert output["early_report_deadline"]["value"] is None


@pytest.mark.parametrize("next_date, report, valid", [
    ("15.03.2027", "05.03.2027", True),
    ("15.03.2027", "12.03.2027", False),
    (None, "05.03.2027", False),
    ("09.03.2027", "27.02.2027", True),  # Saturday is not shifted.
])
def test_final_merged_report_must_match_next_date(next_date, report, valid):
    output = {"procedure_end_date": {"value": None}, "next_session_date": {"value": next_date},
              "early_report_deadline": {"value": report}}
    routing._validate_relationships(output)
    assert output["early_report_deadline"]["value"] == (report if valid else None)


def test_shared_jev_llm_rule_uses_next_date_and_fixed_ten_days():
    rule = FIELD_RULES["early_report_deadline"]
    assert "next_session_date" in rule and "10 календарных" in rule
    assert "за 3 или 5 дней" in rule


def test_llm_retries_wrong_report_calculation_from_verified_next_date(monkeypatch):
    calls = []

    def provider(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(parsed={"early_report_deadline": {
            "value": "12.03.2027" if len(calls) == 1 else "05.03.2027", "confidence": .9,
            "reasoning": "Расчет", "evidence": "source", "ambiguous": False}}, winner_model="test")

    monkeypatch.setattr(selective, "call_llm_json_with_fallback", provider)
    monkeypatch.setattr(selective, "get_field_instruction", lambda *args, **kwargs: kwargs.get("default", ""))
    result = asyncio.run(selective.extract_court_llm_fields(
        "source", {"early_report_deadline": {}}, {"models": {"llm_extraction": {"model": "test"}}},
        reference_fields={"next_session_date": {"value": "15.03.2027", "source": "regex_jev"}}))
    assert len(calls) == 2
    assert result["early_report_deadline"]["value"] == "05.03.2027"
    assert "next_session_date" in calls[0]["system_prompt"]

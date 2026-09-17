import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import yaml
from pydantic import ValidationError

from ocr_platform.services.court_decision_additional import (
    ADDITIONAL_FIELDS, CourtAdditionalResult, additional_fields_result,
)
from ocr_platform.services.validation_service import (
    court_field_issues, review_requirement, validate_fields,
)

TEXT = (
    "Определением от 15.01.2026 заявление принято к производству. ОПРЕДЕЛИЛ: "
    "Ввести процедуру сроком на шесть месяцев. Назначить заседание на 30.10.2026 "
    "в 9 час. 00 мин. по адресу: г. Уфа, ул. Гоголя, д. 18, зал 2."
)


def result_data(**values):
    items = {
        name: dict(value=None, confidence=0.95, reasoning="Не найдено", ambiguous=False, evidence=None)
        for name in ADDITIONAL_FIELDS
    }
    items["procedure_end_date_source"]["value"] = "not_found"
    for name, value in values.items():
        items[name].update(value=value, evidence=TEXT, reasoning="Указано в тексте")
    return CourtAdditionalResult.model_validate(items)


def test_spec_example_types_and_time_normalization():
    data = result_data(application_acceptance_date="15.01.2026", next_session_date="30.10.2026",
                       next_session_time="9 час. 00 мин.", procedure_duration_months=6,
                       procedure_end_date_source="explicit_duration",
                       court_hearing_address="г. Уфа, ул. Гоголя, д. 18, зал 2")
    fields = additional_fields_result(data, TEXT)
    assert fields["next_session_time"]["value"] == "09:00"
    assert type(fields["procedure_duration_months"]["value"]) is int
    assert fields["court_hearing_address"]["value"].endswith("зал 2")
    assert not court_field_issues(fields)


@pytest.mark.parametrize("date", ["31.02.2026", "29.02.2025", "2026-10-30", "30.13.2026"])
def test_invalid_calendar_dates_require_review(date):
    fields = additional_fields_result(result_data(next_session_date=date), TEXT)
    assert fields["next_session_date"]["value"] is None
    assert review_requirement(0.99, court_field_issues(fields))[0] is True


@pytest.mark.parametrize("time,expected", [("09 часов 00 минут", "09:00"), ("9:00", "09:00"),
                                         ("09:00", "09:00"), ("24:00", None), ("12:60", None)])
def test_time_formats(time, expected):
    fields = additional_fields_result(result_data(next_session_date="30.10.2026", next_session_time=time), TEXT)
    assert fields["next_session_time"]["value"] == expected


@pytest.mark.parametrize("value", [True, "6", 6.5])
def test_duration_must_be_integer(value):
    with pytest.raises(ValidationError):
        result_data(procedure_duration_months=value)


def test_absent_fields_do_not_invent_defaults():
    fields = additional_fields_result(result_data(), TEXT)
    assert fields["procedure_end_date_source"]["value"] == "not_found"
    assert all(fields[name]["value"] is None for name in ADDITIONAL_FIELDS[:-1])
    assert court_field_issues(fields) == []


def test_conflicting_session_clears_dependent_fields_and_survives_storage():
    data = result_data(next_session_date="30.10.2026", next_session_time="09:00",
                       court_hearing_address="г. Уфа")
    data.next_session_date.ambiguous = True
    data.next_session_date.reasoning = "Назначены два заседания, выбрать следующее невозможно"
    fields = json.loads(json.dumps(additional_fields_result(data, TEXT)))
    assert all(fields[name]["value"] is None for name in (
        "next_session_date", "next_session_time", "court_hearing_address"))
    status, issues = validate_fields(fields, "court_decision_ru", {"fields": {}})
    assert status == "errors"
    assert len(issues) == 3
    assert review_requirement(1.0, issues) == (True, "court_field_requires_review")


def test_time_and_address_cannot_exist_without_date():
    fields = additional_fields_result(result_data(next_session_time="09:00", court_hearing_address="г. Уфа"), TEXT)
    assert fields["next_session_time"]["value"] is None
    assert fields["court_hearing_address"]["value"] is None
    assert len(court_field_issues(fields)) == 2


def test_unsubstantiated_quote_is_not_accepted():
    data = result_data(application_acceptance_date="15.01.2026")
    data.application_acceptance_date.evidence = "Придуманная цитата из другого документа"
    fields = additional_fields_result(data, TEXT)
    assert fields["application_acceptance_date"]["value"] is None
    assert court_field_issues(fields)


@pytest.mark.parametrize("source,duration", [("explicit_duration", None), ("not_found", 6)])
def test_inconsistent_source_requires_review(source, duration):
    fields = additional_fields_result(result_data(procedure_end_date_source=source, procedure_duration_months=duration), TEXT)
    assert fields["procedure_end_date_source"]["value"] is None
    assert court_field_issues(fields)


def test_explicit_date_can_coexist_with_duration():
    fields = additional_fields_result(result_data(procedure_duration_months=6, procedure_end_date_source="explicit_date"), TEXT)
    assert fields["procedure_end_date_source"]["value"] == "explicit_date"
    assert not court_field_issues(fields)


@pytest.mark.asyncio
@pytest.mark.parametrize("fails", [False, True])
async def test_additional_extraction_preserves_legacy_fields(monkeypatch, fails):
    from ocr_platform.services import extraction_agent as agent
    profile = yaml.safe_load((Path(agent.__file__).parents[1] / "config/pipelines/profiles/court_decision_ru.yaml").read_text(encoding="utf-8"))
    legacy_names = ["case_number", "procedure_end_date", "procedure_end_date_is_calculated", "early_report_deadline", "motivating_part", "resolutive_part"]
    legacy = {name: profile["fields"][name] for name in legacy_names}
    run = AsyncMock(return_value=SimpleNamespace(data=result_data()),
                    side_effect=RuntimeError("Unavailable") if fails else None)
    monkeypatch.setattr(agent.agent_court_additional, "run", run)
    monkeypatch.setattr(agent, "_active_model_settings", lambda: {})
    monkeypatch.setattr(agent, "_get_lf_client", lambda: None)
    monkeypatch.setattr(agent, "get_field_instruction", lambda p, f, default: default)
    text = "15 января 2026 года. Дело № А05-6/2026. УСТАНОВИЛ: Обстоятельства. РЕШИЛ: " + TEXT
    before = await agent._run_agent_extraction_impl(text, legacy, profile_id="court_decision_ru")
    after = await agent._run_agent_extraction_impl(text, {**legacy, **{
        name: profile["fields"][name] for name in ADDITIONAL_FIELDS
    }}, profile_id="court_decision_ru")
    assert {name: after[name] for name in legacy_names} == before
    assert set(after) == set(legacy_names) | set(ADDITIONAL_FIELDS)
    assert run.await_count == (3 if fails else 1)
    assert run.await_args.kwargs["deps"] == text
    if fails:
        assert all(after[name]["value"] is None for name in ADDITIONAL_FIELDS)
        assert len(court_field_issues(after)) == 6
    else:
        assert not court_field_issues(after)


@pytest.mark.parametrize("ambiguous", [False, True])
def test_api_retains_values_and_review_issues(monkeypatch, ambiguous):
    from unittest.mock import MagicMock
    from fastapi.testclient import TestClient
    from ocr_platform.storage import repository
    monkeypatch.setattr(repository, "init_db", lambda: None)
    from ocr_platform.api.main import app

    data = result_data(next_session_date="30.10.2026")
    data.next_session_date.ambiguous = ambiguous
    fields = additional_fields_result(data, TEXT)
    session = MagicMock()
    session.get.return_value = SimpleNamespace(id="doc-test")
    session.query.return_value.filter.return_value.order_by.return_value.first.side_effect = [
        SimpleNamespace(id="run-test"), SimpleNamespace(id=1, text=TEXT),
        SimpleNamespace(id=2, data=json.dumps(fields)),
        SimpleNamespace(technical_score=1.0, semantic_score=1.0, overall_score=1.0),
    ]
    session.__enter__.return_value = session
    monkeypatch.setattr(repository, "get_session", lambda: session)
    response = TestClient(app).get("/documents/doc-test/result")
    assert response.status_code == 200
    payload = response.json()
    assert payload["human_review_required"] is ambiguous
    assert bool(payload["validation_issues"]) is ambiguous
    for name in ADDITIONAL_FIELDS:
        assert payload["fields"][name]["value"] == fields[name]["value"]
        assert "validation_issue" not in payload["fields"][name]

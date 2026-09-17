from types import SimpleNamespace
from unittest.mock import AsyncMock
import json
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from ocr_platform.services import extraction_agent
from ocr_platform.api.schemas import FieldValue
from ocr_platform.services.quality_service import compute_quality_scores


@pytest.mark.asyncio
@pytest.mark.parametrize("combined_fails", [False, True])
async def test_manager_after_10000_characters(monkeypatch, combined_fails):
    # The appointment is on a later page, beyond the old individual prompt limit.
    text = "АРБИТРАЖНЫЙ СУД\n" + "Обстоятельства дела.\n" * 600
    text += (
        "РЕШИЛ:\n3. Утвердить Багиеву Викторию Александровну "
        "(член Ассоциации арбитражных управляющих) финансовым управляющим."
    )
    assert text.index("Багиеву") > 10000
    expected = "Багиева Виктория Александровна"
    fields = {
        "judge_full_name": {
            "extraction_method": "llm",
            "prompt_instruction": "ФИО судьи",
        },
        "financial_manager_full_name": {
            "extraction_method": "llm",
            "prompt_instruction": "ФИО финансового управляющего",
        },
    }
    combined_run = AsyncMock(
        return_value=SimpleNamespace(data=SimpleNamespace(
            judge_full_name="Иванов И.И.",
            judge_full_name_confidence=0.9,
            judge_full_name_reasoning="Found judge",
            financial_manager_full_name=expected,
            financial_manager_full_name_confidence=0.9,
            financial_manager_full_name_reasoning="Found appointment",
            has_text_distortions=False,
        )),
        side_effect=RuntimeError("Combined extraction unavailable") if combined_fails else None,
    )
    generic_run = AsyncMock(return_value=SimpleNamespace(data=SimpleNamespace(
        value=expected, confidence=0.9, reasoning="Found appointment",
    )))
    monkeypatch.setattr(extraction_agent.agent_court_decision_combined, "run", combined_run)
    monkeypatch.setattr(extraction_agent.agent_generic, "run", generic_run)
    monkeypatch.setattr(extraction_agent.asyncio, "sleep", AsyncMock())
    monkeypatch.setattr(extraction_agent, "_active_model_settings", lambda: {})
    monkeypatch.setattr(
        extraction_agent, "get_field_instruction",
        lambda profile_id, field_name, default: default,
    )
    monkeypatch.setattr(extraction_agent, "_get_lf_client", lambda: None)

    result = await extraction_agent._run_agent_extraction_impl(
        text, fields, profile_id="court_decision_ru",
    )

    assert result["financial_manager_full_name"]["value"] == expected
    if combined_fails:
        assert combined_run.await_count == 3
        assert generic_run.await_count == 2
        for call in generic_run.await_args_list:
            assert call.args[0].endswith(text)
            assert call.kwargs["deps"] == text
        assert result["financial_manager_full_name"]["source"] == "llm"
    else:
        combined_run.assert_awaited_once()
        assert "--- FIELD: financial_manager_full_name ---" in combined_run.await_args.args[0]
        assert combined_run.await_args.args[0].endswith(text)
        generic_run.assert_not_awaited()
        assert result["financial_manager_full_name"]["source"] == "court_decision_combined"


@pytest.mark.parametrize("invalid", ["true", "false", 1, 0, None])
def test_early_report_requires_json_boolean(invalid):
    with pytest.raises(ValidationError):
        extraction_agent.EarlyReportRequiredResult(
            value=invalid, confidence=0.9, reasoning="Test",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("required", [True, False])
@pytest.mark.parametrize("heading", ["РЕШИЛ", "ОПРЕДЕЛИЛ"])
async def test_early_report_boolean_without_end_date(monkeypatch, required, heading):
    text = "Обстоятельства дела.\n" * 600 + heading + ":\n"
    text += (
        "Арбитражному управляющему за два месяца до заседания направить отчёт."
        if required else "Назначить рассмотрение отчёта управляющего."
    )
    profile_path = (
        Path(extraction_agent.__file__).parents[1]
        / "config/pipelines/profiles/court_decision_ru.yaml"
    )
    profile = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
    field_names = ["early_report_required", "early_report_deadline"]
    fields = {name: profile["fields"][name] for name in field_names}
    run = AsyncMock(return_value=SimpleNamespace(
        data=extraction_agent.EarlyReportRequiredResult(
            value=required, confidence=0.95, reasoning="По тексту поручения суда",
        ),
    ))
    monkeypatch.setattr(extraction_agent.agent_early_report_required, "run", run)
    monkeypatch.setattr(extraction_agent, "_active_model_settings", lambda: {})
    monkeypatch.setattr(extraction_agent, "_get_lf_client", lambda: None)
    monkeypatch.setattr(extraction_agent, "get_field_instruction", lambda p, f, default: default)
    result = await extraction_agent._run_agent_extraction_impl(
        text, fields, profile_id="court_decision_ru",
    )
    assert result["early_report_required"]["value"] is required
    assert result["early_report_required"]["source"] == "court_decision"
    assert result["early_report_deadline"]["value"] is None
    payload = FieldValue(name="early_report_required", **result["early_report_required"])
    assert json.loads(payload.model_dump_json())["value"] is required
    run.assert_awaited_once()
    assert run.await_args.args[0].endswith(text)
    assert run.await_args.kwargs["deps"] == text


@pytest.mark.asyncio
async def test_new_report_flag_preserves_legacy_values(monkeypatch):
    text = (
        "15 января 2026 года. Дело № А05-6/2026. РЕШИЛ: "
        "Ввести процедуру сроком на шесть месяцев. "
        "Финансовому управляющему заблаговременно представить отчёт."
    )
    legacy_fields = {
        name: {"extraction_method": "regex_legacy"}
        for name in ["case_number", "procedure_end_date",
                     "procedure_end_date_is_calculated", "early_report_deadline"]
    }
    run = AsyncMock(return_value=SimpleNamespace(
        data=extraction_agent.EarlyReportRequiredResult(
            value=True, confidence=0.95, reasoning="Есть поручение",
        ),
    ))
    monkeypatch.setattr(extraction_agent.agent_early_report_required, "run", run)
    monkeypatch.setattr(extraction_agent, "_active_model_settings", lambda: {})
    monkeypatch.setattr(extraction_agent, "_get_lf_client", lambda: None)
    monkeypatch.setattr(extraction_agent, "get_field_instruction", lambda p, f, default: default)
    before = await extraction_agent._run_agent_extraction_impl(
        text, legacy_fields, profile_id="court_decision_ru",
    )
    after = await extraction_agent._run_agent_extraction_impl(
        text, {**legacy_fields, "early_report_required": {"extraction_method": "llm"}},
        profile_id="court_decision_ru",
    )
    assert after.pop("early_report_required")["value"] is True
    assert after == before
    assert after["early_report_deadline"]["value"] == "05.07.2026"


def test_false_report_flag_is_a_completed_field():
    true_score = compute_quality_scores("Text", {"early_report_required": {"value": True}})
    false_score = compute_quality_scores("Text", {"early_report_required": {"value": False}})
    missing_score = compute_quality_scores("Text", {"early_report_required": {"value": None}})
    assert false_score == true_score
    assert missing_score[1] < false_score[1]

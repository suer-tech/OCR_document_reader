from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from ocr_platform.services import extraction_agent


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

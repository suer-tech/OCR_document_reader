from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from ocr_platform.orchestration.router import load_profile, resolve_profile
from ocr_platform.services import extraction_agent
from ocr_platform.services.validation_service import validate_fields


@pytest.mark.parametrize("profile_id", ["rtk2", "rtk3"])
def test_rtk_profile_is_routed_and_has_active_pipeline(profile_id: str) -> None:
    resolution = resolve_profile(
        source_type="external", requested_document_type=profile_id
    )
    profile = load_profile(profile_id)

    assert resolution.profile_id == profile_id
    assert profile["fields"]
    assert profile["pipeline"]
    assert profile["models"]["llm_extraction"]["model"]


@pytest.mark.asyncio
async def test_rtk3_combined_extraction_preserves_claims_and_false_pledge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = load_profile("rtk3")
    data = extraction_agent.Rtk3Result(
        inclusion_date="10.09.2026",
        inclusion_date_confidence=0.9,
        inclusion_date_reasoning="Шапка определения",
        creditor="ООО Кредитор",
        creditor_confidence=0.9,
        creditor_reasoning="Резолютивная часть",
        total_claimed_amount=100.0,
        total_claimed_amount_confidence=0.9,
        total_claimed_amount_reasoning="Сумма требований",
        claims=[
            extraction_agent.Rtk3Claim(
                priority_queue="3 очередь",
                principal_debt=80.0,
                financial_sanctions=20.0,
                total_amount=100.0,
            )
        ],
        claims_confidence=0.9,
        claims_reasoning="Распределение по очередям",
        grounds="Договор займа",
        grounds_confidence=0.9,
        grounds_reasoning="Мотивировочная часть",
        secured_by_pledge=False,
        secured_by_pledge_confidence=0.9,
        secured_by_pledge_reasoning="Залог не указан",
        has_text_distortions=False,
    )
    mock_run = AsyncMock(return_value=SimpleNamespace(data=data))
    monkeypatch.setattr(extraction_agent.agent_rtk3_combined, "run", mock_run)
    monkeypatch.setattr(extraction_agent, "_get_lf_client", lambda: None)
    monkeypatch.setattr(
        extraction_agent,
        "search_creditor_inn",
        lambda *_args: pytest.fail("rtk3 must not search for an unrequested INN"),
    )

    fields = await extraction_agent._run_agent_extraction_impl(
        "Определение о включении требований",
        profile["fields"],
        profile_id="rtk3",
        profile_config=profile,
    )

    assert mock_run.await_count == 1
    assert fields["claims"]["value"] == [
        {
            "priority_queue": "3 очередь",
            "principal_debt": 80.0,
            "financial_sanctions": 20.0,
            "total_amount": 100.0,
        }
    ]
    assert fields["secured_by_pledge"]["value"] is False
    assert "creditor_inn" not in fields
    assert validate_fields(fields, "rtk3", profile)[0] == "ok"

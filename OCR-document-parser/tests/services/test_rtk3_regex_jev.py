import json
from types import SimpleNamespace

import httpx
import pytest

from ocr_platform.orchestration.router import load_profile
from ocr_platform.services import rtk3_regex_jev as service


ACT = """Дело № А40-123/2026-1-2Ф
Полный текст определения изготовлен 19 марта 2026 года.
ОПРЕДЕЛИЛ:
Включить в третью очередь реестра требований кредиторов гражданина
Петрова Ивана Ивановича (ИНН 012345678901) требование ПАО «Пример» (ИНН 0123456789)
по кредитному договору № 1406/ПК от 21.11.2025 в размере 108 руб.,
в том числе 100 руб. основного долга и 8 руб. пеней."""
ORDER = ACT.split("ОПРЕДЕЛИЛ:\n", 1)[1]
CREDITOR = {"value": "ПАО «Пример»", "quote": "требование ПАО «Пример»"}
CREDITOR_INN = {"value": "0123456789", "quote": "требование ПАО «Пример» (ИНН 0123456789)"}
PENALTY = {"kind": "sanction", "amount": "8 руб.", "quote": "8 руб. пеней"}
MODEL_CLAIM = {
    "priority_queue": {"value": "3 очередь", "quote": "третью очередь"},
    "decision_quote": ORDER,
    "breakdown_quote": "",
    "components": [
        {"kind": "principal", "amount": "100 руб.", "quote": "100 руб. основного долга"},
        PENALTY,
    ],
    "components_complete": True,
    "included_total": {"value": "108 руб.", "quote": "108 руб."},
    "total_scope": "without_duty",
    "pledge_resolution": "unknown",
    "pledge_quote": "",
    "is_salary_or_severance": False,
    "salary_quote": "по кредитному договору № 1406/ПК от 21.11.2025",
    "uncertainty": "",
}


@pytest.mark.asyncio
@pytest.mark.parametrize("jev_mode", ["confirm_layout", "reject_layout", "timeout"])
async def test_reasoning_gateway_routes_and_merges_rtk3_fields(monkeypatch, jev_mode):
    profile = load_profile("rtk3")
    calls = {}

    def gateway(**request):
        kind = request["request_type"]
        calls[kind] = request
        if kind == "decisions":
            if jev_mode == "timeout":
                raise httpx.ReadTimeout("JEV unavailable")
            threshold = profile["models"]["verification"]["threshold"]
            probabilities = {
                "claim_layout": threshold if jev_mode == "confirm_layout" else threshold - 0.01,
                "claim_1_financial_sanctions": threshold - 0.01,
                "creditor": threshold - 0.01,
            }
            return SimpleNamespace(parsed={"answers": {
                name: {"type": "noul", "noul": probabilities.get(name, 0.99)}
                for name in request["decisions_questions"]
            }})

        parsed = {"creditor": CREDITOR, "creditor_inn": CREDITOR_INN}
        if jev_mode == "confirm_layout":
            parsed.update(claim_1_financial_sanctions={"items": [PENALTY], "complete": True, "quote": ORDER},
                          claim_1_pledge_resolution=None)
        else:
            parsed.update(claims=[MODEL_CLAIM], claims_complete=True, uncertainty="")
        if jev_mode == "timeout":
            parsed.update(
                case_number={"value": "А40-123/2026", "quote": "А40-123/2026-1-2Ф"},
                document_case_number={"value": "А40-123/2026-1-2Ф", "quote": "А40-123/2026-1-2Ф"},
                debtor_inn={"value": "012345678901", "quote": "гражданина Петрова Ивана Ивановича (ИНН 012345678901)"},
                inclusion_date={"value": "19.03.2026", "quote": "Полный текст определения изготовлен 19 марта 2026 года."},
                grounds={"value": "Кредитный договор", "quote": "по кредитному договору № 1406/ПК от 21.11.2025"},
                grounds_ocr={"value": "по кредитному договору № 1406/ПК от 21.11.2025",
                             "quote": "по кредитному договору № 1406/ПК от 21.11.2025"},
            )
        return SimpleNamespace(parsed=parsed)

    monkeypatch.setattr(service, "call_llm_json_with_fallback", gateway)
    result = await service.extract_fields(ACT, profile)

    assert list(calls) == ["decisions", "rtk3_components"]
    decisions = calls["decisions"]
    assert set(service.SCALAR_QUESTIONS) <= decisions["decisions_questions"].keys()
    assert {"claim_layout", "claim_1_financial_sanctions", "claim_1_principal_debt"} <= decisions["decisions_questions"].keys()
    fallback = calls["rtk3_components"]
    requested = fallback["response_schema"]["json_schema"]["schema"]["properties"].keys()
    inputs = json.loads(fallback["user_content"])
    if jev_mode == "confirm_layout":
        assert requested == {"creditor", "creditor_inn", "claim_1_financial_sanctions", "claim_1_pledge_resolution"}
        assert len(inputs["verified_claims"]) == 1
        assert "claim_1_principal_debt" in inputs["verified_fields"]
    else:
        assert {"claims", "claims_complete", "uncertainty", "creditor", "creditor_inn"} <= requested
        assert inputs["verified_claims"] == []
        assert not any(name.startswith("claim_") for name in inputs["verified_fields"])
    assert ("case_number" in inputs["verified_fields"]) is (jev_mode != "timeout")
    assert "creditor_inn" not in inputs["verified_fields"]
    if jev_mode == "timeout":
        assert "слова, связывающие его с ролью должника" in fallback["system_prompt"]

    assert {name: result[name]["value"] for name in service.SCALAR_QUESTIONS} == {
        "case_number": "А40-123/2026",
        "document_case_number": "А40-123/2026-1-2Ф",
        "debtor_inn": "012345678901",
        "creditor": "ПАО «Пример»",
        "creditor_inn": "0123456789",
        "inclusion_date": "19.03.2026",
        "grounds": "Кредитный договор",
        "grounds_ocr": "по кредитному договору № 1406/ПК от 21.11.2025",
    }
    assert {name: result[name]["value"] for name in ("kad_publication_date", "pledge_resolution", "secured_by_pledge",
                                                        "total_claimed_amount")} == {
        "kad_publication_date": None, "pledge_resolution": None, "secured_by_pledge": None,
        "total_claimed_amount": "108.00",
    }
    assert result["claims"]["value"] == [{
        "priority_queue": "3 очередь", "contract_number": "1406/ПК", "principal_debt": "100.00",
        "financial_sanctions": "8.00", "pau_only_amount": "0.00", "claimed_amount": "108.00",
        "total_amount": "108.00", "is_salary_or_severance": False,
        "pledge_resolution": None, "secured_by_pledge": None,
    }]

import json
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

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


@pytest.mark.asyncio
@pytest.mark.parametrize("layout_verified", [True, False], ids=["fixed-layout", "full-fallback"])
@pytest.mark.parametrize("partial", [False, True], ids=["empty-argon", "partial-argon"])
async def test_argon_incomplete_reply_keeps_rtk3_evidence_and_requests_review(
    monkeypatch, tmp_path, layout_verified, partial,
):
    from ocr_platform.orchestration import run_processor as worker
    from ocr_platform.storage import models, repository

    act = ACT.replace("в размере 108 руб.", "как обеспеченное залогом имущества в размере 108 руб.")
    order = act.split("ОПРЕДЕЛИЛ:\n", 1)[1]
    unconfirmed = {"document_case_number", "inclusion_date", "grounds_ocr", "claim_1_financial_sanctions"}
    calls = []

    def gateway(**request):
        calls.append(request)
        if request["request_type"] == "decisions":
            answers = {
                name: {"type": "noul", "noul": 0.1 if name in unconfirmed else 0.99}
                for name in request["decisions_questions"]
            }
            answers["claim_layout"]["noul"] = 0.99 if layout_verified else 0.1
            return SimpleNamespace(parsed={"answers": answers})
        if not partial:
            return SimpleNamespace(parsed=None)
        facts = {
            "inclusion_date": {"value": "19.03.2026", "quote": "Полный текст определения изготовлен 19 марта 2026 года."},
            "grounds_ocr": "Ответ без объекта Fact",
        }
        if layout_verified:
            facts["claim_1_financial_sanctions"] = {"items": [PENALTY], "complete": True, "quote": order}
        else:
            facts["claims"] = [{
                **MODEL_CLAIM, "decision_quote": order, "pledge_resolution": "yes",
                "pledge_quote": "как обеспеченное залогом имущества",
            }]
        return SimpleNamespace(parsed=facts)

    monkeypatch.setattr(service, "call_llm_json_with_fallback", gateway)
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    models.Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(repository, "get_session", sessions)
    monkeypatch.setattr(repository, "init_db", lambda: None)
    monkeypatch.setattr(worker, "get_pipeline_tracer", MagicMock())
    monkeypatch.setattr(worker, "mlflow_run", lambda *args, **kwargs: nullcontext())
    for name in ("mlflow_set_tag", "mlflow_log_metric", "mlflow_log_param", "mlflow_log_text"):
        monkeypatch.setattr(worker, name, MagicMock())
    webhook = AsyncMock()
    monkeypatch.setattr(worker, "_trigger_webhook_safely", webhook)
    path = tmp_path / "ruling.txt"
    path.write_text(act, encoding="utf-8")
    try:
        with sessions() as session:
            session.add(models.Document(id="doc-rtk3", source_type="external", document_type="rtk3"))
            session.add(models.DocumentFile(document_id="doc-rtk3", file_type="text", storage_path=str(path)))
            session.add(models.PipelineRun(id="run-rtk3", document_id="doc-rtk3", profile_id="rtk3", status="queued",
                                           webhook_url="https://callback.invalid"))
            session.commit()

        await worker._process_pipeline_run_impl("run-rtk3")
        assert [call["request_type"] for call in calls] == ["decisions", "rtk3_components"]
        request = calls[1]["response_schema"]["json_schema"]["schema"]["properties"]
        assert {"document_case_number", "inclusion_date", "grounds_ocr"} <= request.keys()
        assert ("claim_1_financial_sanctions" in request) is layout_verified
        assert ("claims" in request) is not layout_verified
        assert "case_number" not in request

        confirmed = {
            "case_number": "А40-123/2026", "debtor_inn": "012345678901",
            "creditor": "ПАО «Пример»", "creditor_inn": "0123456789", "grounds": "Кредитный договор",
        }
        with sessions() as session:
            assert session.get(models.PipelineRun, "run-rtk3").status == "done"
            stored = session.scalar(select(models.StructuredVersion)).data
            for name, value in confirmed.items():
                assert stored[name]["value"] == value
                assert stored[name]["extraction_route"] == "regex_jev"
            assert stored["inclusion_date"]["value"] == ("19.03.2026" if partial else None)
            assert stored["document_case_number"]["value"] is None
            assert stored["grounds_ocr"]["value"] is None
            assert stored["secured_by_pledge"]["value"] is (True if layout_verified else None)
            assert stored["total_claimed_amount"]["value"] == ("108.00" if partial and layout_verified else None)
            rows = stored["claims"]["value"]
            if layout_verified or partial:
                row, = rows
                assert (row["priority_queue"], row["contract_number"], row["principal_debt"],
                        row["pledge_resolution"], row["secured_by_pledge"]) == (
                            "3 очередь", "1406/ПК", "100.00", "yes", True,
                        )
                assert row["is_salary_or_severance"] is False
                assert row["pau_only_amount"] == "0.00"
                assert row["financial_sanctions"] == ("8.00" if partial else None)
                assert row["claimed_amount"] == ("108.00" if partial else None)
                assert row["total_amount"] == ("108.00" if partial else None)
            else:
                assert rows == []

        from ocr_platform.api.main import create_app

        with TestClient(create_app()) as client:
            reply = client.get("/documents/doc-rtk3/result")
        assert reply.status_code == 200
        webhook.assert_awaited_once()
        sent = webhook.await_args.args[1]
        assert sent["status"] == "done"
        for payload in (sent, reply.json()):
            assert payload["validation_status"] == "errors"
            assert payload["human_review_required"] is True
            assert payload["human_review_reason"] == "rtk3_requires_review"
            issues = {issue["field_name"] for issue in payload["validation_issues"]}
            assert "document_case_number" in issues
            assert ("inclusion_date" in issues) is not partial
            for name in (*confirmed, "claims", "document_case_number", "inclusion_date",
                         "total_claimed_amount", "secured_by_pledge"):
                assert payload["fields"][name]["value"] == stored[name]["value"]
    finally:
        engine.dispose()

import copy
import json
from contextlib import nullcontext
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from ocr_platform.orchestration.router import load_profile
from ocr_platform.services import extraction_agent as agent
from ocr_platform.services.rtk3_claims import claimed_amount_issues
from ocr_platform.services.validation_service import profile_field_issues, review_requirement


def claim(included, claimed=None, queue="3 очередь"):
    return dict(priority_queue=queue, claimed_amount=claimed, principal_debt=included,
                financial_sanctions=0.0, total_amount=included)


def fields(total, claims):
    return {
        name: dict(value=value, confidence=0.95, reasoning="Указано в документе", source="rtk3_combined")
        for name, value in dict(inclusion_date="10.09.2026", creditor="ООО Кредитор",
                               total_claimed_amount=total, claims=claims,
                               grounds="Договор займа", secured_by_pledge=False).items()
    }


@pytest.mark.parametrize("value,expected", [
    (None, None), ("75000.00", "75000.00"), (75000, "75000.00"),
    (38010.25, "38010.25"), (Decimal("1000000"), "1000000.00"), (0, "0.00"),
])
def test_claimed_amount_is_optional_decimal_string_and_preserves_existing_fields(value, expected):
    original = claim(85000.0, value)
    original.update(principal_debt=80000.0, financial_sanctions=5000.0)
    parsed = agent.Rtk3Claim.model_validate(original).model_dump(mode="json")
    assert parsed == {**original, "claimed_amount": expected}
    original.pop("claimed_amount")
    assert agent.Rtk3Claim.model_validate(original).claimed_amount is None


@pytest.mark.parametrize("value", ["NaN", "Infinity", -1, True, "12.345", "12,34", "1 000 руб."])
def test_invalid_claimed_amount_is_rejected_without_rounding(value):
    with pytest.raises(ValidationError):
        agent.Rtk3Claim.model_validate(claim(10.0, value))


# Includes the agreed exceptions, partial extraction and exact decimal addition.
REVIEW_CASES = [
    (38010.25, [claim(38010.25, "38010.25")], None),
    (1000000.0, [claim(850000.0, "1000000.00")], None),
    (1000000.0, [claim(850000.0)], None),
    (1000000.0, [claim(50000.0, "50000.00", "2 очередь"), claim(800000.0, "950000.00")], None),
    (1000000.0, [claim(50000.0), claim(800000.0)], "claimed_amount_by_queue_not_determined"),
    (1000000.0, [claim(50000.0, "50000.00"), claim(800000.0)], "claimed_amount_by_queue_not_determined"),
    (850000.0, [claim(50000.0), claim(800000.0)], None),
    (1000000.0, [claim(50000.0, "50000.00"), claim(800000.0, "900000.00")], "claimed_amount_total_mismatch"),
    (0.3, [claim(0.1, "0.10"), claim(0.2, "0.20")], None),
    (0.3, [claim(0.1), claim(0.2)], None),
    (None, [claim(50000.0), claim(800000.0)], "claimed_amount_by_queue_not_determined"),
    (1000000.0, [claim(None), claim(800000.0)], "claimed_amount_by_queue_not_determined"),
]


@pytest.mark.parametrize("total,claims,reason", REVIEW_CASES)
def test_review_rules_never_recalculate_values(total, claims, reason):
    data = fields(total, copy.deepcopy(claims))
    original = copy.deepcopy(data)
    issues = claimed_amount_issues(data)
    assert review_requirement(0.99, issues) == (reason is not None, reason)
    assert [issue.code for issue in issues] == ([reason] if reason else [])
    assert data == original
    assert profile_field_issues(data, "rtk2") == []
    assert profile_field_issues(data, "rtk") == []


def test_missing_optional_amount_does_not_suppress_other_review_reasons():
    issues = claimed_amount_issues(fields(1000000.0, [claim(850000.0)]))
    assert review_requirement(0.5, issues) == (True, "low_quality_or_missing_fields")


def test_published_schema_matches_optional_string_contract():
    schema = json.loads((Path(agent.__file__).parents[1] /
                         "config/pipelines/schemas/rtk3/Rtk3Result.json").read_text(encoding="utf-8"))
    assert schema == agent.Rtk3Result.model_json_schema()
    row = schema["$defs"]["Rtk3Claim"]
    assert "claimed_amount" not in row["required"]
    assert row["properties"]["claimed_amount"]["anyOf"] == [
        {"pattern": r"^[0-9]+\.[0-9]{2}$", "type": "string"}, {"type": "null"},
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("combined_fails", [False, True])
async def test_extraction_keeps_claimed_amount_with_cached_prompts_and_fallback(monkeypatch, combined_fails):
    rows = [claim(68962.0, "75000.00", "2 очередь"),
            dict(priority_queue="3 очередь", claimed_amount="45000.00", principal_debt=28914.0,
                 financial_sanctions=14835.0, total_amount=43749.0)]
    result_values = {}
    for name, info in fields(120000.0, rows).items():
        result_values[name] = info["value"]
        result_values[name + "_confidence"] = info["confidence"]
        result_values[name + "_reasoning"] = info["reasoning"]
    combined = AsyncMock(return_value=SimpleNamespace(data=agent.Rtk3Result(
        **result_values, has_text_distortions=False)),
        side_effect=RuntimeError("unavailable") if combined_fails else None)
    individual = AsyncMock(return_value=SimpleNamespace(data=agent.Rtk3ClaimsResult(
        value=rows, confidence=0.95, reasoning="Указано в документе")))
    monkeypatch.setattr(agent.agent_rtk3_combined, "run", combined)
    monkeypatch.setattr(agent.agent_rtk3_claims, "run", individual)
    monkeypatch.setattr(agent, "get_field_instruction", lambda *a, **kw: "Old Langfuse prompt")
    monkeypatch.setattr(agent, "_get_lf_client", lambda: None)
    monkeypatch.setattr(agent.asyncio, "sleep", AsyncMock())
    profile = load_profile("rtk3")
    text = "Определение. " * 1000 + "Первоначально заявлено 120000 рублей."
    output = await agent._run_agent_extraction_impl(
        text, {"claims": profile["fields"]["claims"]}, profile_id="rtk3", profile_config=profile)
    assert output["claims"]["value"] == rows
    assert combined.await_count == (3 if combined_fails else 1)
    assert individual.await_count == (1 if combined_fails else 0)
    call = individual.await_args if combined_fails else combined.await_args
    assert text in call.args[0]
    assert "Old Langfuse prompt" in call.args[0]
    assert profile["fields"]["claims"]["claimed_amount_instruction"] in call.args[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("total,claims,reason", [REVIEW_CASES[i] for i in (2, 3, 4, 6, 7)])
async def test_pipeline_storage_webhook_and_http_agree(monkeypatch, tmp_path, total, claims, reason):
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from ocr_platform.storage import models, repository
    from ocr_platform.orchestration import run_processor as processor

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    models.Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(repository, "get_session", sessions)
    monkeypatch.setattr(repository, "init_db", lambda: None)
    monkeypatch.setattr(processor, "get_pipeline_tracer", MagicMock())
    monkeypatch.setattr(processor, "mlflow_run", lambda *a, **kw: nullcontext())
    for name in ("mlflow_set_tag", "mlflow_log_metric", "mlflow_log_param", "mlflow_log_text"):
        monkeypatch.setattr(processor, name, MagicMock())
    original = fields(total, copy.deepcopy(claims))
    monkeypatch.setattr(processor.document_intel_service, "simple_extract_fields",
                        AsyncMock(return_value=copy.deepcopy(original)))
    webhook = AsyncMock()
    monkeypatch.setattr(processor, "_trigger_webhook_safely", webhook)
    document = tmp_path / "ruling.txt"
    document.write_text("Определение о включении требований кредитора", encoding="utf-8")
    with sessions() as session:
        session.add(models.Document(id="doc", source_type="external", document_type="rtk3"))
        session.add(models.DocumentFile(document_id="doc", storage_path=str(document), file_type="text"))
        session.add(models.PipelineRun(id="run", document_id="doc", profile_id="rtk3", status="queued",
                                       webhook_url="https://example.invalid/webhook"))
        session.commit()
    try:
        await processor._process_pipeline_run_impl("run")
        webhook.assert_awaited_once()
        webhook_result = webhook.await_args.args[1]
        from ocr_platform.api.main import app
        with TestClient(app) as client:
            response = client.get("/documents/doc/result")
        assert response.status_code == 200
        http_result = response.json()
        for payload in (webhook_result, http_result):
            assert payload["human_review_required"] is (reason is not None)
            assert payload["human_review_reason"] == reason
            assert payload["validation_status"] == ("errors" if reason else "ok")
            assert [issue["code"] for issue in payload["validation_issues"]] == ([reason] if reason else [])
            for name, info in original.items():
                assert payload["fields"][name]["value"] == info["value"]
        with sessions() as session:
            stored = session.query(models.StructuredVersion).one().data
            assert {name: stored[name] for name in original} == original
            assert session.get(models.PipelineRun, "run").status == "done"
    finally:
        engine.dispose()

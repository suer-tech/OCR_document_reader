"""New court provenance survives storage, HTTP and webhook; no network calls."""
from contextlib import nullcontext
import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from ocr_platform.services.court_field_provenance import finalize_court_fields


def test_court_swagger_example_uses_current_provenance_contract():
    from ocr_platform.api.schemas import DocumentResultResponse

    fields = DocumentResultResponse.model_config['json_schema_extra']['examples'][0]['fields']
    for name, item in fields.items():
        if name == 'processing_started_at':
            continue
        assert item['source'] in {'header', 'motivating_part', 'resolutive_part', 'signature_block', 'document', None}
        assert item['extraction_method'] in {'regex_jev', 'court_llm_fallback'}
        assert item['reasoning']
    assert 'Утвердить финансовым управляющим Сидорова С.С.' in fields['resolutive_part']['value']
    assert 'Утвердить финансовым управляющим Сидорова С.С.' in DocumentResultResponse.model_config['json_schema_extra']['examples'][0]['raw_text']


@pytest.mark.asyncio
@pytest.mark.parametrize("issue", [False, True])
async def test_pipeline_storage_http_and_webhook_keep_new_field_and_review(monkeypatch, tmp_path, issue):
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
    text = "РЕШЕНИЕ\nДело № А40-1/2026\nУСТАНОВИЛ:\nФакты.\nРЕШИЛ:\nВвести процедуру реализации имущества."
    raw = {"case_number": {"value": "А40-1/2026", "confidence": .99, "source": "regex_jev",
                           "evidence": "Дело № А40-1/2026", "reasoning": "Выбран номер текущего дела."}}
    if issue:
        raw["case_number"].update(value=None, validation_issue="Номер дела неоднозначен", source="court_llm_fallback")
    original = finalize_court_fields(text, raw)
    monkeypatch.setattr(processor.document_intel_service, "simple_extract_fields", AsyncMock(return_value=copy.deepcopy(original)))
    webhook = AsyncMock()
    monkeypatch.setattr(processor, "_trigger_webhook_safely", webhook)
    document = tmp_path / "decision.txt"
    document.write_text(text, encoding="utf-8")
    with sessions() as session:
        session.add(models.Document(id="doc", source_type="external", document_type="court_decision"))
        session.add(models.DocumentFile(document_id="doc", storage_path=str(document), file_type="text"))
        session.add(models.PipelineRun(id="run", document_id="doc", profile_id="court_decision_ru", status="queued",
                                       webhook_url="https://example.invalid/webhook"))
        session.commit()
    try:
        await processor._process_pipeline_run_impl("run")
        webhook.assert_awaited_once()
        from ocr_platform.api.main import app
        with TestClient(app) as client:
            response = client.get("/documents/doc/result")
        assert response.status_code == 200
        for payload in (webhook.await_args.args[1], response.json()):
            item = payload["fields"]["case_number"]
            assert item["extraction_method"] == original["case_number"]["extraction_method"]
            assert item["source"] == (None if issue else "header")
            assert item["reasoning"] == original["case_number"]["reasoning"]
            assert item["value"] == original["case_number"]["value"]
            if issue:
                assert payload["human_review_required"] is True
                assert any(row["field_name"] == "case_number" and row["code"] == "court_field_requires_review"
                           for row in payload["validation_issues"])
        with sessions() as session:
            assert session.query(models.StructuredVersion).one().data["case_number"] == original["case_number"]
    finally:
        engine.dispose()


def test_old_stored_result_is_returned_without_provenance_migration(monkeypatch):
    from ocr_platform.storage import repository
    monkeypatch.setattr(repository, "init_db", lambda: None)
    from ocr_platform.api.main import app
    historical = {"case_number": {"value": "А40-1/2026", "confidence": .99, "source": "regex_jev"}}
    session = MagicMock()
    session.get.return_value = SimpleNamespace(id="doc")
    session.query.return_value.filter.return_value.order_by.return_value.first.side_effect = [
        SimpleNamespace(id="run", profile_id="court_decision_ru"), SimpleNamespace(id=1, text="old text"),
        SimpleNamespace(id=2, data=json.dumps(historical)),
        SimpleNamespace(technical_score=1.0, semantic_score=1.0, overall_score=1.0),
    ]
    session.__enter__.return_value = session
    monkeypatch.setattr(repository, "get_session", lambda: session)
    item = TestClient(app).get("/documents/doc/result").json()["fields"]["case_number"]
    assert item["source"] == "regex_jev"
    assert item["extraction_method"] is None

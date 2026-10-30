"""One passport completeness contract for the worker, stored results and review."""
import copy
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from ocr_platform.orchestration.router import load_profile
from ocr_platform.services.validation_service import (
    profile_field_issues,
    review_requirement,
    validate_fields,
)
from ocr_platform.storage import models, repository


def complete(profile_id):
    values = {
        "passport_series": "0306", "passport_number": "001234", "last_name": "Тестов",
        "first_name": "Иван", "patronymic": None, "gender": "Мужской", "birth_date": "01.02.1980",
        "birth_place": "Г. ТЕСТ", "issue_date": "01.02.2020", "department_code": "001-002",
        "issued_by": "ТЕСТОВЫМ ОТДЕЛОМ", "registration_address": "Г. ТЕСТ, УЛ. ТЕСТОВАЯ, Д. 1",
        "post_index": None, "region": None, "city": None, "street": None,
    }
    return {name: {"value": values[name]} for name in load_profile(profile_id)["fields"]}


@pytest.mark.parametrize("profile_id", ["passport_main", "passport_registration"])
def test_required_fields_come_from_profile_and_optional_fields_may_be_empty(profile_id):
    fields = complete(profile_id)
    assert validate_fields(fields, profile_id, load_profile(profile_id)) == ("ok", [])
    assert profile_field_issues(fields, profile_id) == []
    assert review_requirement(1.0, profile_field_issues(fields, profile_id)) == (False, None)


@pytest.mark.parametrize("profile_id", ["passport_main", "passport_registration"])
@pytest.mark.parametrize("missing_kind", ["absent", "null", "empty", "whitespace", "empty_list", "empty_dict", "scalar_null"])
def test_missing_required_field_forces_review_even_with_high_quality(profile_id, missing_kind):
    fields = complete(profile_id)
    name = "issue_date" if profile_id == "passport_main" else "registration_address"
    if missing_kind == "absent":
        del fields[name]
    elif missing_kind == "scalar_null":
        fields[name] = None
    else:
        fields[name] = {"value": {"null": None, "empty": "", "whitespace": " \n\t ",
                                 "empty_list": [], "empty_dict": {}}[missing_kind]}
    before = copy.deepcopy(fields)
    status, issues = validate_fields(fields, profile_id, load_profile(profile_id))
    assert status == "errors"
    assert [i.field_name for i in issues] == [name]
    assert [i.model_dump() for i in issues] == [i.model_dump() for i in profile_field_issues(fields, profile_id)]
    assert review_requirement(1.0, issues) == (True, "passport_requires_review")
    assert fields == before


@pytest.mark.parametrize("profile_id,expected", [("passport_main", 10), ("passport_registration", 1)])
def test_service_timestamp_cannot_substitute_for_passport_fields(profile_id, expected):
    fields = {"processing_started_at": "2026-10-30T00:00:00"}
    status, issues = validate_fields(fields, profile_id, load_profile(profile_id))
    assert status == "errors" and len(issues) == expected
    assert review_requirement(1.0, issues) == (True, "passport_requires_review")


@pytest.mark.parametrize("profile_id", ["passport_main", "passport_registration"])
@pytest.mark.parametrize("representation", ["field_objects", "scalar_values", "json_string"])
@pytest.mark.parametrize("state", ["complete", "partial", "timestamp_only", "empty"])
def test_get_result_revalidates_historical_data_without_rewriting_it(monkeypatch, profile_id, representation, state):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    models.Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(repository, "get_session", sessions)
    monkeypatch.setattr(repository, "init_db", lambda: None)
    fields = complete(profile_id)
    if state == "partial":
        fields["issued_by" if profile_id == "passport_main" else "registration_address"] = {"value": None}
    elif state in {"timestamp_only", "empty"}:
        fields = {}
    if state != "empty":
        fields["processing_started_at"] = "2026-10-30T00:00:00"
    if representation == "scalar_values":
        fields = {k: v.get("value") if isinstance(v, dict) else v for k, v in fields.items()}
    stored = json.dumps(fields, ensure_ascii=False) if representation == "json_string" else fields
    original = copy.deepcopy(stored)
    with sessions() as session:
        session.add(models.Document(id="doc", source_type="external", document_type=profile_id))
        session.add(models.PipelineRun(id="run", document_id="doc", profile_id=profile_id, status="done"))
        session.add(models.TextVersion(id=1, document_id="doc", pipeline_run_id="run", text="Сохранённый текст"))
        session.add(models.StructuredVersion(id=2, document_id="doc", pipeline_run_id="run", data=stored))
        session.add(models.QualityScore(pipeline_run_id="run", technical_score=1, semantic_score=1, overall_score=1))
        session.commit()
    try:
        from ocr_platform.api.main import app

        response = TestClient(app).get("/documents/doc/result")
        assert response.status_code == 200
        payload = response.json()
        worker_status, worker_issues = validate_fields(fields, profile_id, load_profile(profile_id))
        assert payload["validation_status"] == worker_status
        assert payload["validation_issues"] == [i.model_dump() for i in worker_issues]
        assert payload["human_review_required"] is (state != "complete")
        assert payload["structured_version_id"] == "2" and payload["raw_text_version_id"] == "1"
        assert payload["fields"] == {
            k: {"name": k, "value": v.get("value") if isinstance(v, dict) else v,
                "reasoning": None, "confidence": None, "source": None, "extraction_method": None}
            for k, v in fields.items()
        }
        with sessions() as session:
            assert session.get(models.StructuredVersion, 2).data == original
            assert session.query(models.StructuredVersion).count() == 1
    finally:
        engine.dispose()

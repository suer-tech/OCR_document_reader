import re

import pytest

from ocr_platform.api.schemas import DocumentResultResponse
from ocr_platform.orchestration.router import load_all_profiles, load_profile


@pytest.fixture
def openapi(monkeypatch):
    from ocr_platform.storage import repository
    monkeypatch.setattr(repository, "init_db", lambda: None)
    from ocr_platform.api.main import create_app
    from fastapi.testclient import TestClient
    with TestClient(create_app()) as client:
        response = client.get("/openapi.json")
        assert response.status_code == 200
        return response.json()


def test_swagger_documents_exact_current_profile_fields(openapi):
    description = openapi["components"]["schemas"]["DocumentResultResponse"]["properties"]["fields"]["description"]
    sections = re.split(r"\nДля `([^`]+)`", description)
    by_type = dict(zip(sections[1::2], sections[2::2]))
    for profile in load_all_profiles().values():
        section = by_type[profile["document_type"]].split("\nПоля каждого объекта")[0]
        documented = set(re.findall(r"^\* `([^`]+)`", section, re.MULTILINE))
        assert documented == set(profile.get("fields", {})), profile["profile_id"]
    assert "`processing_started_at`" in sections[0]

    nested = by_type["rtk3"].split("\nПоля каждого объекта")[1]
    assert set(re.findall(r"^\* `([^`]+)`", nested, re.MULTILINE)) == {
        "priority_queue", "claimed_amount", "principal_debt", "financial_sanctions",
        "pau_only_amount", "total_amount", "is_salary_or_severance",
        "contract_number", "pledge_resolution", "secured_by_pledge"}


def test_swagger_court_example_matches_response_schema(openapi):
    examples = openapi["paths"]["/documents/{document_id}/result"]["get"]["responses"]["200"]["content"]["application/json"]["examples"]
    DocumentResultResponse.model_validate(examples["court_decision"]["value"])


@pytest.mark.parametrize("name,review", [("rtk3", False), ("rtk3_review", True)])
def test_swagger_rtk3_examples_match_current_contract_and_review_rules(openapi, name, review):
    from ocr_platform.services.quality_service import compute_rtk3_quality_scores
    from ocr_platform.services.rtk3_evidence import VERSION
    from ocr_platform.services.validation_service import review_requirement, validate_fields

    examples = openapi["paths"]["/documents/{document_id}/result"]["get"]["responses"]["200"]["content"]["application/json"]["examples"]
    assert set(examples) == {"court_decision", "rtk3", "rtk3_review"}
    payload = examples[name]["value"]
    DocumentResultResponse.model_validate(payload)
    profile = load_profile("rtk3")
    fields = payload["fields"]
    assert set(fields) == {*profile["fields"], "processing_started_at"}
    assert all(fields[key]["source"] == VERSION for key in profile["fields"])
    assert fields["kad_publication_date"]["value"] is None
    assert fields["pledge_resolution"]["value"] is None
    assert fields["secured_by_pledge"]["value"] is None
    assert re.fullmatch(r"\d+\.\d{2}", fields["total_claimed_amount"]["value"])
    rows = fields["claims"]["value"]
    assert [row["priority_queue"] for row in rows] == ["3 очередь", "3 очередь"]
    assert [row["contract_number"] for row in rows] == ["А-1", "А-2"]
    assert [row["pledge_resolution"] for row in rows] == ["yes", "no"]
    assert [row["secured_by_pledge"] for row in rows] == [True, False]
    for row in rows:
        assert row["is_salary_or_severance"] is False
        for field in ("principal_debt", "financial_sanctions", "pau_only_amount", "claimed_amount", "total_amount"):
            assert re.fullmatch(r"\d+\.\d{2}", row[field])
    status, issues = validate_fields(fields, "rtk3", profile)
    assert payload["validation_status"] == status == ("errors" if review else "ok")
    assert payload["human_review_required"] is review
    assert (payload["human_review_required"], payload["human_review_reason"]) == review_requirement(
        payload["overall_quality_score"], issues)
    assert [issue.model_dump() for issue in issues] == payload["validation_issues"]
    assert compute_rtk3_quality_scores(payload["raw_text"], fields, profile["fields"]) == (
        payload["technical_quality_score"], payload["semantic_confidence_score"], payload["overall_quality_score"])


def test_upload_type_description_distinguishes_unknown_and_auto(openapi):
    body_ref = openapi["paths"]["/documents/upload"]["post"]["requestBody"]["content"]["multipart/form-data"]["schema"]["$ref"]
    body = openapi["components"]["schemas"][body_ref.rsplit("/", 1)[1]]
    description = body["properties"]["document_type"]["description"]
    assert "document_type" in body["required"]
    assert "без автоматической классификации" in description
    for profile in load_all_profiles().values():
        assert profile["document_type"] in description

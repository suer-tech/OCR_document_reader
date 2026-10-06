import re

import pytest

from ocr_platform.api.schemas import DocumentResultResponse
from ocr_platform.orchestration.router import load_all_profiles


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

    from ocr_platform.services.extraction_agent import Rtk3Claim
    nested = by_type["rtk3"].split("\nПоля каждого объекта")[1]
    assert set(re.findall(r"^\* `([^`]+)`", nested, re.MULTILINE)) == set(Rtk3Claim.model_fields)


def test_swagger_response_examples_match_contract_and_review_rules(openapi):
    from ocr_platform.services.extraction_agent import Rtk3Claim
    from ocr_platform.services.quality_service import compute_quality_scores
    from ocr_platform.services.validation_service import profile_field_issues, review_requirement

    examples = openapi["paths"]["/documents/{document_id}/result"]["get"]["responses"]["200"]["content"]["application/json"]["examples"]
    assert set(examples) == {"court_decision", "rtk3", "rtk3_review"}
    for example in examples.values():
        DocumentResultResponse.model_validate(example["value"])
    for name in ("rtk3", "rtk3_review"):
        payload = examples[name]["value"]
        fields = payload["fields"]
        for row in fields["claims"]["value"]:
            assert Rtk3Claim.model_validate(row).model_dump() == row
        issues = profile_field_issues(fields, "rtk3")
        assert (payload["human_review_required"], payload["human_review_reason"]) == review_requirement(
            payload["overall_quality_score"], issues)
        assert [issue.model_dump() for issue in issues] == payload["validation_issues"]
        stored_fields = {**fields, "processing_started_at": fields["processing_started_at"]["value"]}
        assert compute_quality_scores(payload["raw_text"], stored_fields) == (
            payload["technical_quality_score"], payload["semantic_confidence_score"], payload["overall_quality_score"])


def test_upload_type_description_distinguishes_unknown_and_auto(openapi):
    body_ref = openapi["paths"]["/documents/upload"]["post"]["requestBody"]["content"]["multipart/form-data"]["schema"]["$ref"]
    body = openapi["components"]["schemas"][body_ref.rsplit("/", 1)[1]]
    description = body["properties"]["document_type"]["description"]
    assert "document_type" in body["required"]
    assert "без автоматической классификации" in description
    for profile in load_all_profiles().values():
        assert profile["document_type"] in description

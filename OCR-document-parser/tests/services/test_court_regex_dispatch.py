import asyncio

from ocr_platform.services.document_intel_service import simple_extract_fields
from ocr_platform.services import court_regex_jev


def test_opted_in_court_profile_uses_verified_pipeline(monkeypatch):
    seen = []
    async def fake(text, config, **kwargs):
        seen.append((text, config, kwargs))
        return {"case_number": {"value": "А40-1/2026", "source": "regex_jev", "confidence": .98}}
    monkeypatch.setattr(court_regex_jev, "extract_court_regex_jev", fake)
    config = {"extraction_mode": "regex_jev", "fields": {"case_number": {}}}
    result = asyncio.run(simple_extract_fields("source", config, "court_decision_ru", "run", "doc"))
    assert result["case_number"]["source"] == "regex_jev"
    assert seen == [("source", config, {"document_id": "doc", "pipeline_run_id": "run"})]

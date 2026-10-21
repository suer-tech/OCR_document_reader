"""Boolean/null contract, checked offline without model or server calls."""
import json

import pytest
from pydantic import ValidationError

from ocr_platform.api.schemas import FieldValue
from ocr_platform.services import court_selective_llm, extraction_agent
from ocr_platform.services.court_regex_candidates import extract_court_candidates


FLAG = "procedure_end_date_is_calculated"
HEADER = "РЕШЕНИЕ\n01 октября 2026 года\nРЕШИЛ:\n"
CASES = [
    ("Ввести процедуру реализации имущества сроком на шесть месяцев.", "01.04.2027", True),
    ("Ввести процедуру реализации имущества до 01.04.2027.", "01.04.2027", False),
    ("Признать гражданина банкротом.", None, None),
    ("Ввести процедуру реализации имущества до 01.04.2027. "
     "Продлить процедуру реализации имущества до 01.05.2027.", None, None),
]


@pytest.mark.parametrize("body,end,expected", CASES)
def test_regex_boolean_null_and_public_json(body, end, expected):
    result = extract_court_candidates(HEADER + body, ["procedure_end_date", FLAG])
    date_candidate = result.fields.get("procedure_end_date")
    flag_candidate = result.fields.get(FLAG)
    assert (date_candidate.value if date_candidate else None) == end
    value = flag_candidate.value if flag_candidate else None
    assert value is expected
    if flag_candidate:
        assert flag_candidate.dependencies == ("procedure_end_date",)
        assert flag_candidate.metadata["is_calculated"] is expected
    payload = FieldValue(name=FLAG, value=value, confidence=1.0)
    assert json.loads(payload.model_dump_json())["value"] is expected


@pytest.mark.asyncio
@pytest.mark.parametrize("body,end,expected", CASES)
async def test_legacy_regex_route_has_same_json_type(monkeypatch, body, end, expected):
    monkeypatch.setattr(extraction_agent, "_get_lf_client", lambda: None)
    monkeypatch.setattr(extraction_agent, "_active_model_settings", lambda: {})
    result = await extraction_agent._run_agent_extraction_impl(
        HEADER + body, {name: {"extraction_method": "regex_legacy"}
                        for name in ("procedure_end_date", FLAG)}, profile_id="court_decision_ru",
    )
    assert result["procedure_end_date"]["value"] == end
    assert result[FLAG]["value"] is expected


@pytest.mark.parametrize("value", [True, False, None])
def test_reserve_output_preserves_boolean_or_null_without_model_call(value):
    raw = {"value": value, "confidence": .9, "reasoning": "reason",
           "ambiguous": False, "evidence": "source" if value is not None else None}
    output = court_selective_llm._field_output(FLAG, raw, "source")
    assert output["value"] is value
    assert json.loads(FieldValue(name=FLAG, **output).model_dump_json())["value"] is value


@pytest.mark.parametrize("value", ["True", "False", "true", "false", "null", 0, 1])
def test_reserve_schema_rejects_strings_and_numbers(value):
    with pytest.raises(ValidationError):
        court_selective_llm.field_result_model(FLAG).model_validate({
            "value": value, "confidence": .9, "reasoning": "reason",
            "ambiguous": False, "evidence": "source",
        })

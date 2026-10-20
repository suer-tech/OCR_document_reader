import asyncio
from types import SimpleNamespace

from ocr_platform.services import court_selective_llm as service


def field(value, confidence=.9, evidence="source"):
    return {"value": value, "confidence": confidence, "evidence": evidence, "reasoning": "reason", "ambiguous": False}


def execute(monkeypatch, requested, responses):
    calls = []
    def provider(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(parsed=responses[min(len(calls) - 1, len(responses) - 1)], winner_model="test")
    monkeypatch.setattr(service, "call_llm_json_with_fallback", provider)
    monkeypatch.setattr(service, "get_field_instruction", lambda *args, **kwargs: kwargs.get("default", ""))
    result = asyncio.run(service.extract_court_llm_fields("source", {name: {} for name in requested},
                        {"models": {"llm_extraction": {"model": "test", "provider": "router_ai"}}}))
    return result, calls


def schema_fields(call):
    return set(call["response_schema"]["json_schema"]["schema"]["properties"])


def test_failed_response_retries_only_configured_claude_even_with_legacy_fallbacks(monkeypatch):
    calls = []
    model = "anthropic/claude-sonnet-5.5"

    def provider(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return None  # Includes an unparseable JSON response from the gateway.
        parsed = {"procedure_duration_months": field(6, confidence=80 if len(calls) == 2 else .9)}
        return SimpleNamespace(parsed=parsed, winner_model=model)

    monkeypatch.setattr(service, "call_llm_json_with_fallback", provider)
    monkeypatch.setattr(service, "get_field_instruction", lambda *args, **kwargs: kwargs.get("default", ""))
    result = asyncio.run(service.extract_court_llm_fields(
        "source", {"procedure_duration_months": {}},
        {"models": {"llm_extraction": {"provider": "router_ai", "model": model,
                                      "fallback_models": ["openai/gpt-oss-120b"]}}},
    ))
    assert len(calls) == 3
    assert all(call["provider"] == "router_ai" and call["models"] == [model] for call in calls)
    assert result["procedure_duration_months"]["value"] == 6
    assert result["procedure_duration_months"]["model"] == model


def test_provider_failure_exhausts_same_model_then_requires_review(monkeypatch):
    calls = []

    def provider(**kwargs):
        calls.append(kwargs)
        return None

    monkeypatch.setattr(service, "call_llm_json_with_fallback", provider)
    monkeypatch.setattr(service, "get_field_instruction", lambda *args, **kwargs: kwargs.get("default", ""))
    model = "anthropic/claude-sonnet-5.5"
    result = asyncio.run(service.extract_court_llm_fields(
        "source", {"case_number": {}},
        {"models": {"llm_extraction": {"provider": "router_ai", "model": model,
                                      "fallback_models": ["openai/gpt-oss-120b"]}}},
    ))
    assert len(calls) == 3
    assert all(call["models"] == [model] for call in calls)
    assert result["case_number"]["value"] is None
    assert result["case_number"]["validation_issue"]


def test_schema_and_prompt_only_request_selected_fields(monkeypatch):
    result, calls = execute(monkeypatch, ["procedure_duration_months"], [{"procedure_duration_months": field(6)}])
    assert schema_fields(calls[0]) == {"procedure_duration_months"}
    assert result["procedure_duration_months"]["value"] == 6
    assert result["procedure_duration_months"]["source"] == "court_llm_fallback"
    assert "case_number" not in calls[0]["system_prompt"]
    schema = calls[0]["response_schema"]["json_schema"]["schema"]
    for definition in schema["$defs"].values():
        if definition.get("type") == "object":
            assert definition["additionalProperties"] is False


def test_bad_confidence_retries_only_failed_field(monkeypatch):
    result, calls = execute(monkeypatch, ["procedure_duration_months", "case_number"], [
        {"procedure_duration_months": field(6, 80), "case_number": field("А01-1/2026")},
        {"procedure_duration_months": field(6)},
    ])
    assert len(calls) == 2
    assert schema_fields(calls[1]) == {"procedure_duration_months"}
    assert result["case_number"]["value"] == "А01-1/2026"
    assert result["procedure_duration_months"]["confidence"] == .9


def test_fabricated_citation_retries_then_requires_review(monkeypatch):
    result, calls = execute(monkeypatch, ["case_number"], [{"case_number": field("А01-1/2026", evidence="not present")}])
    assert len(calls) == 3
    assert result["case_number"]["value"] is None
    assert result["case_number"]["validation_issue"]


def test_boolean_is_strict_and_null_is_not_false(monkeypatch):
    result, calls = execute(monkeypatch, ["early_report_required"], [{"early_report_required": field("false")}, {"early_report_required": field(False)}])
    assert len(calls) == 2
    assert result["early_report_required"]["value"] is False


def test_months_reject_string_float_and_zero(monkeypatch):
    result, calls = execute(monkeypatch, ["procedure_duration_months"], [
        {"procedure_duration_months": field("6")}, {"procedure_duration_months": field(6.0)}, {"procedure_duration_months": field(0)},
    ])
    assert len(calls) == 3
    assert result["procedure_duration_months"]["value"] is None


def test_invalid_calendar_date_does_not_escape(monkeypatch):
    result, calls = execute(monkeypatch, ["decision_date"], [{"decision_date": field("31.02.2026")}])
    assert len(calls) == 3
    assert result["decision_date"]["value"] is None


def test_negative_report_order_needs_no_fabricated_positive_citation(monkeypatch):
    result, calls = execute(monkeypatch, ["early_report_required"], [{"early_report_required": field(False, evidence=None)}])
    assert len(calls) == 1
    assert result["early_report_required"]["value"] is False
    assert "validation_issue" not in result["early_report_required"]


def test_quote_whitespace_is_mapped_back_to_literal_source():
    text = "Место\nрождения: Москва"
    result = service._field_output("debtor_birth_place", field("Москва", evidence="Место рождения: Москва"), text)
    assert result["evidence"] == text
    assert text[result["evidence_start"]:result["evidence_end"]] == result["evidence"]


def test_long_sections_use_verified_source_anchors():
    text = "УСТАНОВИЛ:\nПервое предложение.\nДлинный исходный текст.\nПоследнее предложение.\nРЕШИЛ: Вывод."
    item = field({"start_anchor": "Первое предложение.", "end_anchor": "Последнее предложение."}, evidence=None)
    result = service._field_output("motivating_part", item, text)
    assert result["value"] == "Первое предложение.\nДлинный исходный текст.\nПоследнее предложение."
    assert text[result["evidence_start"]:result["evidence_end"]] == result["value"]


def test_real_anchor_cannot_omit_final_reasoning_paragraph():
    import pytest
    text = "УСТАНОВИЛ: Факты дела. Вывод суда. Руководствуясь статьями кодекса, суд РЕШИЛ: Итог."
    value = {"start_anchor": "Факты дела.", "end_anchor": "Вывод суда."}
    with pytest.raises(ValueError, match="Пропущены заключительные абзацы"):
        service._field_output("motivating_part", field(value, evidence=None), text)
    value["end_anchor"] = "Руководствуясь статьями кодекса, суд"
    assert service._field_output("motivating_part", field(value, evidence=None), text)["value"].endswith("суд")


def test_ambiguous_source_anchors_are_rejected():
    import pytest
    item = field({"start_anchor": "Начало", "end_anchor": "Конец"}, evidence=None)
    with pytest.raises(ValueError, match="однозначно"):
        service._field_output("motivating_part", item, "Начало раз. Начало два. Конец")


def test_procedure_type_format_matches_regex_path():
    result = service._field_output("procedure_type", field("процедура реализации имущества гражданина"), "source")
    assert result["value"] == "реализация имущества гражданина"

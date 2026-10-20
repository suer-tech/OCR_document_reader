"""Offline regressions for explicit appointment → procedure-end fallback policy."""
import asyncio
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from ocr_platform.services import court_regex_candidates as registry
from ocr_platform.services import court_regex_jev as routing
from ocr_platform.services import court_selective_llm as selective
from ocr_platform.services.court_regex_candidates import CourtCandidate, CourtCandidates


HEADER = "РЕШЕНИЕ\n01 октября 2026 года\nСудья Иванов\nРЕШИЛ:\n"
END = "10.03.2027"
APPOINTMENT = "20.03.2027"


def proposal(value, basis=None, dependencies=()):
    metadata = {"selection_basis": basis} if basis else {}
    return CourtCandidate(value, "source", 0, 6, "test", metadata, dependencies)


def mock_routes(monkeypatch, proposals, probabilities, fallback=None):
    calls = {"regex": [], "jev": [], "llm": []}

    def regex(text, names):
        calls["regex"].append(list(names))
        return CourtCandidates({name: value for name, value in proposals.items() if name in names}, {})

    def jev(**kwargs):
        calls["jev"].append(kwargs)
        return SimpleNamespace(
            parsed={"answers": {name: {"type": "noul", "noul": probability}
                                for name, probability in probabilities.items()}},
            winner_model="typesafe/jev-1.14",
        )

    async def llm(text, fields, config, **kwargs):
        calls["llm"].append(list(fields))
        return fallback or {name: {"value": None, "confidence": 0, "source": "court_llm_fallback"}
                            for name in fields}

    monkeypatch.setattr(routing, "extract_court_candidates", regex)
    monkeypatch.setattr(routing, "call_llm_json_with_fallback", jev)
    monkeypatch.setattr(routing, "extract_court_llm_fields", llm)
    return calls


def run(names, style="concrete"):
    config = {"fields": {name: {} for name in names},
              "models": {"verification": {"threshold": .95, "question_style": style}}}
    return asyncio.run(routing.extract_court_regex_jev(
        "source", config, document_id="policy-doc", pipeline_run_id="policy-run"))


def test_explicit_date_without_time_does_not_create_time(monkeypatch):
    monkeypatch.setattr(registry.legacy, "extract_procedure_end_date_with_meta", lambda text: (END, False))
    text = HEADER + f"Назначить судебное заседание на {APPOINTMENT}."
    result = registry.extract_court_candidates(text, ["next_session_date", "next_session_time"])
    date = result.fields["next_session_date"]
    assert date.value == APPOINTMENT
    assert date.metadata["selection_basis"] == "explicit_appointment"
    assert "procedure_end_date" not in date.dependencies
    assert "next_session_time" not in result.fields
    assert text[date.start:date.end] == date.quote


def test_end_fallback_does_not_turn_header_location_or_signature_time_into_appointment(monkeypatch):
    monkeypatch.setattr(registry.legacy, "extract_procedure_end_date_with_meta", lambda text: (END, False))
    text = ("Арбитражный суд\nг. Москва, ул. Ленина, д. 1\n" + HEADER
            + f"Ввести процедуру реализации имущества до {END}.\n"
            + "Электронная подпись действительна. Дата подписания: 01.10.2026 в 12:30.")
    result = registry.extract_court_candidates(
        text, ["next_session_date", "next_session_time", "court_hearing_address"])
    date = result.fields["next_session_date"]
    assert date.value == END
    assert date.metadata["selection_basis"] == "procedure_end_fallback"
    assert "procedure_end_date" in date.dependencies
    assert "next_session_time" not in result.fields
    assert "court_hearing_address" not in result.fields
    assert text[date.start:date.end] == date.quote


def test_no_appointment_and_no_end_has_no_date_proposal(monkeypatch):
    monkeypatch.setattr(registry.legacy, "extract_procedure_end_date_with_meta", lambda text: (None, False))
    result = registry.extract_court_candidates(HEADER + "Утвердить финансового управляющего.", ["next_session_date"])
    assert "next_session_date" not in result.fields
    assert result.problems["next_session_date"]


def test_explicit_appointment_equal_to_end_keeps_event_basis(monkeypatch):
    monkeypatch.setattr(registry.legacy, "extract_procedure_end_date_with_meta", lambda text: (END, False))
    text = HEADER + f"Назначить судебное заседание на {END} в 09:30."
    result = registry.extract_court_candidates(text, ["next_session_date", "next_session_time"])
    assert result.fields["next_session_date"].metadata["selection_basis"] == "explicit_appointment"
    assert result.fields["next_session_time"].value == "09:30"


def test_problematic_explicit_date_remains_visible_on_end_fallback(monkeypatch):
    monkeypatch.setattr(registry.legacy, "extract_procedure_end_date_with_meta", lambda text: (END, False))
    text = HEADER + "Назначить судебное заседание на 31.02.2027."
    result = registry.extract_court_candidates(text, ["next_session_date"])
    candidate = result.fields["next_session_date"]
    assert candidate.value == END
    assert candidate.metadata["selection_basis"] == "procedure_end_fallback"
    assert candidate.metadata["explicit_appointment_problem"] == "invalid_appointment_date"


@pytest.mark.parametrize("style", ["concrete", "context"])
@pytest.mark.parametrize("basis", ["explicit_appointment", "procedure_end_fallback"])
def test_one_jev_question_uses_document_branch_even_in_context_style(monkeypatch, style, basis):
    dependencies = ("procedure_end_date",) if basis == "procedure_end_fallback" else ()
    proposals = {"next_session_date": proposal(END, basis, dependencies),
                 "procedure_end_date": proposal(END)}
    calls = mock_routes(monkeypatch, proposals, {name: .99 for name in proposals})
    result = run(["next_session_date"], style)
    assert result["next_session_date"]["selection_basis"] == basis
    assert len(calls["jev"]) == 1
    questions = calls["jev"][0]["decisions_questions"]
    assert [key for key in questions if "next_session_date" in key] == ["next_session_date"]
    instructions = questions["next_session_date"]["instructions"]
    # Compare the actual proposition, before common semantic guard/rules text.
    proposition = instructions.split("Проверяй", 1)[0].casefold()
    if basis == "procedure_end_fallback":
        assert "окончания" in proposition
    else:
        assert "назнач" in proposition
    assert calls["llm"] == []


def test_rejected_end_source_invalidates_high_scoring_fallback_date(monkeypatch):
    proposals = {"next_session_date": proposal(END, "procedure_end_fallback", ("procedure_end_date",)),
                 "procedure_end_date": proposal(END)}
    calls = mock_routes(monkeypatch, proposals, {"next_session_date": .99, "procedure_end_date": .2})
    result = run(["next_session_date"])
    assert calls["llm"] == [["next_session_date"]]
    assert result["next_session_date"]["source"] == "court_llm_fallback"
    assert len(calls["jev"]) == 1  # A successful or empty LLM result is never JEV-checked again.


def test_explicit_date_survives_rejected_end_source(monkeypatch):
    proposals = {"next_session_date": proposal(APPOINTMENT, "explicit_appointment"),
                 "procedure_end_date": proposal(END)}
    calls = mock_routes(monkeypatch, proposals, {"next_session_date": .99, "procedure_end_date": .2})
    result = run(["next_session_date", "procedure_end_date"])
    assert result["next_session_date"]["value"] == APPOINTMENT
    assert result["next_session_date"]["source"] == "regex_jev"
    assert calls["llm"] == [["procedure_end_date"]]


def test_time_only_request_extracts_internal_dependencies_transitively(monkeypatch):
    proposals = {"next_session_time": proposal("09:30", dependencies=("next_session_date",)),
                 "next_session_date": proposal(END, "procedure_end_fallback", ("procedure_end_date",)),
                 "procedure_end_date": proposal(END)}
    calls = mock_routes(monkeypatch, proposals, {name: .99 for name in proposals})
    result = run(["next_session_time"])
    assert set(calls["regex"][0]) >= {"next_session_time", "next_session_date", "procedure_end_date"}
    assert set(result) == {"next_session_time"}  # Source dependencies are internal only.


@pytest.mark.parametrize("source", ["regex_jev", "court_llm_fallback"])
def test_nonempty_fallback_date_cannot_support_time_or_venue(source):
    output = {"next_session_date": {"value": END, "source": source, "selection_basis": "procedure_end_fallback"},
              "next_session_time": {"value": "12:30", "confidence": .99, "source": source},
              "court_hearing_address": {"value": "г. Москва, ул. Ленина, д. 1", "confidence": .99, "source": source}}
    routing._validate_relationships(output)
    for name in ("next_session_time", "court_hearing_address"):
        assert output[name]["value"] is None
        assert output[name]["confidence"] == 0
        assert output[name]["validation_issue"]


def test_explicit_basis_preserves_linked_time_even_when_equal_to_end():
    output = {"next_session_date": {"value": END, "selection_basis": "explicit_appointment"},
              "procedure_end_date": {"value": END}, "next_session_time": {"value": "09:30"},
              "court_hearing_address": {"value": "г. Москва, ул. Ленина, д. 1"}}
    routing._validate_relationships(output)
    assert output["next_session_time"]["value"] == "09:30"
    assert output["court_hearing_address"]["value"]


def test_old_mock_without_basis_keeps_existing_nonempty_date_behavior():
    output = {"next_session_date": {"value": END}, "next_session_time": {"value": "09:30"}}
    routing._validate_relationships(output)
    assert output["next_session_time"]["value"] == "09:30"


def llm_field(value, basis=None, evidence="source"):
    raw = {"value": value, "confidence": .98, "reasoning": "reason", "ambiguous": False, "evidence": evidence}
    if basis is not None:
        raw["selection_basis"] = basis
    return raw


def test_llm_date_schema_requires_explicit_branch_discriminator():
    model = selective.field_result_model("next_session_date")
    schema = model.model_json_schema()
    assert "selection_basis" in schema["required"]
    assert set(schema["properties"]["selection_basis"]["enum"]) == {
        "explicit_appointment", "procedure_end_fallback", "not_found"}
    with pytest.raises(ValidationError):
        model.model_validate(llm_field(END))
    with pytest.raises(ValidationError):
        model.model_validate(llm_field(END, "guessed"))


@pytest.mark.parametrize("basis,value", [("explicit_appointment", APPOINTMENT),
                                         ("procedure_end_fallback", END), ("not_found", None)])
def test_llm_output_keeps_selection_basis_for_final_guards(basis, value):
    result = selective._field_output("next_session_date", llm_field(value, basis), "source")
    assert result["selection_basis"] == basis
    assert result["value"] == value


def test_llm_missing_basis_retries_only_date_and_prompt_describes_fallback(monkeypatch):
    calls = []
    replies = [
        {"next_session_date": llm_field(END), "case_number": llm_field("А40-1/2026")},
        {"next_session_date": llm_field(END, "procedure_end_fallback")},
    ]

    def provider(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(parsed=replies[min(len(calls) - 1, 1)], winner_model="test")

    monkeypatch.setattr(selective, "call_llm_json_with_fallback", provider)
    monkeypatch.setattr(selective, "get_field_instruction", lambda *args, **kwargs: kwargs.get("default", ""))
    result = asyncio.run(selective.extract_court_llm_fields(
        "source", {"next_session_date": {}, "case_number": {}},
        {"models": {"llm_extraction": {"model": "test", "provider": "router_ai"}}}))
    assert len(calls) == 2
    retried = calls[1]["response_schema"]["json_schema"]["schema"]["properties"]
    assert set(retried) == {"next_session_date"}
    assert result["case_number"]["value"] == "А40-1/2026"
    assert result["next_session_date"]["selection_basis"] == "procedure_end_fallback"
    prompt = calls[0]["system_prompt"].casefold()
    assert "selection_basis" in prompt
    assert "procedure_end_fallback" in prompt
    assert "окончания процедуры" in prompt

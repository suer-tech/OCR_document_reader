import asyncio
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest
import yaml

from ocr_platform.services import court_regex_jev as service
from ocr_platform.services.court_regex_candidates import CourtCandidate, CourtCandidates


def test_all_profile_fields_have_verification_rules():
    profile = Path(__file__).parents[2] / "src/ocr_platform/config/pipelines/profiles/court_decision_ru.yaml"
    fields = yaml.safe_load(profile.read_text(encoding="utf-8"))["fields"]
    assert set(fields) == set(service.FIELD_RULES)
    assert fields["debtor_full_name"]["regex_jev_mode"] == "regex_jev"
    assert fields["judge_full_name"].get("regex_jev_mode", "regex_jev") == "regex_jev"


@pytest.mark.parametrize("probability", [.99, .2, None])
def test_configured_debtor_name_is_verified_and_only_rejected_name_enters_llm(monkeypatch, probability):
    profile_path = Path(__file__).parents[2] / "src/ocr_platform/config/pipelines/profiles/court_decision_ru.yaml"
    profile = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
    profile["fields"] = {name: profile["fields"][name] for name in ("debtor_full_name", "case_number")}
    text = ("РЕШЕНИЕ\nДело № А40-1/2026\nРЕШИЛ:\n"
            "Признать Иванова Ивана Ивановича несостоятельным (банкротом).")
    if probability is None:
        text = "РЕШЕНИЕ\nДело № А40-1/2026\nРЕШИЛ:\nВвести процедуру."
    calls = {"jev": [], "llm": []}

    def verify(**kwargs):
        calls["jev"].append(kwargs)
        if probability is None:
            assert "debtor_full_name" not in kwargs["decisions_questions"]
        else:
            assert kwargs["decisions_state"]["fields"]["debtor_full_name"]["value"] == {
                "debtor_last_name": "Иванов", "debtor_first_name": "Иван", "debtor_patronymic": "Иванович"}
        return SimpleNamespace(parsed={"answers": {
            "debtor_full_name": {"type": "noul", "noul": probability},
            "case_number": {"type": "noul", "noul": .99}}}, winner_model="test")

    async def llm(text, fields, profile, **kwargs):
        calls["llm"].append(list(fields))
        return {"debtor_full_name": {"value": {"debtor_last_name": "Иванов", "debtor_first_name": "Иван",
                                               "debtor_patronymic": "Иванович"},
                                     "source": "court_llm_fallback", "confidence": .9}}

    monkeypatch.setattr(service, "call_llm_json_with_fallback", verify)
    monkeypatch.setattr(service, "extract_court_llm_fields", llm)
    result = asyncio.run(service.extract_court_regex_jev(text, profile, document_id="d", pipeline_run_id="r"))
    assert len(calls["jev"]) == 1  # LLM result is never verified a second time.
    assert calls["llm"] == ([] if probability == .99 else [["debtor_full_name"]])
    assert result["debtor_full_name"]["source"] == ("regex_jev" if probability == .99 else "court_llm_fallback")
    assert result["case_number"]["value"] == "А40-1/2026"


def test_direct_llm_field_runs_immediately_in_parallel_and_never_enters_jev(monkeypatch):
    started_direct, started_verification = threading.Event(), threading.Event()
    calls = {"regex": [], "jev": [], "llm": []}
    full_name = {"debtor_last_name": "Иванов", "debtor_first_name": "Иван", "debtor_patronymic": "Иванович"}

    def regex(text, names):
        calls["regex"].append(names)
        assert "debtor_full_name" not in names
        return CourtCandidates({"judge_full_name": candidate("Петров И.И.")}, {})

    def verify(**kwargs):
        assert started_direct.wait(2)
        started_verification.set()
        calls["jev"].append(kwargs)
        return SimpleNamespace(parsed={"answers": {"judge_full_name": {"type": "noul", "noul": .99}}}, winner_model="test")

    async def llm(text, fields, profile, **kwargs):
        calls["llm"].append(list(fields))
        assert list(fields) == ["debtor_full_name"]
        started_direct.set()
        assert await asyncio.to_thread(started_verification.wait, 2)
        return {"debtor_full_name": {"value": full_name, "confidence": .98, "source": "court_llm_fallback"},
                "judge_full_name": {"value": "injected"}}

    monkeypatch.setattr(service, "extract_court_candidates", regex)
    monkeypatch.setattr(service, "call_llm_json_with_fallback", verify)
    monkeypatch.setattr(service, "extract_court_llm_fields", llm)
    config = {"fields": {"debtor_full_name": {"required": True, "regex_jev_mode": "direct_llm"}, "judge_full_name": {}}}
    result = asyncio.run(service.extract_court_regex_jev("source", config, document_id="d", pipeline_run_id="r"))
    assert result["debtor_full_name"]["value"] == full_name
    assert result["debtor_full_name"]["extraction_route"] == "direct_llm"
    assert "verification" not in result["debtor_full_name"]
    assert "regex_verification" not in result["debtor_full_name"]
    assert result["judge_full_name"]["value"] == "Петров И.И."
    assert len(calls["jev"]) == len(calls["llm"]) == 1
    assert set(calls["jev"][0]["decisions_questions"]) == {"judge_full_name"}
    assert set(calls["jev"][0]["decisions_state"]["fields"]) == {"judge_full_name"}


@pytest.mark.parametrize("llm_result", [{}, {"debtor_full_name": {"value": None, "confidence": .5, "source": "court_llm_fallback"}}])
def test_direct_required_field_missing_goes_to_review_without_jev(monkeypatch, llm_result):
    def forbidden(*args, **kwargs):
        raise AssertionError("Direct-only field must not enter regex or JEV")

    async def llm(*args, **kwargs):
        return llm_result

    monkeypatch.setattr(service, "extract_court_candidates", forbidden)
    monkeypatch.setattr(service, "call_llm_json_with_fallback", forbidden)
    monkeypatch.setattr(service, "extract_court_llm_fields", llm)
    config = {"fields": {"debtor_full_name": {"required": True, "regex_jev_mode": "direct_llm"}}}
    result = asyncio.run(service.extract_court_regex_jev("source", config, document_id="d", pipeline_run_id="r"))
    assert result["debtor_full_name"]["value"] is None
    assert result["debtor_full_name"]["validation_issue"]


def test_invalid_direct_mode_fails_before_any_provider_call():
    config = {"fields": {"debtor_full_name": {"regex_jev_mode": "typo"}}}
    with pytest.raises(ValueError, match="regex_jev_mode"):
        asyncio.run(service.extract_court_regex_jev("source", config, document_id="d", pipeline_run_id="r"))


def candidate(value, dependencies=()):
    return CourtCandidate(value, "source", 0, 6, "test", {}, dependencies)


def setup(monkeypatch, proposals, probabilities, llm_values=None):
    calls = {"jev": [], "llm": []}
    monkeypatch.setattr(service, "extract_court_candidates", lambda text, names: CourtCandidates(proposals, {}))
    def verify(**kwargs):
        calls["jev"].append(kwargs)
        return SimpleNamespace(parsed={"model": "typesafe/jev-1.14-test", "answers": {name: {"type": "noul", "noul": probability} for name, probability in probabilities.items()}}, winner_model="typesafe/jev-1.14")
    async def fallback(text, fields, profile, **kwargs):
        calls["llm"].append(list(fields))
        return llm_values or {name: {"value": "fallback", "confidence": .8, "source": "court_llm_fallback"} for name in fields}
    monkeypatch.setattr(service, "call_llm_json_with_fallback", verify)
    monkeypatch.setattr(service, "extract_court_llm_fields", fallback)
    return calls


def run(fields, verification=None):
    config = {"fields": {name: {} for name in fields}, "models": {"verification": verification or {"threshold": .95}}}
    return asyncio.run(service.extract_court_regex_jev("source", config, document_id="doc", pipeline_run_id="run"))


def test_approved_fields_need_no_llm_and_false_is_a_value(monkeypatch):
    calls = setup(monkeypatch, {"early_report_required": candidate(False)}, {"early_report_required": .98})
    result = run(["early_report_required"])
    assert result["early_report_required"]["value"] is False
    assert result["early_report_required"]["confidence"] == .98
    assert result["early_report_required"]["source"] == "regex_jev"
    assert len(calls["jev"]) == 1
    assert calls["llm"] == []
    assert calls["jev"][0]["decisions_state"]["document"] == "source"


def test_only_missing_and_rejected_fields_go_to_llm(monkeypatch):
    calls = setup(monkeypatch, {"case_number": candidate("А01-1/2026"), "decision_date": candidate("01.01.2026")}, {"case_number": .99, "decision_date": .1})
    result = run(["case_number", "decision_date", "debtor_inn"])
    assert result["case_number"]["value"] == "А01-1/2026"
    assert calls["llm"] == [["decision_date", "debtor_inn"]]
    assert set(calls["jev"][0]["decisions_questions"]) == {"case_number", "decision_date"}
    assert len(calls["jev"]) == 1  # LLM outputs never enter JEV.


@pytest.mark.parametrize("probability", [None, True, 95, -1, float("nan"), "0.99"])
def test_invalid_probability_routes_to_fallback(monkeypatch, probability):
    calls = setup(monkeypatch, {"case_number": candidate("А01-1/2026")}, {"case_number": probability})
    run(["case_number"])
    assert calls["llm"] == [["case_number"]]


def test_provider_failure_routes_fields_without_second_jev(monkeypatch):
    calls = setup(monkeypatch, {"case_number": candidate("А01-1/2026")}, {})
    monkeypatch.setattr(service, "call_llm_json_with_fallback", lambda **kwargs: None)
    run(["case_number"])
    assert calls["llm"] == [["case_number"]]


def test_rejected_calculation_base_blocks_dependent_approval(monkeypatch):
    proposals = {"procedure_end_date": candidate("01.01.2027"), "early_report_deadline": candidate("22.12.2026", ("procedure_end_date",))}
    calls = setup(monkeypatch, proposals, {"procedure_end_date": .2, "early_report_deadline": .99})
    run(["procedure_end_date", "early_report_deadline"])
    assert calls["llm"] == [["procedure_end_date", "early_report_deadline"]]


def test_rejected_decision_date_blocks_its_resolutive_date_substitution(monkeypatch):
    proposals = {"decision_date": candidate("24.09.2026"),
                 "resolutive_part_date": candidate("24.09.2026", ("decision_date",))}
    calls = setup(monkeypatch, proposals, {"decision_date": .2, "resolutive_part_date": .99})
    run(list(proposals))
    assert calls["llm"] == [["decision_date", "resolutive_part_date"]]
    assert len(calls["jev"]) == 1


def test_rejected_hearing_date_also_rechecks_its_time_and_address(monkeypatch):
    proposals = {"next_session_date": candidate("01.01.2027"),
                 "next_session_time": candidate("10:00", ("next_session_date",)),
                 "court_hearing_address": candidate("зал 1", ("next_session_date",))}
    calls = setup(monkeypatch, proposals, {"next_session_date": .2, "next_session_time": .99, "court_hearing_address": .99})
    run(list(proposals))
    assert calls["llm"] == [list(proposals)]


def test_fallback_cannot_overwrite_an_approved_field(monkeypatch):
    setup(monkeypatch, {"case_number": candidate("А01-1/2026")}, {"case_number": .99},
          {"case_number": {"value": "injected"}, "debtor_inn": {"value": None, "confidence": 0}})
    assert run(["case_number", "debtor_inn"])["case_number"]["value"] == "А01-1/2026"


def test_per_field_threshold(monkeypatch):
    calls = setup(monkeypatch, {"case_number": candidate("А01-1/2026")}, {"case_number": .96})
    run(["case_number"], {"threshold": .95, "field_thresholds": {"case_number": .99}})
    assert calls["llm"] == [["case_number"]]


@pytest.mark.parametrize("probability, accepted", [(.599, False), (.6, True), (.601, True)])
def test_current_profile_accepts_at_point_six_and_routes_lower_scores_to_llm(monkeypatch, probability, accepted):
    path = Path(__file__).parents[2] / "src/ocr_platform/config/pipelines/profiles/court_decision_ru.yaml"
    profile = yaml.safe_load(path.read_text(encoding="utf-8"))
    profile["fields"] = {"case_number": profile["fields"]["case_number"]}
    calls = setup(monkeypatch, {"case_number": candidate("А01-1/2026")}, {"case_number": probability})
    result = asyncio.run(service.extract_court_regex_jev("source", profile, document_id="d", pipeline_run_id="r"))
    assert profile["models"]["verification"]["threshold"] == .6
    assert (result["case_number"]["source"] == "regex_jev") is accepted
    assert calls["llm"] == ([] if accepted else [["case_number"]])
    assert len(calls["jev"]) == 1


def test_concrete_boolean_question_matches_false_proposal():
    question = service.verification_question("early_report_required", candidate(False))
    assert "не возложил" in question["instructions"]


def test_null_calculation_flag_does_not_ask_whether_date_is_explicit():
    missing = CourtCandidate(None, "source", 0, 6, "absence", {"absence": True})
    question = service.verification_question("procedure_end_date_is_calculated", missing)
    assert "результат null" in question["instructions"]
    assert "не нужно рассчитывать" not in question["instructions"]


def test_optional_absence_requires_its_own_confirmation(monkeypatch):
    calls = setup(monkeypatch, {}, {"next_session_date": .99})
    monkeypatch.setattr(service, "extract_court_candidates", lambda *_: CourtCandidates({}, {"next_session_date": "no_explicit_appointment"}))
    result = run(["next_session_date"])
    assert result["next_session_date"]["value"] is None
    assert result["next_session_date"]["verification"]["absence"] is True
    assert "evidence" not in result["next_session_date"]
    assert calls["llm"] == []
    assert calls["jev"][0]["decisions_state"]["fields"]["next_session_date"]["metadata"]["absence"] is True


def test_conflict_is_not_treated_as_absence(monkeypatch):
    calls = setup(monkeypatch, {}, {"next_session_date": .99})
    monkeypatch.setattr(service, "extract_court_candidates", lambda *_: CourtCandidates({}, {"next_session_date": "conflicting_appointments"}))
    run(["next_session_date"])
    assert calls["jev"] == []
    assert calls["llm"] == [["next_session_date"]]


def test_failed_base_field_and_required_null_force_human_review(monkeypatch):
    from ocr_platform.services.validation_service import profile_field_issues, review_requirement
    setup(monkeypatch, {}, {}, {"case_number": {"value": None, "confidence": .9, "source": "court_llm_fallback"}})
    config = {"fields": {"case_number": {"required": True}}}
    output = asyncio.run(service.extract_court_regex_jev("source", config, document_id="doc", pipeline_run_id="run"))
    assert output["case_number"]["validation_issue"]
    issues = profile_field_issues(output, "court_decision_ru")
    assert issues[0].field_name == "case_number"
    assert review_requirement(.99, issues) == (True, "court_field_requires_review")


def test_optional_failed_field_is_reviewed_even_with_high_overall_quality():
    from ocr_platform.services.validation_service import profile_field_issues, review_requirement
    output = {"motivating_part": {"value": None, "source": "court_llm_fallback", "validation_issue": "Невалидная цитата"}}
    issues = profile_field_issues(output, "court_decision_ru")
    assert review_requirement(.99, issues) == (True, "court_field_requires_review")


def test_required_absence_cannot_be_approved(monkeypatch):
    calls = setup(monkeypatch, {}, {"case_number": .99})
    monkeypatch.setattr(service, "extract_court_candidates", lambda *_: CourtCandidates({}, {"case_number": "not_found"}))
    config = {"fields": {"case_number": {"required": True}}}
    asyncio.run(service.extract_court_regex_jev("source", config, document_id="doc", pipeline_run_id="run"))
    assert calls["jev"] == []
    assert calls["llm"] == [["case_number"]]


@pytest.mark.parametrize("threshold", [True, .5, 1.01, float("nan"), "0.95"])
def test_invalid_threshold_fails_before_request(threshold):
    with pytest.raises(ValueError, match="threshold"):
        run(["case_number"], {"threshold": threshold})

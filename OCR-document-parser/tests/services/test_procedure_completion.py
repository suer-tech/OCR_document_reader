import json
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from ocr_platform.orchestration import router
from ocr_platform.services import procedure_completion as service
from ocr_platform.services.validation_service import (
    profile_field_issues,
    review_requirement,
)
from ocr_platform.storage import models, repository
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

HEADER = "ОПРЕДЕЛЕНИЕ\nДело № А12-12345/25\n31 января 2026 года"
HISTORY = "Ранее суд ввёл РИ в отношении Тестова Олега Петровича (ИНН 123456789012)."
START = "Завершить процедуру реализации имущества Тестова Олега Петровича (ИНН 012345678901)."
RELEASE = "Освободить должника от дальнейшего исполнения требований кредиторов."
MANAGER = "Прекратить полномочия финансового управляющего Макетова Олега Петровича."
END = "Определение может быть обжаловано в течение месяца."


def fact(value=None, quote="", source=None, review=None):
    return {"value": value, "source": source, "quote": quote, "reasoning": "Выбор по контексту акта.",
            "confidence": 0.98, "review_reason": review}


def evidence(start=START, end=END, **patch):
    return {
        **dict.fromkeys(service.CompletionEvidence.model_fields, fact()),
        "document_type": fact("determination", "ОПРЕДЕЛЕНИЕ", "header"),
        "document_is_completion_act": fact(True, start, "resolutive_part"),
        "case_number_raw": fact("А12-12345/25", "Дело № А12-12345/25", "header"),
        "judicial_act_date": fact("2026-01-31", "31 января 2026 года", "header"),
        "completion_result": fact("completed", start, "resolutive_part"),
        "completed_procedure_type": fact("realization", start, "resolutive_part"),
        "resolutive_part_text": fact({"start_quote": start, "end_quote": end}, source="resolutive_part"),
        "debtor_inn": fact("012345678901", start, "resolutive_part"),
        "financial_manager_full_name": fact("Макетов Олег Петрович", MANAGER, "resolutive_part"),
        "debtor_aliases": fact([]),
        "excluded_creditors": fact([]),
        "debt_release_status": fact("applies", RELEASE, "resolutive_part"),
        "debt_release_text": fact(RELEASE, source="resolutive_part"),
        "prior_restructuring_quote": "",
        "explicit_completion_quote": "",
        **patch,
    }


def build(payload, text):
    return service.build_fields(service.CompletionEvidence.model_validate(payload), text, "test-model")


def test_llm_choice_and_disputed_values_require_source_evidence():
    text = f"{HEADER}\n{HISTORY}\nОПРЕДЕЛИЛ:\n{START}\n{RELEASE}\n{MANAGER}\n{END}"
    accepted = build(evidence(), text)
    assert accepted["debtor_inn"]["value"] == "012345678901"
    assert accepted["case_number"]["value"] == "А12-12345/2025"
    assert accepted["case_number_raw"]["value"] == "А12-12345/25"
    assert accepted["debtor_inn"]["extraction_method"] == "llm"
    unsupported = build(evidence(debtor_inn=fact("123456789012", START, "resolutive_part")), text)
    assert unsupported["debtor_inn"]["value"] is None
    assert review_requirement(1.0, profile_field_issues(unsupported, service.PROFILE))[0]
    unresolved = build(evidence(debtor_inn=fact(review="В тексте два равносильных ИНН.")), text)
    assert unresolved["debtor_inn"]["value"] is None
    assert review_requirement(1.0, profile_field_issues(unresolved, service.PROFILE)) == (
        True, "В тексте два равносильных ИНН.",
    )
    flagged = build(evidence(debtor_inn=fact("012345678901", START, "resolutive_part",
                                            review="ИНН в прежнем решении отличается.")), text)
    assert flagged["debtor_inn"]["value"] == "012345678901"
    assert review_requirement(1.0, profile_field_issues(flagged, service.PROFILE)) == (
        True, "ИНН в прежнем решении отличается.",
    )


@pytest.mark.parametrize("explicit_end,direct", [(False, False), (True, True)])
def test_transition_explicit_date_has_priority_over_calculation(explicit_end, direct):
    introduction = "Ввести процедуру реализации имущества гражданина сроком на шесть месяцев."
    prior = "Ранее введена реструктуризация долгов."
    explicit = "Завершить процедуру реструктуризации долгов должника." if direct else ""
    identity = "Должник Тестов Олег Петрович (ИНН 012345678901)."
    end_order = "Установить дату окончания процедуры 15 июля 2026 года."
    text = f"{HEADER}\n{identity}\n{prior}\nРЕШИЛ:\n{explicit}\n{introduction}\n{end_order if explicit_end else ''}\n{MANAGER}\n{END}"

    def operative(value):
        return fact(value, introduction, "resolutive_part")

    fields = build(evidence(
        start=explicit or introduction, debtor_inn=fact("012345678901", identity, "header"),
        completion_result=operative(service.TRANSITION),
        completed_procedure_type=operative("restructuring"), next_procedure_type=operative("realization"),
        prior_restructuring_quote=prior, explicit_completion_quote=explicit,
        debt_release_status=fact(), debt_release_text=fact(),
        new_procedure_start_date=fact("2026-01-31", "31 января 2026 года", "header"),
        new_procedure_duration_months=operative(6),
        new_procedure_end_date=fact("2026-07-15", end_order, "resolutive_part") if explicit_end else fact(),
    ), text)
    assert fields["new_procedure_end_date"]["value"] == ("2026-07-15" if explicit_end else "2026-07-31")
    assert fields["new_procedure_end_date_is_calculated"]["value"] is not explicit_end
    assert fields["debt_release_status"]["value"] is None
    issues = profile_field_issues(fields, service.PROFILE)
    expected_review = (False, None) if direct else (True, service.INFERRED_TRANSITION_REASON)
    assert review_requirement(1.0, issues) == expected_review
    if not direct:
        unsupported = build(evidence(
            start=introduction, completion_result=operative(service.TRANSITION),
            completed_procedure_type=operative("restructuring"), next_procedure_type=operative("realization"),
            prior_restructuring_quote="Ранее введена процедура, которой в тексте нет.",
            debt_release_status=fact(), debt_release_text=fact(),
        ), text)
        assert unsupported["completed_procedure_type"]["value"] is None
        assert unsupported["completion_result"]["value"] == "unclear"
        assert unsupported["next_procedure_type"]["value"] is None
        assert review_requirement(1.0, profile_field_issues(unsupported, service.PROFILE))[0]


def test_partial_release_keeps_literal_creditor_basis_and_requests_review():
    basis = "Не применять освобождение к требованиям публичного акционерного общества «Сбербанк России» по акту от 19 ноября 2024 года."
    text = f"{HEADER}\nОПРЕДЕЛИЛ:\n{START}\n{basis}\n{MANAGER}\n{END}"
    fields = build(evidence(
        debt_release_status=fact("partially_applies", basis, "resolutive_part"),
        debt_release_text=fact(basis, source="resolutive_part"),
        excluded_creditors=fact([{"creditor_name": 'ПАО «Сбербанк России»', "basis_act_date": "2024-11-19",
                                  "basis_text": basis, "restriction_text": basis,
                                  "creditor_inn": "1234567890", "amount": "2024"}], source="resolutive_part"),
    ), text)
    assert fields["excluded_creditors"]["value"] == [{
        "creditor_name": "ПАО Сбербанк", "basis_act_date": "2024-11-19", "basis_text": basis,
        "restriction_text": basis, "creditor_inn": None, "amount": None,
    }]
    issues = profile_field_issues(fields, service.PROFILE)
    assert [issue.code for issue in issues] == ["partial_debt_release"]
    assert review_requirement(1.0, issues) == (True, service.PARTIAL_RELEASE_REASON)


def test_release_text_requires_full_paragraph_and_unknown_ri_has_explicit_status():
    text = f"{HEADER}\nОПРЕДЕЛИЛ:\n{START}\n{RELEASE}\n{MANAGER}\n{END}"
    truncated = build(evidence(debt_release_text=fact("от дальнейшего исполнения требований кредиторов.",
                                                       source="resolutive_part")), text)
    assert truncated["debt_release_text"]["value"] is None
    assert review_requirement(1.0, profile_field_issues(truncated, service.PROFILE))[0]
    unknown = build(evidence(debt_release_status=fact(), debt_release_text=fact()), text)
    assert unknown["debt_release_status"]["value"] == "not_specified"
    assert review_requirement(1.0, profile_field_issues(unknown, service.PROFILE)) == (
        True, service.UNSPECIFIED_RELEASE_REASON,
    )
    truncated_operative = build(evidence(end=RELEASE), text + "\nСудья И. И. Тестов")
    assert truncated_operative["resolutive_part_text"]["value"] is None
    assert review_requirement(1.0, profile_field_issues(truncated_operative, service.PROFILE))[0]


def test_restructuring_completion_with_null_release_does_not_require_review():
    start = "Завершить процедуру реструктуризации долгов Тестова Олега Петровича (ИНН 012345678901)."
    text = f"{HEADER}\nОПРЕДЕЛИЛ:\n{start}\n{MANAGER}\n{END}"
    payload = evidence(start=start, completed_procedure_type=fact("restructuring", start, "resolutive_part"),
                       debt_release_status=fact(), debt_release_text=fact(), excluded_creditors=fact())
    fields = build(payload, text)
    assert fields["debt_release_status"]["value"] is None
    assert fields["excluded_creditors"]["value"] == []
    assert not profile_field_issues(fields, service.PROFILE)
    payload["debtor_inn"] = fact()
    incomplete = build(payload, text)
    assert review_requirement(1.0, profile_field_issues(incomplete, service.PROFILE)) == (
        True, "Не установлен ИНН должника.",
    )


def test_secured_settlement_requires_review_without_changing_release_status():
    release = "Не применять к должнику правила об освобождении от обязательств."
    settlement = "Утвердить мировое соглашение с залоговым кредитором АО «Пример»."
    text = f"{HEADER}\nОПРЕДЕЛИЛ:\n{START}\n{release}\n{settlement}\n{MANAGER}\n{END}"
    fields = build(evidence(
        debt_release_status=fact("does_not_apply", release, "resolutive_part"),
        debt_release_text=fact(release, source="resolutive_part"),
        secured_creditor_settlement_approved=fact(True, settlement, "resolutive_part"),
        secured_creditor_settlement_text=fact(settlement, source="resolutive_part"),
    ), text)
    assert fields["debt_release_status"]["value"] == "does_not_apply"
    assert fields["secured_creditor_settlement_approved"]["value"] is True
    assert fields["secured_creditor_settlement_text"]["value"] == settlement
    assert review_requirement(1.0, profile_field_issues(fields, service.PROFILE))[0]


@pytest.mark.parametrize("status,procedure,review", [("not_applicable", "restructuring", False), ("unclear", "realization", True)])
def test_saved_v1_status_preserves_original_validation(status, procedure, review):
    fields = {name: {"value": "present"} for name in ["case_number", "document_type", "judicial_act_date", "resolutive_part_text"]}
    fields.update(document_is_completion_act={"value": True}, completed_procedure_type={"value": procedure},
                  completion_result={"value": "completed", "prompt_version": "procedure_completion_v1"},
                  debt_release_status={"value": status})
    assert review_requirement(1.0, profile_field_issues(fields, service.PROFILE))[0] is review
    assert fields["debt_release_status"]["value"] == status


def test_completion_is_explicit_only(monkeypatch):
    detection = MagicMock(return_value=SimpleNamespace(document_type="unknown", confidence=0.0,
                                                       source="unknown", reasoning=None, model_name=None))
    monkeypatch.setattr(router, "detect_document_type", detection)
    assert router.resolve_profile("external", service.PROFILE).profile_id == service.PROFILE
    detection.assert_not_called()
    router.resolve_profile("external", None, "судебный акт")
    assert service.PROFILE not in detection.call_args.args[1]


@pytest.mark.asyncio
@pytest.mark.parametrize("retry_missing_date", [False, True])
async def test_worker_and_get_use_the_same_completion_result(monkeypatch, tmp_path, retry_missing_date):
    from ocr_platform.api.main import app
    from ocr_platform.orchestration import run_processor as worker

    source = f"{HEADER}\n{HISTORY}\nОПРЕДЕЛИЛ:\n{START}\n{RELEASE}\n{MANAGER}\n{END}"
    path = tmp_path / "act.txt"
    path.write_text(source, encoding="utf-8")
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    models.Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(repository, "get_session", sessions)
    monkeypatch.setattr(repository, "init_db", lambda: None)
    monkeypatch.setattr(worker, "get_pipeline_tracer", MagicMock())
    monkeypatch.setattr(worker, "mlflow_run", lambda *a, **kw: nullcontext())
    for name in ["mlflow_set_tag", "mlflow_log_metric", "mlflow_log_param", "mlflow_log_text"]:
        monkeypatch.setattr(worker, name, MagicMock())
    first = evidence()
    responses = [SimpleNamespace(parsed=first, winner_model="test-model")]
    if retry_missing_date:
        responses.append(SimpleNamespace(parsed={"judicial_act_date": first["judicial_act_date"]},
                                         winner_model="test-model"))
        first["judicial_act_date"] = fact()
    gateway = MagicMock(side_effect=responses)
    monkeypatch.setattr(service, "call_llm_json_with_fallback", gateway)
    webhook = AsyncMock()
    monkeypatch.setattr(worker, "_trigger_webhook_safely", webhook)

    try:
        with sessions() as session:
            session.add(models.Document(id="completion-doc", source_type="external", document_type=service.PROFILE))
            session.add(models.DocumentFile(document_id="completion-doc", storage_path=str(path), file_type="text"))
            session.add(models.PipelineRun(id="completion-run", document_id="completion-doc", profile_id=service.PROFILE,
                                           status="queued", webhook_url="https://callback.invalid"))
            session.commit()
        await worker._process_pipeline_run_impl("completion-run")
        assert gateway.call_count == (2 if retry_missing_date else 1)
        assert all(call.kwargs["user_content"] == source for call in gateway.call_args_list)
        assert all(call.kwargs["models"] == ["google/gemini-4-argon"] for call in gateway.call_args_list)
        assert all(call.kwargs["response_schema"] == {"type": "json_object"} for call in gateway.call_args_list)
        if retry_missing_date:
            schema = json.loads(gateway.call_args.kwargs["system_prompt"].rsplit("\nJSON Schema ответа:\n", 1)[1])
            assert list(schema["properties"]) == ["judicial_act_date"]
        with TestClient(app) as client:
            response = client.get("/documents/completion-doc/result")
        assert response.status_code == 200
        payload = response.json()
        assert payload["fields"]["debtor_inn"]["value"] == "012345678901"
        assert payload["fields"]["judicial_act_date"]["value"] == "2026-01-31"
        assert payload["fields"]["debt_release_status"]["value"] == "applies"
        assert payload["fields"]["resolutive_part_text"]["value"] == f"{START}\n{RELEASE}\n{MANAGER}\n{END}"
        assert payload["validation_status"] == "ok" and payload["human_review_required"] is False
        sent = webhook.await_args.args[1]
        assert sent["fields"] == payload["fields"]
        assert sent["human_review_required"] is False
    finally:
        engine.dispose()

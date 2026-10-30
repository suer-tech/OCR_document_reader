"""Current-claim evidence and public delivery checks without external providers."""
import copy
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from ocr_platform.orchestration.router import load_profile
from ocr_platform.services import extraction_agent
from ocr_platform.services.rtk2_contract import case_field, guard_contract
from ocr_platform.services.validation_service import profile_field_issues, review_requirement

HEADER = "Арбитражный суд\nОпределение\nг. Ижевск Дело № А71- 8761/2026 Т/2\n04 сентября 2026 года\nСудья рассмотрев заявление\n"
FILING = "28.08.2026 Акционерное общество «Банк Русский Стандарт» обратилось в суд с заявлением о включении требований."
DUTY = "Должнику и финансовому управляющему представить в суд до 20.11.2026 отзыв на заявление, кредитору — результаты исполнительного производства (при наличии)."
TEXT = HEADER + "01 июня 2026 года должник обратился с заявлением о банкротстве. " + FILING + "\nОПРЕДЕЛИЛ:\n1. " + DUTY + "\n2. Требование будет рассмотрено без проведения судебного заседания не позднее 22.12.2026."


def fields():
    return {
        "case_number": {"value": None}, "decision_date": {"value": "17.08.2026"},
        "creditor_name": {"value": "Акционерное общество «Банк Русский Стандарт»", "evidence_quote": FILING},
        "creditor_application_date": {"value": "28.08.2026", "evidence_quote": FILING},
        "hearing_date": {"value": "22.12.2026"}, "review_required": {"value": True},
        "debtor_full_name": {"value": "Иванов Иван Иванович"},
        "procedure_type": {"value": "реализация имущества"},
        "financial_manager_full_name": {"value": "Петров Пётр Петрович"},
    }


def test_required_fields_match_profile_and_false_is_a_filled_value():
    from ocr_platform.services.rtk2_contract import REQUIRED_FIELDS
    from ocr_platform.services.quality_service import compute_quality_scores

    profile = load_profile("rtk2")
    assert set(REQUIRED_FIELDS) == {name for name, info in profile["fields"].items() if info.get("required")}
    assert compute_quality_scores("text", {"review_required": {"value": False}})[1] == 1.0


def test_customer_example_and_legacy_fields_preserved():
    before = fields()
    result = guard_contract(TEXT, copy.deepcopy(before))
    assert result["case_number"]["value"] == "А71-8761/2026"
    assert result["decision_date"]["value"] == "04.09.2026"
    assert result["creditor_name"]["value"] == before["creditor_name"]["value"]
    assert result["creditor_application_date"]["value"] == "28.08.2026"
    assert result["hearing_date"]["value"] is None
    assert result["review_required"]["value"] is True
    assert "20.11.2026 отзыв" in result["review_required"]["reasoning"]
    for name in ["debtor_full_name", "procedure_type", "financial_manager_full_name"]:
        assert result[name] == before[name]
    assert profile_field_issues(result, "rtk2") == []


@pytest.mark.parametrize("number", ["А71- 8761/2026 Т/2", "A71 — 8761/26 Т/3", "А71-8761/2026-12 «Б»"])
def test_main_case_has_no_dispute_suffix(number):
    assert case_field(f"Определение\nДело № {number}\nСудья рассмотрев заявление") ["value"] == "А71-8761/2026"


def test_header_case_not_body_reference_or_enforcement_document():
    text = "Определение\nСудья рассмотрев заявление. Дело № А99-999/2025. Исполнительный лист № 2-1008/2022."
    assert case_field(text)["value"] is None
    assert case_field(HEADER + text)["value"] == "А71-8761/2026"


def test_conflicting_header_numbers_require_review_instead_of_first_match():
    assert case_field("Дело № А71-8761/2026\nДело № А99-999/2025\nСудья") ["value"] is None


@pytest.mark.parametrize("ruling,expected", [
    ("Назначить судебное заседание на 15.12.2026.", "15.12.2026"),
    ("Рассмотрение требования назначить на «15» декабря 2026 года.", "15.12.2026"),
    ("Судебное заседание назначено на 15.12.2026.", "15.12.2026"),
    ("Назначить заявление к рассмотрению в судебном заседании на 15.12.2026.", "15.12.2026"),
    ("Судебное заседание будет назначено дополнительно.", None),
    ("Представить отзыв до 20.11.2026. Рассмотреть не позднее 22.12.2026.", None),
    ("Рассмотреть без судебного заседания не позднее 22.12.2026.", None),
    ("Назначить судебное заседание на 31.11.2026.", None),
])
def test_hearing_only_when_explicit_and_valid(ruling, expected):
    text = HEADER + "Ранее судебное заседание назначено на 10.10.2026.\nОПРЕДЕЛИЛ:\n" + ruling
    assert guard_contract(text, {"hearing_date": {"value": "04.10.2026"}})["hearing_date"]["value"] == expected


@pytest.mark.parametrize("ruling,expected", [
    ("Финансовому управляющему предоставить письменный отзыв.", True),
    ("Обязать должника направить возражения относительно требования кредитора.", True),
    ("Должник обязан представить отзыв.", True),
    ("Лица, участвующие в деле, вправе представить возражения.", False),
    ("Финансовый управляющий вправе представить возражения.", False),
    ("Заявления, ходатайства, отзывы и иные документы могут быть представлены через сайт суда.", False),
    ("Кредитору представить отзыв на апелляционную жалобу.", False),
    ("Должнику представить отзыв на апелляционную жалобу.", False),
    ("Должник не обязан представить отзыв.", False),
    ("Предложить финансовому управляющему представить отзыв.", None),
    ("Должнику при необходимости представить отзыв.", None),
    ("Финансовому управляющему представить пояснения.", None),
    ("Должнику представить возражения на отчёт управляющего.", None),
])
def test_review_requires_current_duty_not_keyword(ruling, expected):
    text = HEADER + "Ранее должнику представить отзыв.\nОПРЕДЕЛИЛ:\n" + ruling
    result = guard_contract(text, {"review_required": {"value": True}})
    assert result["review_required"]["value"] is expected
    assert ruling in result["review_required"]["reasoning"]
    if expected is None:
        assert review_requirement(1.0, profile_field_issues(result, "rtk2")) == (True, "rtk2_requires_review")


def test_explanations_substituting_for_review_have_grounded_semantic_evidence():
    quote = "Финансовому управляющему представить пояснения по требованию кредитора вместо отзыва."
    result = guard_contract("ОПРЕДЕЛИЛ:\n" + quote, {"review_required": {"value": True, "evidence_quote": quote}})
    assert result["review_required"]["value"] is True


@pytest.mark.parametrize("date,quote", [
    ("01.06.2026", "01 июня 2026 года должник обратился с заявлением о банкротстве."),
    ("20.08.2026", "Сообщение опубликовано в ЕФРСБ 20.08.2026."),
    ("04.09.2026", FILING),
    ("28.08.2026", "28.08.2026 ООО «Другой кредитор» обратилось с заявлением."),
    ("28.08.2026", "Придуманная цитата"),
])
def test_filing_date_cannot_be_borrowed_from_other_actor_or_event(date, quote):
    data = fields()
    data["creditor_application_date"] = {"value": date, "evidence_quote": quote}
    result = guard_contract(TEXT + quote, data)
    assert result["creditor_application_date"]["value"] is None
    assert review_requirement(1.0, profile_field_issues(result, "rtk2"))[0]


def test_missing_application_date_is_null_and_reviewable():
    data = fields()
    data["creditor_application_date"] = {"value": None}
    result = guard_contract(TEXT, data)
    assert result["creditor_application_date"]["value"] is None
    assert any(i.field_name == "creditor_application_date" for i in profile_field_issues(result, "rtk2"))


@pytest.mark.parametrize("value", ["29.07.2026", None])
@pytest.mark.parametrize("statement", [
    "рассмотрев заявление Акционерного общества «Банк Русский Стандарт» о включении требований (вх. № 69040 от 29.07.2026)",
    "29.07.2026 поступило заявление Акционерного общества «Банк Русский Стандарт» о включении требований.",
    "29.07.2026 зарегистрировано заявление Акционерного общества «Банк Русский Стандарт» о включении требований.",
])
def test_incoming_registration_never_substitutes_for_filing_date(statement, value):
    data = fields()
    data["creditor_application_date"] = {"value": value, "evidence_quote": statement}
    result = guard_contract(TEXT + statement, data)
    assert result["creditor_application_date"]["value"] is None
    assert "входящая" in result["creditor_application_date"]["reasoning"].lower()
    assert review_requirement(1.0, profile_field_issues(result, "rtk2")) == (True, "rtk2_requires_review")


@pytest.mark.parametrize("header_text", [
    "Определение\nСудья рассмотрев заявление. Определением от 01.06.2026...",
    "Определение\n31.11.2026\nСудья рассмотрев заявление...",
    "Определение\n04.09.2026\n05.09.2026\nСудья рассмотрев заявление...",
])
def test_missing_invalid_or_ambiguous_header_date_is_not_taken_from_body(header_text):
    result = guard_contract(header_text, {"decision_date": {"value": "01.06.2026"}})
    assert result["decision_date"]["value"] is None
    assert result["decision_date"].get("validation_issue")


def test_creditor_without_verbatim_evidence_is_not_accepted():
    data = fields()
    data["creditor_name"]["evidence_quote"] = "Акционерное общество «Банк Русский Стандарт» якобы подало требование."
    result = guard_contract(TEXT, data)
    assert result["creditor_name"]["value"] is None
    assert result["creditor_application_date"]["value"] is None


def test_grounded_creditor_does_not_retain_unsupported_succession_in_reasoning():
    data = fields()
    data["creditor_name"]["reasoning"] = "Другой банк — первоначальный кредитор, требования переданы заявителю."
    result = guard_contract(TEXT, data)
    assert result["creditor_name"]["value"] == data["creditor_name"]["value"]
    assert result["creditor_name"]["reasoning"] == f"Наименование по тексту рассматриваемого требования. Цитата: «{FILING}»."
    assert "первоначальный" not in result["creditor_name"]["reasoning"]


@pytest.mark.asyncio
async def test_combined_extraction_requests_new_fields_and_guards_stale_remote_prompts(monkeypatch):
    profile = load_profile("rtk2")
    data = {}
    for name, value in fields().items():
        data.update({name: value["value"], name + "_confidence": .95, name + "_reasoning": "Из текста"})
    data.update(creditor_name_quote=FILING, creditor_application_date_quote=FILING,
                review_required_quote=DUTY, has_text_distortions=False)
    validated = extraction_agent.Rtk2Result.model_validate(data)
    run = AsyncMock(return_value=SimpleNamespace(data=validated))
    monkeypatch.setattr(extraction_agent.agent_rtk2_combined, "run", run)
    monkeypatch.setattr(extraction_agent, "get_field_instruction", lambda *a, **kw: "old instruction")
    monkeypatch.setattr(extraction_agent, "_get_lf_client", lambda: None)
    result = await extraction_agent.run_agent_extraction(TEXT, profile["fields"], "rtk2", profile)
    assert set(result) == set(profile["fields"])
    assert result["case_number"]["value"] == "А71-8761/2026"
    assert result["creditor_application_date"]["value"] == "28.08.2026"
    assert run.await_count == 1
    assert "NEVER calculate" in run.call_args.args[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("review", [True, False, None])
async def test_worker_storage_webhook_and_get_share_rtk2_contract(monkeypatch, tmp_path, review):
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from ocr_platform.storage import models, repository
    from ocr_platform.orchestration import run_processor as worker

    original = guard_contract(TEXT, fields())
    original["review_required"] = {"value": review, "confidence": .95,
                                   "reasoning": "Неоднозначно" if review is None else "Цитата из акта"}
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    models.Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(repository, "get_session", sessions)
    monkeypatch.setattr(repository, "init_db", lambda: None)
    monkeypatch.setattr(worker, "get_pipeline_tracer", MagicMock())
    monkeypatch.setattr(worker, "mlflow_run", lambda *a, **kw: nullcontext())
    for name in ["mlflow_set_tag", "mlflow_log_metric", "mlflow_log_param", "mlflow_log_text"]:
        monkeypatch.setattr(worker, name, MagicMock())
    monkeypatch.setattr(worker.document_intel_service, "simple_extract_fields", AsyncMock(return_value=copy.deepcopy(original)))
    webhook = AsyncMock()
    monkeypatch.setattr(worker, "_trigger_webhook_safely", webhook)
    path = tmp_path / "ruling.txt"
    path.write_text(TEXT, encoding="utf-8")
    with sessions() as session:
        session.add(models.Document(id="doc-rtk2", source_type="external", document_type="rtk2"))
        session.add(models.DocumentFile(document_id="doc-rtk2", storage_path=str(path), file_type="text"))
        session.add(models.PipelineRun(id="run-rtk2", document_id="doc-rtk2", profile_id="rtk2", status="queued",
                                      webhook_url="https://callback.invalid"))
        session.commit()
    try:
        await worker._process_pipeline_run_impl("run-rtk2")
        sent = webhook.await_args.args[1]
        from ocr_platform.api.main import app
        with TestClient(app) as client:
            response = client.get("/documents/doc-rtk2/result")
        assert response.status_code == 200
        returned = response.json()
        for key in ["validation_status", "validation_issues", "human_review_required", "human_review_reason"]:
            assert sent[key] == returned[key]
        assert returned["human_review_required"] is (review is None)
        assert returned["validation_status"] == ("errors" if review is None else "ok")
        assert returned["fields"]["review_required"]["value"] is review
        assert set(original) <= set(returned["fields"])
        with sessions() as session:
            assert session.query(models.StructuredVersion).one().data == {
                **original, "processing_started_at": sent["fields"]["processing_started_at"]["value"]}
    finally:
        engine.dispose()

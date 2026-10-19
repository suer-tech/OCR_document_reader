import copy
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from ocr_platform.orchestration.router import load_profile
from ocr_platform.services import rtk3_evidence as service
from ocr_platform.services.validation_service import profile_field_issues, review_requirement, validate_fields


def fact(value, quote):
    return {"value": value, "quote": quote}


def fixture(components=None, total="227 065,76", duty="8 177"):
    components = components or [
        ("principal", "174 991,84", "174 991,84 руб. основного долга"),
        ("interest", "38 853,34", "38 853,34 руб. просроченных процентов"),
        ("sanction", "13 220,58", "13 220,58 руб. неустойки"),
    ]
    if duty:
        components = [*components, ("duty", duty, f"{duty} руб. государственной пошлины")]
    quotes = [c[2] for c in components]
    decision = f"Включить в третью очередь требования в размере {total} руб.: " + "; ".join(quotes)
    header = "Дело № А15-4245-1/2026. Определение 02 октября 2026 года."
    debtor = "должника гражданина Тестова (ИНН 012345678901)"
    creditor = "кредитора ООО «Тест» (ИНН 1234567890)"
    publication = "Дата публикации в КАД: 05.10.2026"
    grounds = "задолженность по кредитному договору"
    text = "\n".join([header, debtor, creditor, publication, grounds, decision])
    payload = {
        "document_case_number": fact("А15-4245-1/2026", header),
        "debtor_inn": fact("012345678901", debtor),
        "creditor": fact("ООО «Тест»", creditor),
        "creditor_inn": fact("1234567890", creditor),
        "inclusion_date": fact("02.10.2026", header),
        "kad_publication_date": fact("05.10.2026", publication),
        "grounds": fact("Кредитный договор", grounds),
        "claims_complete": True, "uncertainty": "",
        "claims": [{
            "priority_queue": fact("3 очередь", "третью очередь"),
            "decision_quote": decision,
            "breakdown_quote": "",
            "components": [{"kind": kind, "amount": amount, "quote": quote} for kind, amount, quote in components],
            "components_complete": True, "included_total": fact(total, decision),
            "total_scope": "without_duty", "pledge_resolution": "no", "pledge_quote": "",
            "is_salary_or_severance": False, "salary_quote": "", "uncertainty": "",
        }],
    }
    return payload, text


def build(payload, text):
    return service.build_fields(service.DocumentEvidence.model_validate(payload), text, load_profile("rtk3"))


def test_1843_interest_and_duty_follow_confirmed_emu_semantics():
    payload, text = fixture()
    fields = build(payload, text)
    assert fields["claims"]["value"] == [{
        "priority_queue": "3 очередь", "principal_debt": "213845.18", "financial_sanctions": "13220.58",
        "pau_only_amount": "8177.00", "claimed_amount": "227065.76", "total_amount": "235242.76",
        "is_salary_or_severance": False,
    }]
    assert fields["total_claimed_amount"]["value"] == "227065.76"
    assert fields["debtor_inn"]["value"] == "012345678901"
    assert fields["case_number"]["value"] == "А15-4245/2026"
    assert fields["document_case_number"]["value"] == "А15-4245-1/2026"
    assert validate_fields(fields, "rtk3", load_profile("rtk3")) == ("ok", [])
    assert review_requirement(0.99, profile_field_issues(fields, "rtk3")) == (False, None)
    assert not {"efrsb_total_amount", "pau_total_amount", "amount_mismatch_confirmed"} & fields.keys()


@pytest.mark.parametrize("value,expected", [
    ("1 000 руб. 50 коп.", "1000.50"), ("1000 рублей 5 копеек", "1000.05"),
    ("2\u00a0298,2", "2298.20"), ("46 705,66", "46705.66"), ("0", "0.00"),
    ("-1", None), (True, None), ("NaN", None), ("29.74543", None), ("дело № 123", None),
])
def test_exact_money_no_rounding_or_lost_thousands(value, expected):
    actual = service.money(value)
    assert (format(actual, ".2f") if actual is not None else None) == expected


def test_1863_all_penalties_are_added_not_just_first():
    payload, text = fixture([
        ("principal", "898864,61", "898864,61 руб. основной долг"),
        ("interest", "116819,53", "116819,53 руб. просроченные проценты"),
        ("sanction", "6294,38", "6294,38 руб. неустойка за основной долг"),
        ("sanction", "9082,78", "9082,78 руб. неустойка за проценты"),
    ], total="1031061,3", duty="27 966")
    row = build(payload, text)["claims"]["value"][0]
    assert row["principal_debt"] == "1015684.14"
    assert row["financial_sanctions"] == "15377.16"
    assert row["claimed_amount"] == "1031061.30"
    assert row["total_amount"] == "1059027.30"


@pytest.mark.parametrize("bad_kind", ["missing", "fabricated", "duplicate", "wrong_total", "wrong_role", "refund"])
def test_ambiguous_or_unsupported_components_are_held_for_review(bad_kind):
    payload, text = fixture()
    claim = payload["claims"][0]
    if bad_kind == "missing":
        claim["components"].pop(1)
    elif bad_kind == "fabricated":
        claim["components"][0]["amount"] = "999999.99"
    elif bad_kind == "duplicate":
        claim["components"].append(copy.deepcopy(claim["components"][0]))
    elif bad_kind == "wrong_total":
        claim["included_total"] = fact("999999.99", claim["decision_quote"])
    elif bad_kind == "wrong_role":
        claim["components"][2]["kind"] = "interest"
    else:
        old = claim["components"][-1]["quote"]
        quote = "Вернуть " + old
        text = text.replace(old, quote)
        claim["decision_quote"] = claim["decision_quote"].replace(old, quote)
        claim["included_total"]["quote"] = claim["decision_quote"]
        claim["components"][-1]["quote"] = quote
    fields = build(payload, text)
    assert fields["total_claimed_amount"]["value"] is None
    assert fields["claims"]["value"][0]["principal_debt"] is None
    assert review_requirement(0.99, profile_field_issues(fields, "rtk3"))[0]


def test_inn_is_bound_to_one_subject_and_never_padded():
    payload, text = fixture()
    payload["debtor_inn"] = fact("1234567890", payload["creditor_inn"]["quote"])
    fields = build(payload, text)
    assert fields["debtor_inn"]["value"] is None
    payload, text = fixture()
    shared = payload["debtor_inn"]["quote"] + " и " + payload["creditor_inn"]["quote"]
    payload["debtor_inn"]["quote"] = shared
    fields = build(payload, text + "\n" + shared)
    assert fields["debtor_inn"]["value"] is None


def test_rewritten_creditor_quote_recovers_only_with_same_admitted_party():
    payload, text = fixture()
    claim = payload["claims"][0]
    claim["decision_quote"] += " Требование кредитора ООО «Тест»."
    claim["included_total"]["quote"] = claim["decision_quote"]
    text += " Требование кредитора ООО «Тест»."
    payload["creditor"] = fact("ООО Тест", "кредитор ООО Тест — ИНН 1234567890")
    fields = build(payload, text)
    assert fields["creditor"]["value"] == "ООО «Тест»"
    assert fields["creditor_inn"]["value"] == "1234567890"
    assert "ИНН 1234567890" in fields["creditor"]["reasoning"]
    payload["creditor"] = fact("ООО Другой", "кредитор ООО Другой — ИНН 1111111111")
    text += " ООО «Другой» (ИНН 1111111111)."
    assert build(payload, text)["creditor"]["value"] is None


def test_model_date_cannot_override_current_heading_or_kad_label():
    payload, text = fixture()
    text += " Заседание назначено на 09.10.2026."
    payload["inclusion_date"] = fact("09.10.2026", "Заседание назначено на 09.10.2026.")
    payload["kad_publication_date"] = payload["inclusion_date"].copy()
    fields = build(payload, text)
    assert fields["inclusion_date"]["value"] == "02.10.2026"
    assert fields["kad_publication_date"]["value"] == "05.10.2026"


def test_name_quote_accepts_immediately_adjacent_debtor_role_and_ignores_snils():
    payload, text = fixture()
    quote = "гражданина Тестова (ИНН 012345678901)"
    payload["debtor_inn"] = fact("012345678901", quote)
    assert build(payload, text)["debtor_inn"]["value"] == "012345678901"
    # The first occurrence may lack a role; the operative repetition has it.
    quote = "Тестова (ИНН 012345678901)"
    text = quote + "\n" + text + "\nдолжника — " + quote
    payload["debtor_inn"] = fact("012345678901", quote)
    assert build(payload, text)["debtor_inn"]["value"] == "012345678901"
    payload, text = fixture()
    quote = "Тестова (ИНН 012345678901), СНИЛС 12345678901"
    text += "\nдолжника " + quote
    payload["debtor_inn"] = fact("012345678901", quote)
    assert build(payload, text)["debtor_inn"]["value"] == "012345678901"


def test_standalone_verified_duty_is_attached_only_to_single_related_claim():
    payload, text = fixture(duty="")
    decision = "Учесть 8 177 руб. государственной пошлины отдельно."
    duty = copy.deepcopy(payload["claims"][0])
    duty.update(priority_queue=fact("Иное", "отдельно"), decision_quote=decision,
                components=[{"kind": "duty", "amount": "8 177", "quote": "8 177 руб. государственной пошлины"}],
                included_total=fact("8 177", decision), total_scope="with_duty")
    payload["claims"].append(duty)
    fields = build(payload, text + "\n" + decision)
    assert len(fields["claims"]["value"]) == 1
    assert fields["claims"]["value"][0]["pau_only_amount"] == "8177.00"
    assert fields["claims"]["value"][0]["total_amount"] == "235242.76"
    assert fields["total_claimed_amount"]["value"] == "227065.76"


def test_standalone_fee_does_not_cancel_secured_principal_status():
    payload, text = fixture(duty="")
    claim = payload["claims"][0]
    pledge = "как обеспеченное залогом транспортного средства"
    claim["decision_quote"] += "; " + pledge
    claim["included_total"]["quote"] = claim["decision_quote"]
    claim.update(pledge_resolution="yes", pledge_quote=pledge)
    text = text[:text.index("Включить")] + claim["decision_quote"]
    decision = "Учесть 86046 руб. государственной пошлины отдельно."
    duty = copy.deepcopy(claim)
    duty.update(priority_queue=fact("Иное", "отдельно"), decision_quote=decision,
                components=[{"kind": "duty", "amount": "86046", "quote": "86046 руб."}],
                included_total=fact("86046", decision), total_scope="with_duty",
                pledge_resolution="no", pledge_quote="")
    payload["claims"].append(duty)
    fields = build(payload, text + "\n" + decision)
    assert len(fields["claims"]["value"]) == 1
    assert fields["claims"]["value"][0]["pau_only_amount"] == "86046.00"
    assert fields["secured_by_pledge"]["value"] is True


def test_omitted_fee_requires_literal_award_and_same_named_creditor():
    payload, text = fixture(duty="")
    quote = ("Взыскать с должника в пользу ООО «Тест» расходы по оплате государственной пошлины "
             "в размере 11 932 руб. и признать данные расходы обоснованными в порядке пункта 3 статьи 137.")
    assert service.separately_awarded_fees(quote)[0][0] == service.money("11932")
    unrelated = quote.replace("и признать данные расходы обоснованными", "Включить другое требование")
    assert service.separately_awarded_fees(unrelated) == []
    result = build(payload, text + "\n" + quote)
    assert result["claims"]["value"][0]["pau_only_amount"] == "11932.00"
    other = build(payload, text + "\n" + quote.replace("«Тест»", "«Другой»"))
    assert other["claims"]["value"][0]["pau_only_amount"] == "0.00"


def test_advisory_uncertainty_keeps_proven_amounts_but_requires_review():
    payload, text = fixture()
    payload["claims"][0]["uncertainty"] = "Пошлина учитывается отдельно от суммы ЕФРСБ."
    fields = build(payload, text)
    assert fields["total_claimed_amount"]["value"] == "227065.76"
    assert fields["claims"]["value"][0]["total_amount"] == "235242.76"
    assert review_requirement(0.99, profile_field_issues(fields, "rtk3"))[0]


def test_no_explicit_gross_total_keeps_supported_components_with_review():
    payload, text = fixture()
    payload["claims"][0]["included_total"] = fact(None, "")
    result = build(payload, text)
    assert result["claims"]["value"][0]["principal_debt"] == "213845.18"
    assert result["total_claimed_amount"]["value"] == "227065.76"
    assert review_requirement(0.99, profile_field_issues(result, "rtk3"))[0]


@pytest.mark.parametrize("wrong_link", [False, True])
def test_2332_earlier_breakdown_must_match_admitted_total(wrong_link):
    payload, text = fixture([
        ("principal", "450 848,07", "450 848,07 руб. – просроченный основной долг"),
        ("interest", "50 468,36", "50 468,36 руб. – просроченные проценты"),
        ("sanction", "4 068,70", "4 068,70 руб. - неустойка"),
    ], total="505 385,13", duty="11 932")
    claim = payload["claims"][0]
    body_total = "505 385,13" if not wrong_link else "1 001 008,01"
    body = f"По договору №1427470 задолженность {body_total} рублей: " + "; ".join(
        c["quote"] for c in claim["components"] if c["kind"] != "duty")
    decision = ("Включить в третью очередь требования в размере 505 385,13 руб. "
                "Взыскать 11 932 руб. государственной пошлины.")
    excluded = ("Производство по заявлению о включении задолженности по договору №347971 "
                "в сумме 1 001 008,01 руб. и признании требования обеспеченным залогом прекратить.")
    claim.update(decision_quote=decision, breakdown_quote=body,
                 included_total=fact("505 385,13", decision))
    text = text[:text.index("Включить")] + body + "\n" + decision + "\n" + excluded
    result = build(payload, text)
    row = result["claims"]["value"][0]
    if wrong_link:
        assert row["principal_debt"] is None
        assert review_requirement(0.99, profile_field_issues(result, "rtk3"))[0]
    else:
        assert row["principal_debt"] == "501316.43"
        assert row["financial_sanctions"] == "4068.70"
        assert row["total_amount"] == "517317.13"
        assert result["secured_by_pledge"]["value"] is False


def test_2064_current_fee_is_excluded_not_added_twice():
    payload, text = fixture([
        ("principal", "14 995,38", "14 995,38 руб. основной долг"),
        ("interest", "5 137,18", "5 137,18 руб. проценты"),
        ("sanction", "312,17", "312,17 руб. неустойка"),
    ], total="20 444,73", duty="")
    claim = payload["claims"][0]
    claim["decision_quote"] = claim["decision_quote"].replace("в третью очередь", "за реестром")
    claim["priority_queue"] = fact("За реестром", "за реестром")
    claim["included_total"] = fact("20 444,73", claim["decision_quote"])
    text = text[:text.index("Включить")] + claim["decision_quote"]
    excluded = ("Прекратить производство по заявлению в части включения "
                "2000 рублей государственной пошлины (является текущим платежом).")
    text += "\n" + excluded
    row = build(payload, text)["claims"]["value"][0]
    assert row["principal_debt"] == "20132.56"
    assert row["claimed_amount"] == "20444.73"
    assert row["pau_only_amount"] == "0.00"
    claim["components"].append({"kind": "duty", "amount": "2000",
                                "quote": "2000 рублей государственной пошлины"})
    # Even if the model includes the excluded paragraph in its decision excerpt,
    # literal support must not make that current fee an admitted component.
    claim["decision_quote"] += "\n" + excluded
    claim["included_total"]["quote"] = claim["decision_quote"]
    assert build(payload, text)["claims"]["value"][0]["total_amount"] is None


def test_prior_awarded_cost_is_in_admitted_principal_once():
    payload, text = fixture([
        ("principal", "4500", "4500 руб. основного долга"),
        ("interest", "6470,89", "6470,89 руб. процентов"),
        ("sanction", "279,11", "279,11 руб. неустойки"),
        ("prior_cost", "225", "225 руб. государственной пошлины"),
    ], total="11475", duty="")
    suffix = " Требование установлено судебным приказом от 01.02.2025."
    text += suffix
    claim = payload["claims"][0]
    claim["decision_quote"] += suffix
    claim["included_total"]["quote"] += suffix
    row = build(payload, text)["claims"]["value"][0]
    assert row["principal_debt"] == "11195.89"
    assert row["financial_sanctions"] == "279.11"
    assert row["pau_only_amount"] == "0.00"
    assert row["total_amount"] == "11475.00"


def test_dismissed_mortgage_does_not_cancel_included_vehicle_pledge():
    payload, text = fixture()
    excluded = "Прекратить производство по ипотечному требованию в размере 5768697,95 руб."
    pledge = "как требования, обеспеченные залогом транспортного средства"
    claim = payload["claims"][0]
    claim["decision_quote"] += "; " + pledge
    claim["included_total"]["quote"] = claim["decision_quote"]
    claim.update(pledge_resolution="yes", pledge_quote=pledge)
    text = text[:text.index("Включить")] + excluded + "\n" + claim["decision_quote"]
    result = build(payload, text)
    assert result["secured_by_pledge"]["value"] is True
    assert result["total_claimed_amount"]["value"] == "227065.76"


@pytest.mark.parametrize("resolution,wording,valid", [
    ("yes", "как обеспеченные залогом", True),
    ("yes", "как необеспеченные залогом", False),
    ("no", "как не обеспеченные залогом", True),
    ("separated", "вопрос о залоге рассмотреть отдельно", True),
    ("unknown", "", False),
])
def test_final_pledge_status_not_just_word_pledge(resolution, wording, valid):
    payload, text = fixture()
    claim = payload["claims"][0]
    suffix = "; " + wording
    text += suffix
    claim["decision_quote"] += suffix
    claim["included_total"]["quote"] += suffix
    claim.update(pledge_resolution=resolution, pledge_quote=wording)
    result = build(payload, text)
    if valid:
        assert profile_field_issues(result, "rtk3") == []
        assert result["secured_by_pledge"]["value"] is (resolution == "yes")
    else:
        assert review_requirement(0.99, profile_field_issues(result, "rtk3"))[0]


def test_kad_is_not_ruling_or_signature_date_and_forces_review():
    payload, text = fixture()
    payload["kad_publication_date"] = payload["inclusion_date"].copy()
    text = text.replace("Дата публикации в КАД: 05.10.2026", "Электронная подпись: 05.10.2026")
    fields = build(payload, text)
    assert fields["kad_publication_date"]["value"] is None
    assert review_requirement(0.99, profile_field_issues(fields, "rtk3"))[0]


@pytest.mark.parametrize("value,document,main", [
    ("A 15 - 1277 - 1 / 26", "А15-1277-1/2026", "А15-1277/2026"),
    ("А56-21012/2026/тр.2", "А56-21012/2026/тр.2", "А56-21012/2026"),
    ("А63 – 26875/2025", "А63-26875/2025", "А63-26875/2025"),
])
def test_dispute_suffixes_are_not_lost(value, document, main):
    assert service.normalize_case(value) == (document, main)


def test_two_queues_stay_separate_and_order_does_not_change_totals():
    payload, text = fixture()
    second, extra = fixture([("principal", "100", "100 руб. основного долга")], "100", duty="")
    claim = second["claims"][0]
    claim["priority_queue"] = fact("2 очередь", "вторую очередь")
    claim["decision_quote"] = claim["decision_quote"].replace("третью", "вторую")
    claim["included_total"]["quote"] = claim["decision_quote"]
    payload["claims"].append(claim)
    text += "\n" + claim["decision_quote"]
    result = build(payload, text)
    assert [r["priority_queue"] for r in result["claims"]["value"]] == ["3 очередь", "2 очередь"]
    assert result["total_claimed_amount"]["value"] == "227165.76"
    assert profile_field_issues(result, "rtk3") == []
    payload["claims"].reverse()
    assert build(payload, text)["total_claimed_amount"] == result["total_claimed_amount"]


def test_legacy_arithmetic_error_forces_review_without_mutation():
    old = {"claims": {"value": [{"principal_debt": 147496.3, "financial_sanctions": 46.71,
                                "total_amount": 194201.96}]}}
    snapshot = copy.deepcopy(old)
    issues = profile_field_issues(old, "rtk3")
    assert review_requirement(0.99, issues)[0]
    assert old == snapshot
    assert profile_field_issues(old, "rtk") == []


@pytest.mark.asyncio
async def test_public_dispatch_uses_profile_model_and_one_gateway_call(monkeypatch):
    payload, text = fixture()
    gateway = MagicMock(return_value=SimpleNamespace(parsed=payload))
    monkeypatch.setattr(service, "call_llm_json_with_fallback", gateway)
    monkeypatch.setattr(service, "get_settings", lambda: SimpleNamespace(
        router_ai_api_key="test-router-key", router_ai_base_url="https://router.invalid/v1"))
    from ocr_platform.services.document_intel_service import simple_extract_fields
    fields = await simple_extract_fields(text, load_profile("rtk3"), "rtk3", "run", "doc")
    assert fields["total_claimed_amount"]["value"] == "227065.76"
    gateway.assert_called_once()
    assert gateway.call_args.kwargs["models"] == ["anthropic/claude-sonnet-5.5"]
    assert gateway.call_args.kwargs["user_content"] == text
    assert gateway.call_args.kwargs["api_key"] == "test-router-key"
    assert gateway.call_args.kwargs["base_url"] == "https://router.invalid/v1"


@pytest.mark.asyncio
async def test_schema_failure_is_reviewable_not_individual_guessing(monkeypatch):
    monkeypatch.setattr(service, "call_llm_json_with_fallback", MagicMock(return_value=SimpleNamespace(parsed={"bad": True})))
    fields = await service.extract_rtk3("document", load_profile("rtk3"))
    assert all(info["value"] is None for info in fields.values())
    assert review_requirement(1.0, profile_field_issues(fields, "rtk3"))[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("missing_kad", [False, True])
async def test_v2_worker_storage_webhook_and_get_share_validation(monkeypatch, tmp_path, missing_kad):
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from unittest.mock import AsyncMock
    from ocr_platform.storage import models, repository
    from ocr_platform.orchestration import run_processor as worker

    payload, text = fixture()
    if missing_kad:
        payload["kad_publication_date"] = fact(None, "")
        text = text.replace("Дата публикации в КАД: 05.10.2026", "")
    original = build(payload, text)
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    models.Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(repository, "get_session", sessions)
    monkeypatch.setattr(repository, "init_db", lambda: None)
    monkeypatch.setattr(worker, "get_pipeline_tracer", MagicMock())
    monkeypatch.setattr(worker, "mlflow_run", lambda *a, **kw: nullcontext())
    for name in ["mlflow_set_tag", "mlflow_log_metric", "mlflow_log_param", "mlflow_log_text"]:
        monkeypatch.setattr(worker, name, MagicMock())
    monkeypatch.setattr(worker.document_intel_service, "simple_extract_fields",
                        AsyncMock(return_value=copy.deepcopy(original)))
    webhook = AsyncMock()
    monkeypatch.setattr(worker, "_trigger_webhook_safely", webhook)
    path = tmp_path / "ruling.txt"
    path.write_text(text, encoding="utf-8")
    with sessions() as session:
        session.add(models.Document(id="doc-v2", source_type="external", document_type="rtk3"))
        session.add(models.DocumentFile(document_id="doc-v2", storage_path=str(path), file_type="text"))
        session.add(models.PipelineRun(id="run-v2", document_id="doc-v2", profile_id="rtk3", status="queued",
                                       webhook_url="https://callback.invalid"))
        session.commit()
    try:
        await worker._process_pipeline_run_impl("run-v2")
        sent = webhook.await_args.args[1]
        from ocr_platform.api.main import app
        with TestClient(app) as client:
            response = client.get("/documents/doc-v2/result")
        assert response.status_code == 200
        returned = response.json()
        for key in ["validation_status", "validation_issues", "human_review_required", "human_review_reason"]:
            assert sent[key] == returned[key]
        assert returned["human_review_required"] is missing_kad
        assert returned["validation_status"] == ("errors" if missing_kad else "ok")
        assert returned["fields"]["claims"]["value"][0]["total_amount"] == "235242.76"
        assert returned["fields"]["debtor_inn"]["value"] == "012345678901"
        with sessions() as session:
            assert session.query(models.StructuredVersion).one().data == {
                **original, "processing_started_at": sent["fields"]["processing_started_at"]["value"]}
    finally:
        engine.dispose()

import json
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from ocr_platform.orchestration.router import load_profile
from ocr_platform.services import rtk3_regex_jev as service
from ocr_platform.services.rtk3_regex_candidates import Salary, extract_candidates


ACT = """Дело № А40-123/2026-1-2Ф
Полный текст определения изготовлен 19 марта 2026 года.
ОПРЕДЕЛИЛ:
Включить в третью очередь реестра требований кредиторов гражданина
Петрова Ивана Ивановича (ИНН 012345678901) требование ПАО «Пример» (ИНН 0123456789)
по кредитному договору № 1406/ПК от 21.11.2025 в размере 108 руб.,
в том числе 100 руб. основного долга и 8 руб. пеней."""
ORDER = ACT.split("ОПРЕДЕЛИЛ:\n", 1)[1]
CREDITOR = {"value": "ПАО «Пример»", "quote": "требование ПАО «Пример»"}
CREDITOR_INN = {"value": "0123456789", "quote": "требование ПАО «Пример» (ИНН 0123456789)"}


@pytest.mark.parametrize("heading,document,main", [
    ("г. Краснодар Дело№А32-321/2026", "А32-321/2026", "А32-321/2026"),
    ("Дело № А40-219/26-9-325 Ф", "А40-219/2026-9-325Ф", "А40-219/2026"),
    ("Дело № А56-219/2026/тр.2", "А56-219/2026/тр.2", "А56-219/2026"),
    ("Дело № А21-219-1/2026", "А21-219-1/2026", "А21-219/2026"),
    ("Дело № А21-219/2026; дело № А21-220/2026", None, None),
    ("", None, None),
])
def test_current_heading_survives_missing_model_reply(heading, document, main):
    text = heading + "\nРассмотрев заявление, суд установил: по делу № А13-900/2023 ранее взысканы расходы."
    fields = service._scalar_fields(text, {"document_case_number": {}, "case_number": {}}, {}, {}, {})
    assert (fields["document_case_number"]["value"], fields["case_number"]["value"]) == (document, main)
    if document:
        assert fields["document_case_number"]["reasoning"] in heading


@pytest.mark.parametrize("decision,answer,ending,expected", [
    ("Включить требование банка в реестр.", "no", "Судья Иванов", "no"),
    ("Включить требование банка в реестр.", "no", "", None),
    ("Включить требование банка как обеспеченное залогом квартиры.", "yes", "Судья Иванов", "yes"),
    ("В признании требования обеспеченным залогом имущества отказать.", "no", "Судья Иванов", "no"),
    ("Выделить вопрос о залоге в отдельное производство.", "separated", "Судья Иванов", "separated"),
    ("Включить требование банка в реестр.", "yes", "Судья Иванов", None),
])
def test_document_pledge_from_argon_without_amount_rows(decision, answer, ending, expected):
    text = f"ОПРЕДЕЛИЛ:\n{decision}\n{ending}"
    evidence = service.PledgeFact(value=answer, quote=decision)
    assert service._document_pledge(evidence, text, []) == expected


def test_document_pledge_does_not_transfer_from_another_claim():
    decision = "Признать требование банка обеспеченным залогом."
    evidence = service.PledgeFact(value="yes", quote=decision)
    assert service._document_pledge(evidence, "Банк просил суд: " + decision + "\nОПРЕДЕЛИЛ: отказать.", []) is None
    assert service._document_pledge(evidence, "ОПРЕДЕЛИЛ:\n" + decision, [{"pledge_resolution": "no"}]) is None


@pytest.mark.parametrize("name,quote,expected", [
    ("ООО «Профессиональная коллекторская организация «Пример»",
     "требование ООО «Профессиональная коллекторская организация «Пример»", "ООО ПКО «Пример»"),
    ("ООО коллекторское бюро «Пример»", "требования ООО коллекторского бюро «Пример»", "ООО коллекторское бюро «Пример»"),
    ("ПАО СБЕРБАНК", "требование ПАО СБЕРБАНК", "ПАО «Сбербанк»"),
    ("НАО ПКО «Пример»", "требование непубличного акционерного общества ПКО «Пример»", "НАО ПКО «Пример»"),
    ("НАО ПКО «Пример»", "требование публичного акционерного общества ПКО «Пример»", None),
])
def test_source_bound_argon_creditor_format(name, quote, expected):
    assert service._model_fact("creditor", service.Fact(value=name, quote=quote), quote, None) == expected


@pytest.mark.asyncio
async def test_truncated_contracts_retry_independent_document_facts(monkeypatch):
    decision = "Включить требование ПАО «Пример» в третью очередь реестра требований."
    text = "Дело № А42-123-2/2026\nОПРЕДЕЛИЛ:\n" + decision + "\nСудья Иванов"
    calls = []

    def gateway(**args):
        kind = args["request_type"]
        calls.append(kind)
        if kind == "rtk3_document_facts_retry":
            assert set(args["response_schema"]["json_schema"]["schema"]["properties"]) == {
                "creditor", "creditor_inn", "document_pledge",
            }
            return SimpleNamespace(parsed={
                "creditor": {"value": "ПАО «Пример»", "quote": decision},
                "document_pledge": {"value": "no", "quote": decision},
            })
        return None

    monkeypatch.setattr(service, "call_llm_json_with_fallback", gateway)
    monkeypatch.setattr(service, "_enrich_creditor_inn", AsyncMock())
    fields = await service.extract_fields(text, load_profile("rtk3"))
    assert calls == ["decisions", "rtk3_components", "rtk3_document_facts_retry"]
    assert fields["claims"]["value"] == []
    assert fields["pledge_resolution"]["value"] == "no"
    assert fields["pledge_resolution"]["extraction_route"] == "gemini_argon"
    assert fields["creditor"]["value"] == "ПАО «Пример»"
    assert fields["document_case_number"]["value"] == "А42-123-2/2026"

PENALTY = {"kind": "sanction", "amount": "8 руб.", "quote": "8 руб. пеней"}
MONEY_KEYS = {f"claim_1_{name}" for name in ("principal_debt", "financial_sanctions", "pau_only_amount", "included_total")}
MODEL_CLAIM = {
    "priority_queue": {"value": "3 очередь", "quote": "третью очередь"},
    "decision_quote": ORDER,
    "breakdown_quote": "",
    "components": [
        {"kind": "principal", "amount": "100 руб.", "quote": "100 руб. основного долга"},
        PENALTY,
    ],
    "components_complete": True,
    "included_total": {"value": "108 руб.", "quote": "108 руб."},
    "total_scope": "without_duty",
    "pledge_resolution": "unknown",
    "pledge_quote": "",
    "is_salary_or_severance": False,
    "salary_quote": "по кредитному договору № 1406/ПК от 21.11.2025",
    "uncertainty": "",
}


@pytest.mark.parametrize("quote,proposed,expected", [
    ("по кредитному договору № 1", "Кредитный договор", "Кредитный договор"),
    ("по кредитным договорам № 1 и № 2", "Кредитный договор", "Кредитный договор"),
    ("по договору о предоставлении потребительского кредита (займа)", "Кредитный договор", "Кредитный договор"),
    ("проценты за пользование кредитом по договору банковского счета", "Кредитный договор", "Кредитный договор"),
    ("по договору банковского счета с правом овердрафта", "Кредитный договор", "Кредитный договор"),
    ("должник получил кредитные денежные средства", "Кредитный договор", "Кредитный договор"),
    ("Включить требование ПАО «Примербанк»: основной долг и проценты", "Кредитный договор", "Кредитный договор"),
    ("Включить требование ПАО «Примербанк» в сумме 100 руб.", "Кредитный договор", None),
    ("Включить основной долг и проценты за пользование чужими денежными средствами в пользу ПАО «Примербанк»",
     "Кредитный договор", None),
    ("Включить требование коллектора: основной долг и проценты", "Кредитный договор", None),
    ("проценты за пользование займом", "Договор займа", "Договор займа"),
    ("по договору микрозайма", "Кредитный договор", None),
    ("по уплате обязательных платежей", "Налоговая задолженность", "Налоговая задолженность"),
    ("по оплате коммунальных услуг", "Оплата коммунальных услуг", "Оплата коммунальных услуг"),
    ("по судебному приказу № 1", "Кредитный договор", None),
], ids=["one-contract", "several-contracts", "consumer-credit-loan", "interest-on-credit",
        "overdraft", "credit-funds", "bank-principal-interest", "bank-only", "civil-interest",
        "collector-only", "loan-interest", "microloan", "tax", "utilities", "enforcement-order"])
def test_argon_ground_uses_origin_of_obligation(quote, proposed, expected):
    fact = service.Fact(value=proposed, quote=quote)
    assert service._model_fact("grounds", fact, quote, "ПАО «Примербанк»") == expected


@pytest.mark.parametrize("mode", ["fixed", "full"])
@pytest.mark.parametrize("extra,decision,expected", [
    ("", False, False),
    ("Упомянут кредитный договор № 222.", False, None),
    ("Рассмотрена также задолженность по заработной плате.", False, None),
    ("", None, None),
])
def test_argon_salary_evidence_in_single_contract_header(mode, extra, decision, expected):
    header = MODEL_CLAIM["salary_quote"]
    order = ORDER.replace(header, "")
    text = f"Заявление кредитора {header}. {extra}\nОПРЕДЕЛИЛ:\n{order}"
    response = {"grounds": service.Fact(value="Кредитный договор", quote=header)}
    if mode == "fixed":
        response["claim_1_is_salary_or_severance"] = Salary(value=decision, quote=header)
        result = service._fixed_claims([service.Claim(order, {}, None)], {}, response, text)
    else:
        claim = service.ClaimEvidence.model_validate({
            **MODEL_CLAIM, "decision_quote": order, "is_salary_or_severance": decision,
        })
        result = service._model_claims(text, load_profile("rtk3"), {
            **response, "claims": [claim], "claims_complete": True,
        }, {})
    assert result["claims"]["value"][0]["is_salary_or_severance"] is expected


@pytest.mark.parametrize("answer", ["recovered", "empty", "unquoted", "wrong_total", "already_known"])
@pytest.mark.asyncio
async def test_targeted_principal_retry(monkeypatch, answer):
    principal = MODEL_CLAIM["components"][0]
    known = answer == "already_known"
    key = "claim_1_principal_debt"
    calls = []

    def gateway(**request):
        kind = request["request_type"]
        calls.append(kind)
        if kind == "decisions":
            return SimpleNamespace(parsed={"answers": {
                name: {"type": "noul", "noul": 1.0}
                for name in request["decisions_questions"]
            }})
        if kind == "rtk3_principal_retry":
            assert set(request["response_schema"]["json_schema"]["schema"]["properties"]) == {key}
            assert "после проверки оно осталось пустым" in request["system_prompt"]
            body = json.loads(request["user_content"])
            assert len(body["previous_argon_row_quotes"]) == 1 and body["rows_after_validation"][0]["principal_debt"] is None
            item = dict(principal)
            if answer == "unquoted":
                item["quote"] = "неподтверждённые 100 руб. основного долга"
            if answer == "wrong_total":
                item.update(amount="108 руб.", quote=ORDER)
            return SimpleNamespace(parsed={key: None if answer == "empty" else {
                "items": [item], "complete": True, "quote": ORDER,
            }, "claim_1_financial_sanctions": None})
        return SimpleNamespace(parsed={"claims_complete": True, "uncertainty": "", "claims": [{
            **MODEL_CLAIM, "components": [principal, PENALTY] if known else [PENALTY],
        }]})

    monkeypatch.setattr(service, "call_llm_json_with_fallback", gateway)
    fields = await service.extract_fields(ACT, load_profile("rtk3"))
    assert calls == ["decisions", "rtk3_components"] + ([] if known else ["rtk3_principal_retry"])
    row, = fields["claims"]["value"]
    if known or answer == "recovered":
        assert (row["principal_debt"], row["financial_sanctions"], row["pau_only_amount"]) == ("100.00", "8.00", "0.00")
        assert fields["total_claimed_amount"]["value"] == row["total_amount"] == "108.00"
    else:
        assert row["principal_debt"] is None


@pytest.mark.parametrize("case", ["terminated", "other_applicant", "extra_award", "multiple_rows"])
def test_separate_order_terminating_fee(case):
    order = 'Включить требование ООО «Пример» в третью очередь в размере 108 руб., из них 8 руб. пеней.'
    applicant = "Иной" if case == "other_applicant" else "Пример"
    fee = f'Производство по заявлению ООО «{applicant}» в части включения в реестр государственной пошлины в размере 5 руб. прекратить.'
    text = "ОПРЕДЕЛИЛ:\n" + order + " " + fee
    if case == "extra_award":
        text += " Взыскать 1 руб. пошлины."
    layout = [service.Claim(order, {}, None)]
    if case == "multiple_rows":
        layout.append(service.Claim("Включить другое требование.", {}, None))
    model = {
        "claim_1_principal_debt": None,
        "claim_1_included_total": service.CourtTotal(amount="108 руб.", scope="with_duty", quote=order, principal_by_remainder=True),
        "claim_1_pau_only_amount": service.Components(items=[], complete=True, quote=fee),
        "claim_1_financial_sanctions": service.Components(items=[PENALTY], complete=True, quote=order),
    }
    fields = service._fixed_claims(layout, {}, model, text)
    row = fields["claims"]["value"][0]
    expected = ("100.00", "0.00") if case == "terminated" else (None, None)
    assert (row["principal_debt"], row["pau_only_amount"]) == expected
    assert row["financial_sanctions"] == "8.00"


@pytest.mark.asyncio
@pytest.mark.parametrize("foreign_quote", [False, True])
async def test_principal_retry_preserves_zero_and_binds_the_missing_row(monkeypatch, foreign_quote):
    orders = ["Включить в третью очередь 0 руб. основного долга и 5 руб. пеней.",
              "Включить в третью очередь 30 руб. основного долга."]
    retry_keys = []

    def gateway(**request):
        if request["request_type"] == "decisions":
            return SimpleNamespace(parsed={"answers": {"claim_layout": {"type": "noul", "noul": 1.0}}})
        def component(index, amount):
            quote = f"{amount} руб. основного долга"
            return {"complete": True, "quote": orders[index], "items": [
                {"kind": "principal", "amount": f"{amount} руб.", "quote": quote}]}
        if request["request_type"] == "rtk3_principal_retry":
            retry_keys.extend(request["response_schema"]["json_schema"]["schema"]["properties"])
            return SimpleNamespace(parsed={"claim_2_principal_debt": component(0, 0) if foreign_quote else component(1, 30),
                                           "claim_1_principal_debt": component(1, 30)})
        return SimpleNamespace(parsed={"claims_complete": True, "claims": [
            {**MODEL_CLAIM, "decision_quote": order, "priority_queue": {"value": "3 очередь", "quote": "в третью очередь"},
             "components": component(0, 0)["items"] + [{"kind":"sanction","amount":"5 руб.","quote":"5 руб. пеней"}] if i == 0 else [],
             "included_total": {"value": None if i == 0 else "30 руб.", "quote": order},
             "salary_quote": "", "is_salary_or_severance": None}
            for i, order in enumerate(orders)]})

    monkeypatch.setattr(service, "call_llm_json_with_fallback", gateway)
    result = await service.extract_fields("ОПРЕДЕЛИЛ:\n" + "\n".join(orders), load_profile("rtk3"))
    assert retry_keys == ["claim_2_principal_debt"]
    assert [r["principal_debt"] for r in result["claims"]["value"]] == ["0.00", None if foreign_quote else "30.00"]


@pytest.mark.parametrize("mode", ["fixed", "full"])
@pytest.mark.parametrize(
    "principal,interest",
    [
        ("90 руб. основного долга", "10 руб. процентов"),
        ("90 руб. основного долга по оплате труда", "10 руб. процентов"),
        ("100 руб. основного долга", ""),
    ],
)
def test_absent_argon_salary_decision_stays_unknown(mode, principal, interest):
    breakdown = ", ".join(filter(None, (principal, interest, PENALTY["quote"])))
    text = ACT.replace("по кредитному договору № 1406/ПК от 21.11.2025 ", "")
    text = text.replace("100 руб. основного долга и 8 руб. пеней", breakdown)
    [claim] = extract_candidates(text).claims
    if mode == "fixed":
        response = {service._claim_key(0, name): value for name, value in service._claim_values(claim).items()}
        response["claim_1_is_salary_or_severance"] = Salary(value=None, quote="")
        result = service._fixed_claims([claim], {}, response, text)
    else:
        components = [{"kind": "principal", "amount": principal.split(" основного")[0], "quote": principal}]
        if interest:
            components.append({"kind": "interest", "amount": "10 руб.", "quote": interest})
        evidence = service.ClaimEvidence.model_validate({
            **MODEL_CLAIM, "decision_quote": claim.quote, "components": [*components, PENALTY],
            "is_salary_or_severance": None, "salary_quote": "",
        })
        result = service._model_claims(
            text, load_profile("rtk3"), {"claims": [evidence], "claims_complete": True}, {},
        )
    [row] = result["claims"]["value"]
    assert row["principal_debt"] == "100.00"
    assert row["total_amount"] == "108.00"
    assert row["is_salary_or_severance"] is None


@pytest.mark.parametrize("mode", ["fixed", "full"])
@pytest.mark.parametrize("quote,expected", [
    ("по выплате заработной платы", True),
    ("выплате выходного пособия", True),
    ("третью очередь", None),
])
def test_argon_salary_true_requires_wage_evidence(mode, quote, expected):
    order = ORDER.replace(MODEL_CLAIM["salary_quote"], "по выплате заработной платы и выплате выходного пособия")
    if mode == "fixed":
        result = service._fixed_claims([service.Claim(order, {}, None)], {}, {
            "claim_1_is_salary_or_severance": Salary(value=True, quote=quote),
        }, order)
    else:
        claim = service.ClaimEvidence.model_validate({
            **MODEL_CLAIM, "decision_quote": order, "is_salary_or_severance": True, "salary_quote": quote,
        })
        result = service._model_claims(order, load_profile("rtk3"), {"claims": [claim], "claims_complete": True}, {})
    assert result["claims"]["value"][0]["is_salary_or_severance"] is expected


@pytest.mark.asyncio
@pytest.mark.parametrize("argon_confirms", [True, False])
async def test_only_argon_supplies_pledge_even_with_a_regex_candidate(monkeypatch, argon_confirms):
    clause = "как обеспеченное залогом имущества должника"
    text = ACT.replace("в размере 108", f"{clause} в размере 108")
    [claim] = extract_candidates(text).claims
    assert claim.fields["pledge_resolution"].value == "yes"
    key = "claim_1_pledge_resolution"

    def gateway(**request):
        if request["request_type"] == "decisions":
            assert key not in request["decisions_questions"]
            assert key not in request["decisions_state"]["fields"]
            return SimpleNamespace(parsed={"answers": {
                name: {"type": "noul", "noul": 1.0} for name in request["decisions_questions"]
            }})
        if request["request_type"] == "rtk3_principal_retry":
            assert key not in request["response_schema"]["json_schema"]["schema"]["properties"]
            return SimpleNamespace(parsed={})
        assert "claims" in request["response_schema"]["json_schema"]["schema"]["properties"]
        assert key not in json.loads(request["user_content"])["verified_fields"]
        return SimpleNamespace(parsed={"claims_complete": True, "claims": [{**MODEL_CLAIM,
            "decision_quote": text.split("ОПРЕДЕЛИЛ:\n")[1], "pledge_resolution": "yes" if argon_confirms else "unknown",
            "pledge_quote": clause if argon_confirms else ""}]})

    monkeypatch.setattr(service, "call_llm_json_with_fallback", gateway)
    fields = await service.extract_fields(text, load_profile("rtk3"))
    [row] = fields["claims"]["value"]
    assert row["pledge_resolution"] == fields["pledge_resolution"]["value"] == ("yes" if argon_confirms else None)
    assert row["secured_by_pledge"] is fields["secured_by_pledge"]["value"] is (True if argon_confirms else None)


@pytest.mark.asyncio
@pytest.mark.parametrize("jev_mode", ["verified", "timeout"])
async def test_reasoning_gateway_routes_and_merges_rtk3_fields(monkeypatch, jev_mode):
    profile = load_profile("rtk3")
    calls = {}

    def gateway(**request):
        kind = request["request_type"]
        calls[kind] = request
        if kind == "decisions":
            if jev_mode == "timeout":
                raise httpx.ReadTimeout("JEV unavailable")
            return SimpleNamespace(parsed={"answers": {
                name: {"type": "noul", "noul": 0.99}
                for name in request["decisions_questions"]
            }})

        basis = MODEL_CLAIM["salary_quote"]
        parsed = {"creditor": CREDITOR, "creditor_inn": CREDITOR_INN,
                  "grounds": {"value": "Кредитный договор", "quote": basis},
                  "grounds_ocr": {"value": basis, "quote": basis}}
        parsed.update(claims=[MODEL_CLAIM], claims_complete=True, uncertainty="")
        if jev_mode == "timeout":
            parsed.update(
                case_number={"value": "А40-123/2026", "quote": "А40-123/2026-1-2Ф"},
                document_case_number={"value": "А40-123/2026-1-2Ф", "quote": "А40-123/2026-1-2Ф"},
                debtor_inn={"value": "012345678901", "quote": "гражданина Петрова Ивана Ивановича (ИНН 012345678901)"},
                inclusion_date={"value": "19.03.2026", "quote": "Полный текст определения изготовлен 19 марта 2026 года."},
            )
        return SimpleNamespace(parsed=parsed)

    monkeypatch.setattr(service, "call_llm_json_with_fallback", gateway)
    def unexpected_split(*args, **kwargs):
        pytest.fail("The production path must send the full act to Argon without constructing claim rows")

    monkeypatch.setattr("ocr_platform.services.rtk3_regex_candidates.claim_candidates", unexpected_split)
    result = await service.extract_fields(ACT, profile)

    assert list(calls) == ["decisions", "rtk3_components"]
    decisions = calls["decisions"]
    assert set(decisions["decisions_questions"]) == set(service.SCALAR_QUESTIONS)
    assert "claim_layout" not in decisions["decisions_state"]
    assert MONEY_KEYS.isdisjoint(decisions["decisions_questions"])
    assert MONEY_KEYS.isdisjoint(decisions["decisions_state"]["fields"])
    fallback = calls["rtk3_components"]
    requested = fallback["response_schema"]["json_schema"]["schema"]["properties"].keys()
    inputs = json.loads(fallback["user_content"])
    direct = {"creditor", "creditor_inn", "document_pledge", "grounds", "grounds_ocr", "claim_1_is_salary_or_severance"}
    assert direct.isdisjoint(decisions["decisions_questions"])
    assert direct.isdisjoint(decisions["decisions_state"]["fields"])
    assert direct.isdisjoint(inputs["verified_fields"])
    assert {"grounds", "grounds_ocr"} <= requested
    assert all(result[key]["extraction_route"] == "gemini_argon" for key in ("grounds", "grounds_ocr"))
    assert {"claims", "claims_complete", "uncertainty", "creditor", "creditor_inn"} <= requested
    assert inputs["document"] == ACT
    assert set(inputs) == {"document", "verified_fields"}
    assert not any(name.startswith("claim_") for name in inputs["verified_fields"])
    assert ("case_number" in inputs["verified_fields"]) is (jev_mode != "timeout")
    assert "creditor_inn" not in inputs["verified_fields"]
    if jev_mode == "timeout":
        assert "слова, связывающие его с ролью должника" in fallback["system_prompt"]

    assert {name: result[name]["value"] for name in service.SCALAR_FIELDS} == {
        "case_number": "А40-123/2026",
        "document_case_number": "А40-123/2026-1-2Ф",
        "debtor_inn": "012345678901",
        "creditor": "ПАО «Пример»",
        "creditor_inn": "0123456789",
        "inclusion_date": "19.03.2026",
        "grounds": "Кредитный договор",
        "grounds_ocr": "по кредитному договору № 1406/ПК от 21.11.2025",
    }
    assert {name: result[name]["value"] for name in ("kad_publication_date", "pledge_resolution", "secured_by_pledge",
                                                        "total_claimed_amount")} == {
        "kad_publication_date": None, "pledge_resolution": None, "secured_by_pledge": None,
        "total_claimed_amount": "108.00",
    }
    assert result["claims"]["value"] == [{
        "priority_queue": "3 очередь", "contract_number": "1406/ПК", "principal_debt": "100.00",
        "financial_sanctions": "8.00", "pau_only_amount": "0.00", "claimed_amount": "108.00",
        "total_amount": "108.00", "is_salary_or_severance": False,
        "pledge_resolution": None, "secured_by_pledge": None,
    }]


@pytest.mark.asyncio
@pytest.mark.parametrize("current", [False, True], ids=["embedded-cost", "current-fee"])
async def test_argon_distinguishes_contract_cost_from_current_fee(monkeypatch, current):
    principal = "90 руб. основного долга"
    fee = "10 руб. расходов по оплате государственной пошлины"
    if current:
        fee += " за рассмотрение настоящего спора"
    text = ACT.replace("100 руб. основного долга и 8 руб. пеней", f"{principal}, {fee} и 8 руб. пеней")
    order = text.split("ОПРЕДЕЛИЛ:\n")[1]
    def gateway(**request):
        if request["request_type"] == "decisions":
            return SimpleNamespace(parsed={"answers": {
                k: {"type": "noul", "noul": 0.99} for k in request["decisions_questions"]
            }})
        return SimpleNamespace(parsed={"claims_complete": True, "claims": [{
            **MODEL_CLAIM, "decision_quote": order, "components_complete": not current,
            "complete_categories": ["financial_sanctions"],
            "total_scope": "with_duty", "components": [
                {"kind": "principal", "amount": "90 руб.", "quote": principal},
                *([] if current else [{"kind": "contract_cost", "amount": "10 руб.", "quote": fee}]), PENALTY],
        }]})
    monkeypatch.setattr(service, "call_llm_json_with_fallback", gateway)
    result = await service.extract_fields(text, load_profile("rtk3"))
    row, = result["claims"]["value"]
    assert row["principal_debt"] == (None if current else "100.00")
    assert row["financial_sanctions"] == "8.00"
    assert row["pau_only_amount"] == (None if current else "0.00")
    assert row["total_amount"] == "108.00"
    assert result["total_claimed_amount"]["value"] == (None if current else "108.00")


@pytest.mark.parametrize("penalty", [0, 8])
def test_incomplete_argon_breakdown_retains_printed_gross_and_confirmed_sanction(penalty):
    order = ORDER.replace("100 руб. основного долга и 8 руб. пеней", f"{penalty} руб. пеней")
    claim = service.ClaimEvidence.model_validate({
        **MODEL_CLAIM, "decision_quote": order, "total_scope": "with_duty", "components_complete": False,
        "complete_categories": ["financial_sanctions"],
        "components": [{"kind": "sanction", "amount": f"{penalty} руб.", "quote": f"{penalty} руб. пеней"}],
    })
    result = service._model_claims(order, load_profile("rtk3"), {"claims": [claim], "claims_complete": True}, {})
    row, = result["claims"]["value"]
    assert row["principal_debt"] is row["claimed_amount"] is row["pau_only_amount"] is None
    assert row["financial_sanctions"] == f"{penalty}.00"
    assert row["total_amount"] == "108.00"
    assert result["total_claimed_amount"]["value"] is None


def test_gross_repeated_as_principal_keeps_only_supported_amounts():
    claim = service.Claim(ORDER, {}, None)
    response = {
        "claim_1_principal_debt": service.Components(items=[
            {"kind": "principal", "amount": "108 руб.", "quote": "в размере 108 руб."},
        ], complete=True, quote=ORDER),
        "claim_1_financial_sanctions": service.Components(items=[PENALTY], complete=True, quote=ORDER),
        "claim_1_included_total": service.CourtTotal(amount="108 руб.", quote=ORDER, scope="with_duty"),
    }
    fields = service._fixed_claims([claim], {}, response, ACT)
    row, = fields["claims"]["value"]
    assert row["principal_debt"] is row["claimed_amount"] is fields["total_claimed_amount"]["value"] is None
    assert (row["financial_sanctions"], row["total_amount"]) == ("8.00", "108.00")


@pytest.mark.parametrize("current", [False, True])
def test_generic_fee_under_prior_order_requires_current_award(current):
    fee = "2 руб. государственной пошлины" + (" за рассмотрение настоящего спора" if current else "")
    order = f"Включить на основании судебного приказа 10 руб. долга, а также {fee}."
    response = {"claim_1_pau_only_amount": service.Components(
        items=[{"kind": "duty", "amount": "2 руб.", "quote": fee}], complete=True, quote=order,
    )}
    fields = service._fixed_claims([service.Claim(order, {}, None)], {}, response, order)
    assert fields["claims"]["value"][0]["pau_only_amount"] == ("2.00" if current else None)


@pytest.mark.asyncio
@pytest.mark.parametrize("partial", [False, True], ids=["empty-argon", "partial-argon"])
async def test_argon_incomplete_reply_keeps_rtk3_evidence_and_requests_review(
    monkeypatch, tmp_path, partial,
):
    from ocr_platform.orchestration import run_processor as worker
    from ocr_platform.storage import models, repository

    act = ACT.replace("в размере 108 руб.", "как обеспеченное залогом имущества в размере 108 руб.")
    order = act.split("ОПРЕДЕЛИЛ:\n", 1)[1]
    unconfirmed = {"document_case_number", "inclusion_date"}
    calls = []

    def gateway(**request):
        calls.append(request)
        if request["request_type"] == "decisions":
            answers = {
                name: {"type": "noul", "noul": 0.1 if name in unconfirmed else 0.99}
                for name in request["decisions_questions"]
            }
            assert "claim_layout" not in answers
            return SimpleNamespace(parsed={"answers": answers})
        if not partial:
            return SimpleNamespace(parsed=None)
        facts = {
            "inclusion_date": {"value": "19.03.2026", "quote": "Полный текст определения изготовлен 19 марта 2026 года."},
            "grounds_ocr": "Ответ без объекта Fact",
        }
        facts["claims"] = [{
            **MODEL_CLAIM, "decision_quote": order, "pledge_resolution": "yes",
            "pledge_quote": "как обеспеченное залогом имущества",
        }]
        return SimpleNamespace(parsed=facts)

    monkeypatch.setattr(service, "call_llm_json_with_fallback", gateway)
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    models.Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(repository, "get_session", sessions)
    monkeypatch.setattr(repository, "init_db", lambda: None)
    monkeypatch.setattr(worker, "get_pipeline_tracer", MagicMock())
    monkeypatch.setattr(worker, "mlflow_run", lambda *args, **kwargs: nullcontext())
    for name in ("mlflow_set_tag", "mlflow_log_metric", "mlflow_log_param", "mlflow_log_text"):
        monkeypatch.setattr(worker, name, MagicMock())
    webhook = AsyncMock()
    monkeypatch.setattr(worker, "_trigger_webhook_safely", webhook)
    path = tmp_path / "ruling.txt"
    path.write_text(act, encoding="utf-8")
    try:
        with sessions() as session:
            session.add(models.Document(id="doc-rtk3", source_type="external", document_type="rtk3"))
            session.add(models.DocumentFile(document_id="doc-rtk3", file_type="text", storage_path=str(path)))
            session.add(models.PipelineRun(id="run-rtk3", document_id="doc-rtk3", profile_id="rtk3", status="queued",
                                           webhook_url="https://callback.invalid"))
            session.commit()

        await worker._process_pipeline_run_impl("run-rtk3")
        expected_calls = ["decisions", "rtk3_components"]
        if not partial:
            expected_calls.append("rtk3_document_facts_retry")
        assert [call["request_type"] for call in calls] == expected_calls
        request = calls[1]["response_schema"]["json_schema"]["schema"]["properties"]
        assert {"document_case_number", "inclusion_date", "grounds_ocr"} <= request.keys()
        assert "claim_1_financial_sanctions" not in request
        assert "claims" in request
        assert "case_number" not in request

        confirmed = {"debtor_inn": "012345678901"}
        with sessions() as session:
            assert session.get(models.PipelineRun, "run-rtk3").status == "done"
            stored = session.scalar(select(models.StructuredVersion)).data
            for name, value in confirmed.items():
                assert stored[name]["value"] == value
                assert stored[name]["extraction_route"] == "regex_jev"
            assert stored["inclusion_date"]["value"] == ("19.03.2026" if partial else None)
            assert stored["document_case_number"]["value"] == "А40-123/2026-1-2Ф"
            assert stored["case_number"]["value"] == "А40-123/2026"
            assert stored["document_case_number"]["extraction_route"] == "document_header"
            assert stored["creditor"]["value"] is None
            assert stored["creditor_inn"]["value"] is None
            assert stored["grounds"]["value"] is None
            assert stored["grounds_ocr"]["value"] is None
            assert stored["secured_by_pledge"]["value"] is None
            assert stored["total_claimed_amount"]["value"] is None
            rows = stored["claims"]["value"]
            if partial:
                row, = rows
                assert (row["priority_queue"], row["contract_number"], row["principal_debt"],
                        row["pledge_resolution"], row["secured_by_pledge"]) == (
                            "3 очередь", "1406/ПК", "100.00",
                            "yes" if partial else None, True if partial else None,
                        )
                assert row["is_salary_or_severance"] is (False)
                assert row["pau_only_amount"] == "0.00"
                assert row["financial_sanctions"] == ("8.00" if partial else None)
                assert row["claimed_amount"] == "108.00"
                assert row["total_amount"] == "108.00"
            else:
                assert rows == []

        from ocr_platform.api.main import create_app

        with TestClient(create_app()) as client:
            reply = client.get("/documents/doc-rtk3/result")
        assert reply.status_code == 200
        webhook.assert_awaited_once()
        sent = webhook.await_args.args[1]
        assert sent["status"] == "done"
        for payload in (sent, reply.json()):
            assert payload["validation_status"] == "errors"
            assert payload["human_review_required"] is True
            assert payload["human_review_reason"] == "rtk3_requires_review"
            issues = {issue["field_name"] for issue in payload["validation_issues"]}
            assert "document_case_number" not in issues
            assert ("inclusion_date" in issues) is not partial
            for name in (*confirmed, "claims", "document_case_number", "inclusion_date",
                         "total_claimed_amount", "secured_by_pledge"):
                assert payload["fields"][name]["value"] == stored[name]["value"]
    finally:
        engine.dispose()

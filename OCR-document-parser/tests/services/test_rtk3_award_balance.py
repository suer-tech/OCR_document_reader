import pytest

from ocr_platform.orchestration.router import load_profile
from ocr_platform.services import rtk3_regex_jev as service


@pytest.mark.parametrize("mode", ["fixed", "full"])
@pytest.mark.parametrize("kind", ["complete", "cropped", "mortgage", "denied", "no_model_answer"])
def test_argon_no_pledge_for_complete_ordinary_admission(mode, kind):
    order = "Включить требование банка в третью очередь в размере 100 руб."
    if kind == "denied":
        order = "Признать требование банка в размере 100 руб. необоснованным."
    text = "ОПРЕДЕЛИЛ:\n" + order
    if kind == "mortgage":
        text += " Вопрос об ипотеке рассмотреть отдельно."
    if kind != "cropped":
        text += " Определение может быть обжаловано."
    resolution = None if kind == "no_model_answer" else "no"
    if mode == "fixed":
        response = {"claim_1_pledge_resolution": service.PledgeFact(value=resolution, quote=order)}
        result = service._fixed_claims([service.Claim(order, {}, None)], {}, response, text)
    else:
        claim = service.ClaimEvidence.model_validate({
            "decision_quote": order, "priority_queue": {"value": "3 очередь", "quote": "третью очередь"},
            "breakdown_quote": "", "components_complete": True, "components": [], "principal_by_remainder": True,
            "included_total": {"value": "100 руб.", "quote": order}, "total_scope": "without_duty",
            "is_salary_or_severance": None, "salary_quote": "", "pledge_resolution": resolution or "unknown",
            "pledge_quote": order if resolution else "", "uncertainty": "",
        })
        result = service._model_claims(text, load_profile("rtk3"), {"claims": [claim], "claims_complete": True}, {})
    status = "no" if kind == "complete" else None
    row, = result["claims"]["value"]
    assert row["pledge_resolution"] == result["pledge_resolution"]["value"] == status
    assert row["secured_by_pledge"] is result["secured_by_pledge"]["value"] is (False if status else None)


@pytest.mark.parametrize("mode", ["fixed", "full"])
@pytest.mark.parametrize("case", ["separate", "duty", "wrong_amount", "queue_only"])
def test_priced_separate_accounting_of_third_priority_sanction(mode, case):
    balance = "90 руб. – требования кредиторов третьей очереди."
    clause = "10 руб. требования кредиторов третьей очереди"
    if case != "queue_only":
        clause += (", учитывающиеся отдельно в реестре требований кредиторов и подлежащие удовлетворению "
                   "после погашения основной суммы задолженности и причитающихся процентов")
    order = "Включить требования в третью очередь в сумме 100 руб. " + balance + " " + clause
    if case == "duty":
        order += " Так учитываются расходы по госпошлине."
    item = {"kind": "sanction", "amount": "90 руб." if case == "wrong_amount" else "10 руб.",
            "quote": balance + " " + clause if case == "wrong_amount" else clause}
    if mode == "fixed":
        reply = {"claim_1_principal_debt": None, "claim_1_included_total": service.CourtTotal(
            amount="100 руб.", quote=order, scope="with_duty", principal_by_remainder=True),
            "claim_1_pau_only_amount": service.Components(items=[], complete=True, quote=order),
            "claim_1_financial_sanctions": service.Components(items=[item], complete=True, quote=order)}
        result = service._fixed_claims([service.Claim(order, {}, None)], {}, reply, order)
    else:
        claim = service.ClaimEvidence.model_validate({
            "decision_quote": order, "priority_queue": {"value": "3 очередь", "quote": "третью очередь"},
            "breakdown_quote": "", "components_complete": True, "components": [item], "principal_by_remainder": True,
            "included_total": {"value": "100 руб.", "quote": order}, "total_scope": "with_duty",
            "is_salary_or_severance": None, "salary_quote": "", "pledge_resolution": "unknown",
            "pledge_quote": "", "uncertainty": "",
        })
        result = service._model_claims(order, load_profile("rtk3"), {"claims": [claim], "claims_complete": True}, {})
    row, = result["claims"]["value"]
    amounts = row["principal_debt"], row["financial_sanctions"], result["total_claimed_amount"]["value"]
    assert amounts == (("90.00", "10.00", "100.00") if case == "separate" else (None, None, None))


@pytest.mark.parametrize("mode", ["fixed", "full"])
@pytest.mark.parametrize("scope,penalty,fee,expected", [
    ("without_duty", 0, 0, ("100.00", "100.00", "100.00")),
    ("without_duty", 8, 5, ("92.00", "100.00", "105.00")),
    ("with_duty", 8, 5, ("87.00", "95.00", "100.00")),
    ("unknown", 8, 5, (None, None, None)),
    ("with_duty", 99, 5, (None, None, None)),
])
def test_confirmed_award_balance(mode, scope, penalty, fee, expected):
    order = "Включить в третью очередь требование кредитора в размере 100 руб."
    if penalty:
        order += f", в том числе {penalty} руб. неустойки."
    if fee:
        order += f" Пошлина за рассмотрение настоящего спора составляет {fee} руб."
    components = []
    if penalty:
        components.append({"kind": "sanction", "amount": f"{penalty} руб.", "quote": f"{penalty} руб. неустойки"})
    if fee:
        components.append({"kind": "duty", "amount": f"{fee} руб.", "quote": f"Пошлина за рассмотрение настоящего спора составляет {fee} руб."})
    if mode == "fixed":
        claim = service.Claim(order, {}, None)
        reply = {"claim_1_principal_debt": None, "claim_1_included_total": service.CourtTotal(
            amount="100 руб.", quote=order, scope=scope, principal_by_remainder=True)}
        for field, kind in (("financial_sanctions", "sanction"), ("pau_only_amount", "duty")):
            reply["claim_1_" + field] = service.Components(
                complete=True, items=[c for c in components if c["kind"] == kind], quote=order)
        result = service._fixed_claims([claim], {}, reply, order)
    else:
        evidence = service.ClaimEvidence.model_validate({
            "decision_quote": order, "priority_queue": {"value": "3 очередь", "quote": "третью очередь"},
            "breakdown_quote": "", "components_complete": True, "components": components,
            "principal_by_remainder": True, "included_total": {"value": "100 руб.", "quote": order},
            "total_scope": scope, "is_salary_or_severance": None, "salary_quote": "",
            "pledge_resolution": "unknown", "pledge_quote": "", "uncertainty": "",
        })
        result = service._model_claims(order, load_profile("rtk3"), {"claims": [evidence], "claims_complete": True}, {})
    row, = result["claims"]["value"]
    assert (row["principal_debt"], row["claimed_amount"], row["total_amount"]) == expected
    assert result["total_claimed_amount"]["value"] == expected[1]
    if expected[0] is not None:
        assert (row["financial_sanctions"], row["pau_only_amount"]) == (f"{penalty}.00", f"{fee}.00")


@pytest.mark.parametrize("field", ["principal_debt", "financial_sanctions", "pau_only_amount", "included_total"])
def test_partial_answer_cannot_authorize_a_balance(field):
    order = "Включить требование в третью очередь в размере 100 руб."
    confirmed_zero = service.Components(complete=True, items=[], quote=order)
    reply = {
        "claim_1_principal_debt": None, "claim_1_pau_only_amount": confirmed_zero,
        "claim_1_financial_sanctions": confirmed_zero, "claim_1_included_total": service.CourtTotal(
            amount="100 руб.", quote=order, scope="without_duty", principal_by_remainder=True),
    }
    del reply["claim_1_" + field]
    fields = service._fixed_claims([service.Claim(order, {}, None)], {}, reply, order)
    row, = fields["claims"]["value"]
    assert row["principal_debt"] is row["claimed_amount"] is fields["total_claimed_amount"]["value"] is None


@pytest.mark.parametrize("complete,printed", [(False, "100 руб."), (True, "90 руб."), (True, None)])
def test_unconfirmed_full_award_keeps_debt_unknown(complete, printed):
    order = "Включить в третью очередь требование в размере 100 руб."
    row = service.ClaimEvidence.model_validate({
        "decision_quote": order, "priority_queue": {"value": "3 очередь", "quote": "третью очередь"},
        "breakdown_quote": "", "components_complete": complete, "components": [], "principal_by_remainder": True,
        "included_total": {"value": printed, "quote": order}, "total_scope": "without_duty",
        "is_salary_or_severance": None, "salary_quote": "", "pledge_resolution": "unknown",
        "pledge_quote": "", "uncertainty": "",
    })
    fields = service._model_claims(order, load_profile("rtk3"), {"claims": [row], "claims_complete": True}, {})
    assert fields["claims"]["value"][0]["principal_debt"] is fields["total_claimed_amount"]["value"] is None


@pytest.mark.parametrize("mode", ["fixed", "full"])
def test_embedded_fee_requires_evidence_of_a_current_award(mode):
    order = "Включить в третью очередь требование в размере 100 руб., из которых 95 руб. долга и 5 руб. расходов по госпошлине."
    fee = {"kind": "duty", "amount": "5 руб.", "quote": "5 руб. расходов по госпошлине"}
    if mode == "fixed":
        result = service._fixed_claims([service.Claim(order, {}, None)], {}, {
            "claim_1_pau_only_amount": service.Components(items=[fee], complete=True, quote=order),
        }, order)
    else:
        claim = service.ClaimEvidence.model_validate({
            "decision_quote": order, "priority_queue": {"value": "3 очередь", "quote": "третью очередь"},
            "breakdown_quote": "", "components_complete": True, "components": [fee], "principal_by_remainder": True,
            "included_total": {"value": "100 руб.", "quote": order}, "total_scope": "with_duty",
            "is_salary_or_severance": None, "salary_quote": "", "pledge_resolution": "unknown",
            "pledge_quote": "", "uncertainty": "",
        })
        result = service._model_claims(order, load_profile("rtk3"), {"claims": [claim], "claims_complete": True}, {})
    assert result["claims"]["value"][0]["pau_only_amount"] is None

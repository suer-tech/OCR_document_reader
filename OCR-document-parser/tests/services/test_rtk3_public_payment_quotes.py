import pytest

from ocr_platform.orchestration.router import load_profile
from ocr_platform.services import rtk3_regex_jev as service


@pytest.mark.parametrize("label, accepted", [
    ("страховые взносы", True),
    ("страховых взносов на обязательное пенсионное страхование", True),
    ("недоимка по налогу", True),
    ("транспортный налог", True),
    ("сумма налога", True),
    ("налог на имущество физических лиц", True),
    ("пени по страховым взносам", False),
    ("штраф за неуплату налога", False),
    ("проценты за просрочку уплаты страховых взносов", False),
    ("госпошлина по взысканию недоимки", False),
    ("членские взносы", False),
], ids=["contributions", "pension", "arrears", "transport", "tax-amount", "property",
        "penalty", "fine", "interest", "fee", "membership"])
def test_public_payment_component_and_retry(label, accepted):
    quote = f"{label} – 24 231.33 руб."
    order = "Включить требование ФНС России во вторую очередь: " + quote
    item = {"kind": "principal", "amount": "24 231.33 руб.", "quote": quote}
    claim = service.ClaimEvidence.model_validate({
        "decision_quote": order, "breakdown_quote": quote,
        "priority_queue": {"value": "2 очередь", "quote": "во вторую очередь"},
        "components_complete": True, "components": [item],
        "included_total": {"value": item["amount"], "quote": quote}, "total_scope": "without_duty",
        "is_salary_or_severance": False, "salary_quote": order, "pledge_resolution": "no",
        "pledge_quote": order, "uncertainty": "",
    })
    result = service._model_claims(order, load_profile("rtk3"), {"claims": [claim], "claims_complete": True}, {})
    assert result["claims"]["value"][0]["principal_debt"] == ("24231.33" if accepted else None)
    patch = service.Components(items=[item], complete=True, quote=quote)
    anchor = service.Claim(order, {}, None)
    assert service._itemized_principal(patch, anchor, order) is accepted
    if label != "членские взносы":
        fixed = service._fixed_claims([anchor], {}, {"claim_1_principal_debt": patch}, order)
        assert fixed["claims"]["value"][0]["principal_debt"] == ("24231.33" if accepted else None)

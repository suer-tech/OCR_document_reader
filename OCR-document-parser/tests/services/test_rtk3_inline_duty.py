import json
from unittest.mock import Mock

import pytest

from ocr_platform.services import rtk3_regex_jev as service
from ocr_platform.services.rtk3_evidence import Component, included_contract_cost
from ocr_platform.services.rtk3_regex_candidates import extract_candidates, inline_duty_passages

ORDER = "Включить в третий раздел реестра требование банка в размере 100 000 руб."
TAIL = " с режимом учета и удовлетворения данного требования к должнику в порядке, установленном пунктом 3 статьи 137."


@pytest.mark.parametrize("addition", [
    "государственную пошлину в сумме 5 000 руб.",
    "расходы по уплате государственной пошлины в размере 5 000 руб.",
    "5 000 руб. расходов по оплате государственной пошлины",
], ids=["direct", "cost", "amount-first"])
def test_inline_fee_in_same_source_passage(addition):
    clause = ORDER + ", а также " + addition + TAIL
    text = "ОПРЕДЕЛИЛ:\n" + clause + " Взыскать с должника по иному договору 8 руб."
    assert inline_duty_passages(text) == [clause]


@pytest.mark.parametrize("separator,qualification", [
    (". Взыскать ", ""),
    (", в том числе ", ""),
    (", а также ", " по другому договору"),
    (", а также ", " в доход федерального бюджета"),
    (", а также ", " в пользу другого кредитора"),
    (", а также ", " во вторую очередь"),
], ids=["new-order", "embedded", "other-contract", "budget", "other-creditor", "queue"])
def test_separate_or_embedded_fee_has_no_inline_hint(separator, qualification):
    text = "ОПРЕДЕЛИЛ:\n" + ORDER + separator + "госпошлину в размере 5 000 руб." + qualification + "."
    assert inline_duty_passages(text) == []


def test_legacy_row_keeps_original_fee_clause():
    text = ("ОПРЕДЕЛИЛ:\n1.Требование ПАО «Пример» в размере 100 000 руб. признать обоснованным. "
            "2. Включить в третий раздел реестра требований кредиторов должника требование ПАО «Пример» "
            "в размере 100 000 руб., а также государственную пошлину в сумме 5 000 руб." + TAIL)
    row, = extract_candidates(text).claims
    assert row.quote.startswith("2. Включить")
    assert row.quote.endswith(TAIL)
    assert row.total.amount == "100 000 руб."
    assert row.fields["pau_only_amount"].items[0].amount == "5 000 руб."


def test_separate_fee_does_not_reclassify_an_equal_included_fee():
    embedded = "5 руб. государственной пошлины"
    separate = "государственной пошлины в размере 5 руб."
    order = (f"Включить в размере 105 руб., в том числе 100 руб. долга, {embedded}. "
             f"Судебные расходы по уплате {separate} подлежат взысканию с должника.")
    fee = Component(kind="duty", amount="5 руб.", quote=embedded)
    assert included_contract_cost(fee, order)
    assert not included_contract_cost(fee.model_copy(update={"quote": separate}), order)


def test_fee_cannot_be_added_to_unreconciled_breakdown():
    citation = "государственной пошлины в размере 5 руб."
    order = ("Включить в размере 105 руб., в том числе 100 руб. основного долга. "
             f"Судебные расходы по уплате {citation} подлежат учету в третьей очереди.")
    assert included_contract_cost(Component(kind="duty", amount="5 руб.", quote=citation), order)


@pytest.mark.asyncio
@pytest.mark.parametrize("field,has_passage", [("claims", True), ("creditor", False)])
async def test_argon_keeps_full_document(field, has_passage, monkeypatch):
    clause = ORDER + ", а также госпошлину в размере 5 000 руб." + TAIL
    text = "Мотивировка.\nОПРЕДЕЛИЛ:\n" + clause + " Взыскать по иному договору 8 руб."
    gateway = Mock(return_value=None)
    monkeypatch.setattr(service, "call_llm_json_with_fallback", gateway)
    await service._gemini(text, {"provider": "router_ai", "model": "google/gemini-4-argon"},
                          {}, {field: (str, ...)}, [], {})
    request = json.loads(gateway.call_args.kwargs["user_content"])
    assert request["document"] == text
    assert request.get("inline_duty_passages", []) == ([clause] if has_passage else [])

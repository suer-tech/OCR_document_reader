"""Offline regressions for independently stated RTK3 sums and fees."""
import pytest

from ocr_platform.services.rtk3_regex_candidates import (
    CourtTotal, claim_amounts, extract_candidates, nested_contract_amounts,
)
from ocr_platform.services.rtk3_regex_jev import _claim_key, _claim_values, _fallback_schema, _fixed_claims
from ocr_platform.services.rtk3_requisites import explicit_ground_category, source_inn
from ocr_platform.services.rtk3_validation import rtk3_issues


def fields(text):
    candidates = extract_candidates(text)
    patch = {_claim_key(i, name): value for i, claim in enumerate(candidates.claims)
             for name, value in _claim_values(claim).items()}
    return _fixed_claims(candidates.claims, {}, patch, text)


def test_debtor_inn_in_current_application_and_bankruptcy_reference():
    introduction = (
        "Рассмотрев заявление ООО «Пример» (ИНН 0123456789) о включении в реестр требований "
        "кредиторов задолженности Примерова Ивана Ивановича (ИНН 012345678901). ОПРЕДЕЛИЛ:"
    )
    assert source_inn(introduction, "debtor_inn").value == "012345678901"
    reference = (
        "В рамках дела № А00-123/2026 о признании Примерова Ивана Ивановича "
        "(ИНН 012345678901) несостоятельным (банкротом). ОПРЕДЕЛИЛ:"
    )
    assert source_inn(reference, "debtor_inn").value == "012345678901"
    assert source_inn(reference.replace("несостоятельным (банкротом)", "кредитором"), "debtor_inn").value is None


def test_personal_inn_in_case_application_requires_the_same_registry_debtor():
    filing = (
        "Рассмотрев заявление ПАО «Пример» о включении требований в реестр требований кредиторов "
        "должника, по делу №А60-123/2026 по заявлению Петрова Ивана Ивановича "
        "17.04.1990 г.р., место рождения г. Североуральск ИНН: 012345678901."
    )
    order = (
        "ОПРЕДЕЛИЛ:\nВключить требования ПАО «Пример» в реестр требований кредиторов Петрова "
        "Ивана Ивановича в размере 100 руб. основного долга в третью очередь."
    )
    result = extract_candidates(f"{filing} {order}").fields
    assert result["debtor_inn"].value == "012345678901"
    bankruptcy_application = filing.replace("по делу №А60-123/2026", "в деле")
    assert source_inn(f"{bankruptcy_application} {order}", "debtor_inn").value == "012345678901"
    different = f"{filing} {order.replace('Петрова', 'Сидорова')}"
    assert "debtor_inn" not in extract_candidates(different).fields


def test_registry_debtor_inn_with_a_line_break_inside_creditors():
    text = (
        "ОПРЕДЕЛИЛ:\nВключить в третью очередь реестра требований кредито-\nров гражданина "
        "Петрова Ивана Ивановича (01.01.1990 года рождения, ИНН 012345678901) "
        "требование ПАО «Пример» (ИНН 0123456789) в размере 100 руб. основного долга."
    )
    result = extract_candidates(text).fields
    assert result["debtor_inn"].value == "012345678901"
    assert result["creditor_inn"].value == "0123456789"


def test_amounts_after_unspaced_commas_and_cost_with_unknown_origin():
    text = (
        "ОПРЕДЕЛИЛ:\nПризнать обоснованными требования ПАО «Пример» в размере 19 300,30 руб., "
        "в том числе: 9 500 руб. – основной долг,2 280 руб. – срочные проценты,"
        "6 100, 5 руб. – просроченные проценты,644, 8 руб. – пеня, 775 руб. – государственная пошлина, "
        "с удовлетворением после удовлетворения требований кредиторов, включенных в реестр."
    )
    row, = fields(text)["claims"]["value"]
    assert row["total_amount"] == "19300.30"
    assert row["financial_sanctions"] == "644.80"
    assert row["pau_only_amount"] is None
    assert row["principal_debt"] is None


def test_pledge_confirmation_links_to_same_creditor_and_contract():
    text = (
        "ОПРЕДЕЛИЛ:\n1. Включить требования ПАО «Пример» в третью очередь реестра требований кредиторов "
        "должника по кредитному договору № 1406/ПК 21.11.2025 в размере 108 руб., из которых: "
        "- 100 руб. требование по уплате суммы основного долга; - 8 руб. проценты; "
        "установить требования ПАО «Пример» в реестре требований кредиторов, вытекающих из кредитного "
        "договора № 1406/ПК 21.11.2025 как требования, обеспеченные залогом имущества. "
        "2. Взыскать с должника судебные расходы по оплате государственной пошлины за подачу заявления "
        "о включении требований в реестр требований кредиторов в размере 5 руб."
    )
    result = extract_candidates(text)
    assert result.fields["grounds_ocr"].value == "по кредитному договору № 1406/ПК 21.11.2025"
    row, = fields(text)["claims"]["value"]
    assert row["contract_number"] == "1406/ПК"
    assert row["secured_by_pledge"] is True
    assert row["total_amount"] == "113.00"
    other = text.replace("договора № 1406/ПК", "договора № 1407/ПК")
    assert not extract_candidates(other).claims


def test_budget_duty_is_distinct_from_tax_allocation_by_queue():
    text = (
        "ОПРЕДЕЛИЛ:\nПризнать обоснованным и включить в третью очередь реестра требований кредиторов "
        "требование ФНС России в размере 121 руб., в том числе: "
        "- во вторую очередь реестра требований кредиторов задолженность в размере 101 руб. (основной долг). "
        "- в третью очередь реестра требований кредиторов задолженность в размере 20 руб., "
        "в том числе: задолженность по пени – 20 руб. "
        "Взыскать с конкурсной массы должника в доход федерального бюджета государственную пошлину "
        "за рассмотрение заявления о включении требования в реестр требований кредиторов в размере 5 руб."
    )
    second, third, fee = fields(text)["claims"]["value"]
    assert (second["priority_queue"], second["total_amount"]) == ("2 очередь", "101.00")
    assert (third["priority_queue"], third["financial_sanctions"]) == ("3 очередь", "20.00")
    assert (fee["priority_queue"], fee["pau_only_amount"]) == (None, "5.00")


def test_registry_fee_does_not_include_a_recovery_assigned_to_the_local_plan():
    text = (
        "Судебные расходы за денежные требования – 10 руб. "
        "- судебные расходы за требования, обеспеченные залогом – 25 руб. "
        "(20 руб. подлежат возврату из федерального бюджета, остальная сумма подлежит "
        "возмещению кредитору в рамках локального плана реструктуризации долгов – 5 руб.). "
        "ОПРЕДЕЛИЛ:\nВключить требования ПАО «Пример» в третью очередь реестра требований кредиторов "
        "в размере 12 3456,50 руб., вытекающие из кредитных договоров №1 от 01.01.2024 и №2 от 02.01.2024. "
        "Взыскать в пользу ПАО «Пример» 10 руб. судебных расходов по уплате государственной пошлины "
        "за рассмотрение настоящего спора, подлежащих удовлетворению по пункту 3 статьи 137. "
        "Возвратить ПАО «Пример» 20 руб. государственной пошлины. "
        "Взыскать в пользу ПАО «Пример» расходы по уплате государственной пошлины в размере 5 руб."
    )
    row, = fields(text)["claims"]["value"]
    assert row["contract_number"] is None
    assert (row["claimed_amount"], row["pau_only_amount"], row["total_amount"]) == ("123456.50", "10.00", "123466.50")
    assert not extract_candidates(text.replace("долгов – 5 руб.", "долгов – 6 руб.")).claims


def test_express_admission_and_application_fee_without_printed_queue():
    text = (
        "ОПРЕДЕЛИЛ:\nВключить требование ПАО «Пример» в реестр требований кредиторов в размере 100 руб. "
        "Взыскать в пользу ПАО «Пример» судебные расходы по оплате государственной пошлины "
        "за подачу заявления о включении требований кредиторов должника в размере 5 руб."
    )
    row, = fields(text)["claims"]["value"]
    assert row["priority_queue"] is None
    assert row["principal_debt"] is None
    assert (row["claimed_amount"], row["pau_only_amount"], row["total_amount"]) == ("100.00", "5.00", "105.00")


def test_printed_award_and_sanctions_survive_inconsistent_cost_breakdown():
    text = (
        "ОПРЕДЕЛИЛ:\nТребование ООО «Пример» в общей сумме 120,01 руб., из них: "
        "100 руб. – основной долг, 15 руб. – штраф, 5 руб. – расходы по оплате госпошлины "
        "признать обоснованным и включить в третью очередь реестра требований кредиторов."
    )
    row, = fields(text)["claims"]["value"]
    assert row["total_amount"] == "120.01"
    assert row["financial_sanctions"] == "15.00"
    assert row["claimed_amount"] is None
    assert row["pau_only_amount"] is None


def test_long_filing_confirmation_and_page_number_inside_penalty_amount():
    text = (
        "ОПРЕДЕЛИЛ:\n"
        "Заявление ПАО «Пример» об установлении и включении в реестр требований кредиторов "
        "должника, место регистрации – г. Москва (дата рождения и место рождения указаны в материалах "
        "электронного дела, адрес регистрации указан в заявлении уполномоченного представителя должника, "
        "дата отправки документов и судебных извещений указана в протоколе судебного заседания), "
        "задолженности в размере 127,50 руб., удовлетворить. "
        "Признать обоснованными и включить в третью очередь реестра требований кредиторов требования "
        "ПАО «Пример» в размере 127,50 руб., из которых: 120 руб. – основной долг, "
        "7,50\n2\nруб. – неустойка за просроченный основной долг. "
        "Суммы неустойки (пени, штрафы) и иные финансовые санкции подлежат включению в группу "
        "«финансовые санкции» третьей очереди реестра требований кредиторов должника. "
        "Взыскать с должника в пользу ПАО «Пример» 5 руб. государственной пошлины за рассмотрение настоящего спора."
    )
    row, = fields(text)["claims"]["value"]
    assert (row["principal_debt"], row["financial_sanctions"], row["total_amount"]) == ("120.00", "7.50", "132.50")
    other = text.replace("требования ПАО «Пример» в размере", "требования АО «Другое» в размере")
    assert not extract_candidates(other).claims


def test_inline_queues_preserve_the_explicit_classification_of_fines():
    text = (
        "ОПРЕДЕЛИЛ:\nВключить требования ФНС России во вторую очередь реестра требований "
        "кредиторов должника в размере 100 руб. – основной долг, в третью очередь 20 руб. "
        "– основной долг, отдельно в третью очередь 10 руб. – пени, 5 руб. – штрафы."
    )
    second, third = fields(text)["claims"]["value"]
    assert (second["priority_queue"], second["total_amount"]) == ("2 очередь", "100.00")
    assert (third["priority_queue"], third["principal_debt"]) == ("3 очередь", "20.00")
    assert third["financial_sanctions"] == "15.00"


def test_fee_terminated_outside_the_itemized_admission():
    text = (
        "ОПРЕДЕЛИЛ:\nВключить в третью очередь реестра требований кредиторов требование ООО «Пример» "
        "в размере 100 руб., из которых: 80 руб. – основной долг, 20 руб. – неустойка. "
        "Прекратить производство по обособленному спору в части требования в размере 5 руб. – "
        "судебных расходов по уплате государственной пошлины."
    )
    row, = fields(text)["claims"]["value"]
    assert (row["claimed_amount"], row["pau_only_amount"], row["total_amount"]) == ("100.00", "0.00", "100.00")


def test_joint_debt_declaration_does_not_supersede_the_same_contract_basis():
    text = (
        "ОПРЕДЕЛИЛ:\nВключить в третью очередь требование ПАО «Пример» в размере 100 руб., "
        "основанное на кредитном договоре от 17.02.2021 №12345. "
        "Признать обязательства по кредитному договору от 17.02.2021 №12345 общими обязательствами супругов."
    )
    result = extract_candidates(text).fields
    assert result["grounds_ocr"].value == "основанное на кредитном договоре от 17.02.2021 №12345"
    row, = fields(text)["claims"]["value"]
    assert row["priority_queue"] == "3 очередь"
    assert row["contract_number"] == "12345"
    assert row["is_salary_or_severance"] is False
    assert row["pledge_resolution"] is None
    other = text.replace("№12345 общими", "№54321 общими")
    assert not extract_candidates(other).claims


def test_tax_nonpayment_ground_matches_the_admitted_filing_amount():
    ground = "которые возникли в результате неисполнения должником обязательств по уплате налога"
    text = (
        "Рассмотрев заявление ФНС России о включении в реестр требований кредиторов должника "
        f"задолженности в размере 100 руб. 00 коп., {ground}, ОПРЕДЕЛИЛ:\n"
        "Признать обоснованным и включить в третью очередь реестра требований кредиторов должника "
        "требование ФНС России в размере 100 руб. – основной долг."
    )
    result = extract_candidates(text).fields
    assert result["grounds_ocr"].value == ground
    assert result["grounds"].value == explicit_ground_category(result["grounds"].quote) == "Налоговая задолженность"
    different = text.replace("100 руб. 00 коп.", "101 руб. 00 коп.")
    assert "grounds" not in extract_candidates(different).fields
    assert "grounds_ocr" not in extract_candidates(different).fields


def test_gross_is_computable_without_classifying_an_itemized_court_cost():
    text = (
        "ОПРЕДЕЛИЛ:\nВключить требования ПАО «Пример» в третью очередь реестра требований кредиторов "
        "в размере 100 руб. – основной долг, 20 руб. – проценты, 4 руб. – судебные расходы, "
        "отдельно в третью очередь 10 руб. – неустойка. "
        "Требования ПАО «Пример» в размере 7 руб. – государственная пошлина признать подлежащими "
        "удовлетворению в порядке пункта 3 статьи 137."
    )
    row, = fields(text)["claims"]["value"]
    assert row["financial_sanctions"] == "10.00"
    assert row["principal_debt"] is None and row["claimed_amount"] is None
    assert (row["pau_only_amount"], row["total_amount"]) == ("7.00", "141.00")


def test_embedded_fee_is_historical_when_the_award_matches_the_cited_order():
    text = (
        "ОПРЕДЕЛИЛ:\nПризнать обоснованным и включить требование ООО «Пример» в третью очередь "
        "реестра требований кредиторов должника на основании судебного приказа мирового судьи "
        "судебного участка № 17 от 06.04.2026 по делу № 2-18/2026 в размере 110 руб. "
        "по кредитному договору № 175 от 14.06.2024, в том числе: 100 руб. основного долга, "
        "3 руб. неустойки, 7 руб. расходов по оплате госпошлины. Требования в части неустойки "
        "и государственной пошлины учитываются отдельно в реестре требований кредиторов третьей очереди."
    )
    row, = fields(text)["claims"]["value"]
    assert (row["principal_debt"], row["financial_sanctions"]) == ("107.00", "3.00")
    assert (row["pau_only_amount"], row["total_amount"]) == ("0.00", "110.00")


def test_payment_from_remaining_property_after_registry_claims():
    text = (
        "ОПРЕДЕЛИЛ:\nТребование АО КБ «Пример» в размере 100 руб. признать обоснованным и "
        "подлежащим удовлетворению за счет имущества, оставшегося после погашения требований "
        "кредиторов, включенных в реестр требований кредиторов должника. "
        "Признать требование в размере 7 руб. судебных расходов по оплате государственной "
        "пошлины за рассмотрение настоящего заявления подлежащим удовлетворению в порядке пункта 3 статьи 137."
    )
    row, = fields(text)["claims"]["value"]
    assert row["priority_queue"] == "За реестром"
    assert (row["claimed_amount"], row["pau_only_amount"], row["total_amount"]) == ("100.00", "7.00", "107.00")
    assert row["principal_debt"] is None


def test_common_award_includes_court_expense_without_assuming_its_type():
    text = (
        "ОПРЕДЕЛИЛ:\nВключить требование ПАО «Пример» задолженности по кредитным договорам "
        "от 20.11.2023 №123, 21.11.2024 №456 в размере 105 руб., из которых: "
        "100 руб. – основной долг, 5 руб. – неустойка, в третью очередь реестра требований кредиторов. "
        "В соответствии с пунктом 3 статьи 137 Федерального закона «О несостоятельности (банкротстве)» "
        "требования об установлении 5 руб. – неустойка учесть отдельно в реестре требований кредиторов. "
        "Признать требования кредитора в размере 7 руб. судебных расходов подлежащими удовлетворению "
        "применительно к пункту 3 статьи 137 Федерального закона."
    )
    row, = fields(text)["claims"]["value"]
    assert row["contract_number"] is None
    assert (row["financial_sanctions"], row["claimed_amount"], row["total_amount"]) == ("5.00", "105.00", "112.00")
    assert row["pau_only_amount"] is None
    assert _claim_key(0, "included_total") in _fallback_schema(extract_candidates(text).claims, {}, set())
    assert not extract_candidates(text.replace("105 руб.", "106 руб.", 1)).claims


def test_malformed_penalty_requires_the_printed_total_to_corroborate_the_cents():
    text = (
        "ОПРЕДЕЛИЛ:\nПризнать обоснованным и включить требование ФНС России в размере "
        "121,59 руб., из которых: 100,01 руб. основного долга, 21,587 руб. пеней "
        "в третью очередь реестра требований кредиторов."
    )
    row, = fields(text)["claims"]["value"]
    assert (row["principal_debt"], row["financial_sanctions"]) == ("100.01", "21.58")
    assert (row["claimed_amount"], row["total_amount"]) == ("121.59", "121.59")
    wrong_total = text.replace("121,59", "121,60")
    row, = fields(wrong_total)["claims"]["value"]
    assert row["financial_sanctions"] is None
    assert row["claimed_amount"] is None and row["total_amount"] == "121.60"


def test_labeled_commission_is_in_the_award_but_not_a_penalty():
    text = (
        "ОПРЕДЕЛИЛ:\nВключить требования ПАО «Пример» по соглашению о кредитовании № 123 "
        "в третью очередь реестра требований кредиторов в размере 130 руб., а именно: "
        "основной долг: 100 руб., проценты: 20 руб., штрафы и неустойки: 3 руб., "
        "комиссию за обслуживание счета: 7 руб. Взыскать в пользу ПАО «Пример» "
        "государственную пошлину в размере 5 руб. за рассмотрение настоящего заявления."
    )
    candidate, = extract_candidates(text).claims
    assert candidate.total.scope == "without_duty"
    assert _claim_key(0, "included_total") in _fallback_schema([candidate], {}, set())
    row, = fields(text)["claims"]["value"]
    assert (row["financial_sanctions"], row["claimed_amount"], row["total_amount"]) == ("3.00", "130.00", "135.00")
    assert row["principal_debt"] is None


def test_numbered_contract_subtotals_are_kept_separate_and_reconciled():
    text = (
        "ОПРЕДЕЛИЛ:\nПризнать обоснованными и включить в третью очередь реестра требований кредиторов "
        "требования ПАО «Пример» в размере 150 руб., из которых: "
        "1) 100 руб. – по кредитному договору от 18.03.2025 №123, из них: "
        "95 руб. – основной долг, 5 руб. – пени, "
        "2) 50 руб. – по кредитному договору от 23.08.2024 №456, из них: "
        "45 руб. – основной долг, 5 руб. – проценты. "
        "Взыскать в пользу ПАО «Пример» государственную пошлину в размере 7 руб."
    )
    result = fields(text)
    first, second, fee = result["claims"]["value"]
    assert (first["contract_number"], first["claimed_amount"]) == ("123", "100.00")
    assert (second["contract_number"], second["claimed_amount"]) == ("456", "50.00")
    assert (fee["contract_number"], fee["pau_only_amount"]) == (None, "7.00")
    assert result["total_claimed_amount"]["value"] == "150.00"
    assert not extract_candidates(text.replace("150 руб.", "151 руб.", 1)).claims


def test_secured_contract_does_not_transfer_pledge_or_court_cost_to_another_contract():
    text = (
        "ОПРЕДЕЛИЛ:\nВключить в третью очередь реестра требований кредиторов требования ПАО «Пример»: "
        "- в рамках кредитного договора: № 123 от 21.01.2025, обеспеченное залогом автомобиля, "
        "в размере 100.00, из которых: 95 руб. – основной долг, 5 руб. – пени; "
        "- в рамках кредитного договора: № 456 от 03.09.2025 в размере 50.00, "
        "из которых: 45 руб. – основной долг, 5 руб. – проценты, "
        "Признать обоснованными понесенные ПАО «Пример» судебные расходы по делу № А01-123/2026 "
        "в размере 7 руб., и подлежащими удовлетворению применительно к пункту 3 статьи 137."
    )
    result = fields(text)
    first, second, expense = result["claims"]["value"]
    assert (first["contract_number"], first["pledge_resolution"], first["claimed_amount"]) == ("123", "yes", "100.00")
    assert (second["contract_number"], second["pledge_resolution"], second["claimed_amount"]) == ("456", None, "50.00")
    assert (expense["priority_queue"], expense["pau_only_amount"], expense["total_amount"]) == (None, None, "7.00")
    assert result["total_claimed_amount"]["value"] == "150.00"


def test_nested_queue_subtotals_reconcile_both_tax_and_penalties():
    text = (
        "ОПРЕДЕЛИЛ:\nВключить в реестр требований кредиторов требования ФНС России "
        "в сумме 125 руб., в том числе: "
        "- во вторую очередь реестра требований кредиторов в сумме 100 руб., в том числе: по налогу – 100 руб.; "
        "- в третью очередь реестра требований кредиторов в сумме 25 руб., в том числе: "
        "по налогу – 20 руб., по пени – 5 руб."
    )
    second, third = fields(text)["claims"]["value"]
    assert (second["priority_queue"], second["principal_debt"]) == ("2 очередь", "100.00")
    assert (third["priority_queue"], third["principal_debt"], third["financial_sanctions"]) == ("3 очередь", "20.00", "5.00")
    assert not extract_candidates(text.replace("25 руб.", "26 руб.", 1)).claims


def test_trailing_local_plan_and_its_refund_are_separate_from_the_registry_award():
    text = (
        "ОПРЕДЕЛИЛ:\nВключить требование ПАО «Пример» в размере 100 руб.: 90 руб. основного долга "
        "и 10 руб. неустойки в третью очередь реестра требований кредиторов. "
        "Судебные расходы по уплате государственной пошлины в размере 7 руб., подлежащие взысканию "
        "с должника в пользу ПАО «Пример», учитываются в реестре по пункту 3 статьи 137. "
        "Утвердить локальный план реструктуризации задолженности в следующей редакции: "
        "1. Задолженность по кредитному договору №321 составляет 900 руб. "
        "Прекратить производство по заявлению ПАО «Пример» о включении в реестр требования, "
        "обеспеченного залогом имущества, по кредитному договору №321. "
        "Возвратить ПАО «Пример» из федерального бюджета государственную пошлину в размере 50 руб."
    )
    candidates = extract_candidates(text)
    assert "grounds" not in candidates.fields
    row, = fields(text)["claims"]["value"]
    assert (row["contract_number"], row["pledge_resolution"]) == (None, None)
    assert (row["claimed_amount"], row["pau_only_amount"], row["total_amount"]) == ("100.00", "7.00", "107.00")


def test_prior_fee_in_printed_award_cites_a_previous_decision_after_the_breakdown():
    text = (
        "ОПРЕДЕЛИЛ:\nПризнать требование ПАО «Пример» обоснованным в размере 120 руб., из них "
        "основной долг – 100 руб., проценты – 15 руб., госпошлина – 5 руб., "
        "по кредитному договору №123 от 09.11.2022, взысканной решением Арбитражного суда "
        "Удмуртской Республики от 27.06.2023 по делу № А71-8630/2023, и подлежащим включению "
        "в реестр требований кредиторов по третьей очереди удовлетворения."
    )
    row, = fields(text)["claims"]["value"]
    assert (row["principal_debt"], row["pau_only_amount"], row["total_amount"]) == ("120.00", "0.00", "120.00")


def test_repeated_ruble_unit_in_interest_requires_a_balanced_award():
    text = (
        "ОПРЕДЕЛИЛ:\nПризнать требование ПАО «Пример» в размере 122 руб. 08 коп., из которых: "
        "100 руб. – основного долга, 20 руб. 08 руб. – процентов, 2 руб. – штрафных санкций "
        "обоснованным и подлежащим включению в третью очередь реестра требований кредиторов."
    )
    row, = fields(text)["claims"]["value"]
    assert (row["principal_debt"], row["financial_sanctions"], row["total_amount"]) == ("120.08", "2.00", "122.08")
    row, = fields(text.replace("122 руб. 08 коп.", "122 руб. 09 коп."))["claims"]["value"]
    assert row["principal_debt"] is None


def test_enforcement_fee_is_preserved_as_literal_basis():
    result = extract_candidates(
        "ОПРЕДЕЛИЛ:\nВключить требования ФНС России в третью очередь в размере "
        "2626 руб. недоимки по исполнительскому сбору."
    ).fields
    assert result["grounds_ocr"].value == "недоимки по исполнительскому сбору"
    assert "grounds" not in result


def test_queue_allocation_requires_an_exact_reconciled_breakdown():
    text = (
        "ОПРЕДЕЛИЛ:\nВключить требования ПАО «Пример» в размере 120 руб. в реестр требований кредиторов. "
        "Установить следующую очередность удовлетворения требований: "
        "100 руб. – требования кредиторов третьей очереди. "
        "20 руб. – требования кредиторов третьей очереди, учитывающиеся отдельно "
        "и подлежащие удовлетворению после погашения основной суммы долга."
    )
    row, = fields(text)["claims"]["value"]
    assert (row["priority_queue"], row["principal_debt"], row["financial_sanctions"]) == (
        "3 очередь", "100.00", "20.00",
    )
    invalid = fields(text.replace("120 руб.", "121 руб."))["claims"]["value"]
    assert all(row["financial_sanctions"] is None for row in invalid)


def test_outside_registry_order_after_express_refusal_to_restore_term():
    text = (
        "ОПРЕДЕЛИЛ:\nВ удовлетворении заявления кредитора о восстановлении срока отказать. "
        "Финансовому управляющему имуществом Примерова Ивана Ивановича включить установленные требования "
        "ООО «Пример» в сумме 100 руб. основного долга, в порядке, установленном пунктом 4 статьи 142."
    )
    row, = fields(text)["claims"]["value"]
    assert row["priority_queue"] == "За реестром"
    without_refusal = text.replace("В удовлетворении заявления кредитора о восстановлении срока отказать. ", "")
    unsupported, = fields(without_refusal)["claims"]["value"]
    assert unsupported["priority_queue"] is None


def test_express_outside_registry_disposition_resolves_previous_registry_refusal():
    text = (
        "ОПРЕДЕЛИЛ:\nТребование ООО «Пример» признать обоснованным в размере 100 руб. "
        "Отказать во включении данных требований в реестр требований кредиторов должника. "
        "Признать данные требования подлежащими удовлетворению за счет имущества должника, оставшегося "
        "после удовлетворения требований кредиторов, включенных в реестр требований кредиторов."
    )
    row, = fields(text)["claims"]["value"]
    assert row["priority_queue"] == "За реестром"
    assert row["principal_debt"] is None
    assert row["claimed_amount"] is None
    assert row["total_amount"] == "100.00"
    assert fields(text.split("Признать данные")[0])["claims"]["value"] == []


def test_express_judicial_order_is_literal_basis_without_a_category():
    text = (
        "ОПРЕДЕЛИЛ:\nТребование ООО «Пример», основанное на судебном приказе № 12-34/2025 "
        "от 16.10.2025, признать обоснованным в размере 100 руб."
    )
    candidates = extract_candidates(text).fields
    assert candidates["grounds_ocr"].value == "основанное на судебном приказе № 12-34/2025 от 16.10.2025"
    assert "grounds" not in candidates
    mentioned = text.replace("основанное на", "указанное в")
    assert "grounds_ocr" not in extract_candidates(mentioned).fields


def test_payment_order_fee_with_separate_operative_accounting_is_current():
    text = (
        "ОПРЕДЕЛИЛ:\nВключить требования ПАО «Пример» в третью очередь в размере 120 руб., "
        "из которых: 100 руб. – основной долг, 20 руб. – неустойка, "
        "а также 5 руб. расходов по оплате государственной пошлины, "
        "уплаченной платежным поручением от 01.01.2026 № 8. Требования в части неустойки "
        "и государственной пошлины учитываются отдельно в реестре требований кредиторов третьей очереди "
        "и подлежат удовлетворению после погашения основной суммы задолженности."
    )
    row, = fields(text)["claims"]["value"]
    assert row["claimed_amount"] == "120.00"
    assert row["pau_only_amount"] == "5.00"
    assert row["total_amount"] == "125.00"
    without_accounting, = fields(text.split(" Требования в части")[0])["claims"]["value"]
    assert without_accounting["pau_only_amount"] is None


def test_claim_contract_number_after_credit_purpose_and_after_colon():
    text = (
        "ОПРЕДЕЛИЛ:\nВключить требования ПАО «Пример» в третью очередь в размере "
        "120 руб. основного долга {contract}."
    )
    for contract, number in (
        ("по договору о предоставлении потребительского кредита (займа) № 1005927201/15 от 29.04.2025", "1005927201/15"),
        ("на основании договора займа: № 9801021703 от 09 марта 2024 года", "9801021703"),
        ("по договору займа от 14.04.2025 № 123-456", "123-456"),
        ("по договору на предоставление возобновляемой кредитной линии № 123-456 от 14.04.2025", "123-456"),
        ("по соглашению о кредитовании от 14.04.2025 № 123-456", "123-456"),
        ("по договору займа от 29.04.2025", None),
    ):
        row, = fields(text.format(contract=contract))["claims"]["value"]
        assert row["contract_number"] == number


def test_single_admitted_amount_can_reference_the_contract_from_the_reasoning(monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock
    from ocr_platform.services import rtk3_regex_jev as service

    reasoning = "Суд установил задолженность по кредитному договору № A-123 от 01.01.2025 в размере 100 руб."
    order = "ОПРЕДЕЛИЛ:\nВключить требования ПАО «Пример» в третью очередь в размере 100 руб. основного долга."
    text = f"{reasoning} {order}"
    claim, = extract_candidates(text).claims
    assert claim.contract_number == "A-123"
    assert claim.contract_reference.quote in text
    assert fields(text)["claims"]["value"][0]["contract_number"] == "A-123"

    different_total, = extract_candidates(text.replace("в размере 100 руб.", "в размере 90 руб.", 1)).claims
    assert different_total.contract_number is None
    ambiguous, = extract_candidates(
        f"{reasoning} Также заявлено требование по кредитному договору № B-456. {order}"
    ).claims
    assert ambiguous.contract_number is None

    profile = {"models": {"verification": {}, "llm_extraction": {}}, "fields": {"claims": {}}}
    monkeypatch.setattr(service, "_verify", AsyncMock(return_value={}))
    monkeypatch.setattr(service, "_gemini", AsyncMock(return_value={}))
    assert asyncio.run(service.extract_fields(text, profile))["claims"]["value"] == []


def test_written_admission_and_separately_awarded_current_duty_survive_unknown_breakdown():
    text = (
        "ОПРЕДЕЛИЛ:\nПризнать обоснованным заявление и включить в третью очередь реестра требований "
        "кредиторов должника требование ПАО «Пример» в размере 120 руб. 60 коп. "
        "Признать обоснованными требования ПАО «Пример» в размере 5 руб. – госпошлины, "
        "уплаченной при подаче настоящего заявления, и подлежащими удовлетворению применительно "
        "к п. 3 ст. 137 Закона о банкротстве."
    )
    result = fields(text)
    row, = result["claims"]["value"]
    assert (row["claimed_amount"], row["total_amount"], row["pau_only_amount"]) == ("120.60", "125.60", "5.00")
    assert row["principal_debt"] is None
    assert row["financial_sanctions"] is None
    assert result["claims"]["validation_issue"]
    assert result["total_claimed_amount"]["value"] == "120.60"
    assert not any(issue.field_name == "total_claimed_amount" for issue in rtk3_issues(result))
    result["total_claimed_amount"]["value"] = "120.59"
    assert any(issue.field_name == "total_claimed_amount" for issue in rtk3_issues(result))

    prior = text.replace("при подаче настоящего заявления", "ранее по другому делу")
    unsupported, = fields(prior)["claims"]["value"]
    assert unsupported["claimed_amount"] is None
    assert unsupported["total_amount"] is None


def test_page_header_between_currency_and_interest_label_keeps_the_printed_amount():
    from ocr_platform.services.rtk3_evidence import amount_supported

    text = (
        "ОПРЕДЕЛИЛ:\nВключить требования ПАО «Пример» в третью очередь в размере 120 руб., "
        "из которых 100 руб. – основной долг, 20 руб.\n5\n36_20510388\n– проценты."
    )
    row, = fields(text)["claims"]["value"]
    assert row["principal_debt"] == "120.00"
    assert row["claimed_amount"] == "120.00"
    assert not amount_supported("20 руб.", "20 руб. 15 коп. — проценты")
    assert not amount_supported("20", "200 руб. — проценты")


def test_separate_fee_under_article_137_keeps_the_admission_amount():
    text = (
        "ОПРЕДЕЛИЛ:\nВключить требования ПАО «Пример» в размере 100 руб. задолженности в третью очередь. "
        "Признать требования ПАО «Пример» к должнику в размере 5 руб. расходов по уплате государственной "
        "пошлины подлежащими удовлетворению в порядке п. 3 ст. 137 Закона о банкротстве."
    )
    row, = fields(text)["claims"]["value"]
    assert (row["claimed_amount"], row["total_amount"]) == ("100.00", "105.00")
    assert row["principal_debt"] is None


def test_separate_fee_accounted_after_debt_uses_both_printed_amounts():
    text = (
        'ОПРЕДЕЛИЛ:\nПризнать требование ПАО «Пример» обоснованным и включить требование '
        'в размере 710.332,27 рублей в третью очередь реестра требований кредиторов. '
        'Требование об оплате государственной пошлины в размере 20.258 рублей учитывать '
        'в составе третьей очереди реестра требований кредиторов с очередностью удовлетворения '
        'после погашения основной задолженности и причитающихся процентов.'
    )
    row, = fields(text)["claims"]["value"]
    assert (row["claimed_amount"], row["pau_only_amount"], row["total_amount"]) == (
        "710332.27", "20258.00", "730590.27",
    )
    assert row["principal_debt"] is None


def test_inline_fee_with_a_relative_clause_about_separate_accounting():
    text = (
        'ОПРЕДЕЛИЛ:\nПризнать обоснованными и включить в третью очередь реестра требований '
        'кредиторов задолженности по кредитному договору от 05.06.2024 в размере '
        '93 525,38 руб. основного долга, 6 474,62 руб. процентов и 5000 руб.госпошлины, '
        'которые учесть в реестре отдельно для удовлетворения после погашения основной суммы задолженности.'
    )
    row, = fields(text)["claims"]["value"]
    assert (row["principal_debt"], row["financial_sanctions"], row["pau_only_amount"], row["total_amount"]) == (
        "100000.00", "0.00", "5000.00", "105000.00",
    )


def test_single_award_for_two_contracts_keeps_only_the_aggregate_amount():
    text = (
        "ОПРЕДЕЛИЛ:\nВключить требование ПАО «Пример» по кредитному договору от 15.11.2024 № A-1, "
        "по кредитному договору от 28.12.2024 № A-2 в общем размере 100 руб. в третью очередь. "
        "Признать требование ПАО «Пример» о возмещении расходов по уплате государственной пошлины "
        "в сумме 5 руб. обоснованным и подлежащим 2 А55-123/2026 удовлетворению "
        "применительно к пункту 3 статьи 137 Закона о банкротстве."
    )
    row, = fields(text)["claims"]["value"]
    assert (row["contract_number"], row["principal_debt"], row["financial_sanctions"]) == (None, None, None)
    assert (row["claimed_amount"], row["pau_only_amount"], row["total_amount"]) == ("100.00", "5.00", "105.00")
    assert row["is_salary_or_severance"] is False


def test_numbered_confirmation_and_third_section_admit_one_amount():
    text = (
        'ОПРЕДЕЛИЛ:\n1. Требование ПАО «Пример» в размере 100 руб.00 коп. признать обоснованным. '
        '2. Включить в третий раздел реестра требований кредиторов должника требование ПАО «Пример» '
        'в размере 100 руб.00 коп., а также государственную пошлину в сумме 5 руб. '
        'с режимом учета и удовлетворения данного требования к должнику в порядке, установленном пунктом 3 статьи 137.'
    )
    row, = fields(text)["claims"]["value"]
    assert (row["priority_queue"], row["claimed_amount"], row["pau_only_amount"], row["total_amount"]) == (
        "3 очередь", "100.00", "5.00", "105.00",
    )
    changed = text.replace("в размере 100 руб.", "в размере 90 руб.", 1)
    assert fields(changed)["claims"]["value"] == []
    filing = "Рассмотрел заявление ПАО «Пример» о включении задолженности по кредитным договорам от 09.03.2021, 25.05.2023"
    sourced, = fields(f"{filing} в размере 100 руб. {text}")["claims"]["value"]
    assert sourced["is_salary_or_severance"] is False
    unconfirmed, = fields(f"{filing} в размере 90 руб. {text}")["claims"]["value"]
    assert unconfirmed["is_salary_or_severance"] is None


def test_fee_payee_can_have_a_line_break_hyphen_in_the_legal_form():
    text = (
        'ОПРЕДЕЛИЛ:\nВключить требования публичного акционерного общества «Пример» '
        'в третью очередь в размере 100 руб. основного долга. '
        'Взыскать в пользу публичного акционерного обще-\nства «Пример» 5 руб. государственной пошлины '
        'в порядке пункта 3 статьи 137.'
    )
    row, = fields(text)["claims"]["value"]
    assert (row["principal_debt"], row["pau_only_amount"], row["total_amount"]) == ("100.00", "5.00", "105.00")


def test_creditor_with_banking_name_inside_or_outside_quotation_marks():
    fields_vtb = extract_candidates(
        'ОПРЕДЕЛИЛ:\nВключить требование публичного акционерного общества «Банк ВТБ» '
        '(ОГРН 1027739609391, ИНН 7702070139) в третью очередь в размере 100 руб.'
    ).fields
    assert fields_vtb["creditor_inn"].value == "7702070139"
    other = extract_candidates(
        'ОПРЕДЕЛИЛ:\nВключить требование ПАО «АК БАРС» БАНК '
        'по кредитному договору № A-1 в третью очередь в размере 100 руб.'
    ).fields
    assert other["creditor"].value == "ПАО «АК БАРС» БАНК"


def test_commission_does_not_prevent_reading_the_explicit_penalty():
    text = (
        'ОПРЕДЕЛИЛ:\nВключить требования АО «Пример» по кредитному договору № A-1 от 01.05.2023 '
        '100 руб. – основной долг, 5 руб. – комиссии, 10 руб. – проценты, '
        '1 руб. – неустойка в третью очередь. Признать обоснованным требование АО «Пример» '
        'к должнику в размере 5 руб. судебных расходов по уплате государственной пошлины, '
        'которые подлежат удовлетворению применительно к пункту 3 статьи 137.'
    )
    row, = fields(text)["claims"]["value"]
    assert row["financial_sanctions"] == "1.00"
    assert row["principal_debt"] is None
    assert (row["claimed_amount"], row["total_amount"]) == ("116.00", "121.00")
    different, = fields(text.replace("– комиссии,", "– комиссии за просрочку,"))["claims"]["value"]
    assert different["claimed_amount"] is None


def test_explicit_loan_breakdown_survives_a_separate_fee_of_unspecified_origin():
    text = (
        "ОПРЕДЕЛИЛ:\nПризнать требования ООО «Пример» по оплате госпошлины в размере 5 руб., "
        "по договору займа от 01.02.2025 № A-1 в размере 120 руб., из них основной долг в размере "
        "100 руб., проценты в размере 15 руб., неустойка в размере 5 руб., "
        "обоснованными и подлежащими удовлетворению за счет имущества, оставшегося после "
        "удовлетворения требований кредиторов, включенных в реестр требований кредиторов."
    )
    result = fields(text)
    loan, expense = result["claims"]["value"]
    assert (loan["principal_debt"], loan["financial_sanctions"], loan["total_amount"]) == ("115.00", "5.00", "120.00")
    assert (expense["total_amount"], expense["principal_debt"], expense["pau_only_amount"]) == ("5.00", None, None)
    assert result["total_claimed_amount"]["value"] is None


def test_single_fee_in_admission_uses_the_courts_explanation_of_separate_accounting():
    reasoning = (
        "Таким образом, государственная пошлина подлежит включению в третью очередь "
        "реестра требований кредиторов с отдельным учётом, для удовлетворения после погашения "
        "основной суммы задолженности в порядке п. 3 ст. 137."
    )
    order = (
        'ОПРЕДЕЛИЛ:\nВключить в третью очередь реестра требований кредиторов требование ПАО «Пример» '
        'в размере: - задолженность по основному долгу 100 000.00 руб. - госпошлина – 5 000,00 руб.'
    )
    row, = fields(f"{reasoning} {order}")["claims"]["value"]
    assert (row["claimed_amount"], row["pau_only_amount"], row["total_amount"]) == ("100000.00", "5000.00", "105000.00")
    assert fields(order)["claims"]["value"][0]["pau_only_amount"] is None


def test_matching_embedded_fee_is_prior_cost_when_the_claim_cites_a_court_order():
    reasoning = (
        "Рассмотрел требование в размере 100 руб. основного долга и 5 руб. государственной пошлины, "
        "основанной на неисполнении договора займа № A-1 от 01.02.2023 (судебный приказ № 2-123/2024)."
    )
    order = (
        'ОПРЕДЕЛИЛ:\nВключить в третью очередь реестра требований кредиторов требование ПАО «Пример» '
        'в размер 100 руб. основного долга и 5 руб. государственной пошлины.'
    )
    row, = fields(f"{reasoning} {order}")["claims"]["value"]
    assert (row["principal_debt"], row["pau_only_amount"], row["total_amount"]) == ("105.00", "0.00", "105.00")
    unless_equal, = fields(f"{reasoning.replace('5 руб.', '6 руб.')} {order}")["claims"]["value"]
    assert unless_equal["principal_debt"] is None


def test_duty_inside_an_admitted_sum_based_on_a_prior_court_order():
    filing = (
        "Рассмотрев заявление ПАО «Пример» о включении требования в сумме 105 руб. "
        "в реестр требований кредиторов должника на основании судебного приказа "
        "от 15.11.2021 по делу № 2-1949/2021."
    )
    decision = (
        "ОПРЕДЕЛИЛ:\nВключить требование ПАО «Пример» в сумме 105 руб., в том числе: "
        "100 руб. – основной долг; 5 руб. – расходы по уплате государственной пошлины, в третью очередь."
    )
    text = f"{filing} {decision}"
    row, = fields(text)["claims"]["value"]
    assert (row["principal_debt"], row["claimed_amount"], row["pau_only_amount"]) == ("105.00", "105.00", "0.00")
    grounds = extract_candidates(text).fields
    assert grounds["grounds_ocr"].value == "на основании судебного приказа от 15.11.2021 по делу № 2-1949/2021"
    assert "grounds" not in grounds

    unitemized, = fields(text.replace("основной долг;", "задолженность;"))["claims"]["value"]
    assert (unitemized["claimed_amount"], unitemized["total_amount"], unitemized["pau_only_amount"]) == (
        "105.00", "105.00", "0.00",
    )
    assert unitemized["principal_debt"] is None and unitemized["financial_sanctions"] is None

    unrelated, = fields(text.replace("в сумме 105 руб.", "в сумме 100 руб.", 1))["claims"]["value"]
    assert unrelated["claimed_amount"] is None
    assert unrelated["pau_only_amount"] is None


def test_inconsistent_source_total_preserves_labelled_parts_for_review():
    text = (
        "ОПРЕДЕЛИЛ:\nВключить требования ПАО «Пример» в размере 120 руб., из которых "
        "100 руб. – основной долг, 19 руб. – неустойка, в третью очередь. "
        "Признать требования ПАО «Пример» в размере 5 руб. государственной пошлины "
        "за рассмотрение настоящего заявления подлежащими удовлетворению в порядке п. 3 ст. 137."
    )
    result = fields(text)
    row, = result["claims"]["value"]
    assert (row["principal_debt"], row["financial_sanctions"], row["pau_only_amount"]) == ("100.00", "19.00", "5.00")
    assert row["claimed_amount"] is None
    assert row["total_amount"] is None
    assert "не совпадают" in result["claims"]["validation_issue"]


def test_unitemized_award_publishes_gross_without_inventing_components():
    text = (
        "ОПРЕДЕЛИЛ:\nПризнать обоснованным и включить требование ООО «Пример» "
        "в состав третьей очереди реестра требований кредиторов в размере 100 руб."
    )
    row, = fields(text)["claims"]["value"]
    assert row["total_amount"] == "100.00"
    assert row["principal_debt"] is None
    assert row["claimed_amount"] is None


def test_explicit_sanctions_subtotal_survives_unitemized_remainder_and_refund():
    text = (
        "ОПРЕДЕЛИЛ:\nВключить требования ПАО «Пример» в третью очередь в размере 120 руб., "
        "из которых штрафные санкции в размере 20 руб. "
        "Возвратить из федерального бюджета 5 руб. государственной пошлины."
    )
    row, = fields(text)["claims"]["value"]
    assert row["financial_sanctions"] == "20.00"
    assert row["pau_only_amount"] is None
    assert row["principal_debt"] is None
    assert row["claimed_amount"] is None
    assert row["total_amount"] == "120.00"
    without_refund, = fields(text.split(" Возвратить")[0])["claims"]["value"]
    assert without_refund["total_amount"] is None


def test_debtor_inn_with_a_two_word_patronymic():
    text = (
        "В рамках дела о несостоятельности (банкротстве) Примеровой Анны Нусрат кызы "
        "(ИНН 123456789012) рассмотрено заявление ООО «Пример». ОПРЕДЕЛИЛ:"
    )
    assert source_inn(text, "debtor_inn").value == "123456789012"


def test_decimal_sanction_repeated_in_separate_accounting_is_counted_once():
    text = (
        "ОПРЕДЕЛИЛ:\nВключить требования ПАО «Пример» в третью очередь в размере 120,15 руб., "
        "из которых: 100 руб. – основной долг, 20,15. – неустойка, "
        "а также 5 руб. расходов по уплате государственной пошлины. "
        "Учесть требования в размере 20,15. – неустойки отдельно в реестре требований кредиторов "
        "после погашения основной суммы задолженности."
    )
    row, = fields(text)["claims"]["value"]
    assert row["financial_sanctions"] == "20.15"
    assert row["pau_only_amount"] is None


def test_inline_contracts_reconcile_without_applying_one_contracts_pledge_to_both():
    text = (
        "ОПРЕДЕЛИЛ:\nВключить требования ПАО «Пример» в третью очередь "
        "в рамках кредитного договора № A-1 в размере 100 руб., "
        "из которых: 90 руб. – основной долг, 10 руб. – проценты, как обеспеченные залогом автомобиля; "
        "в рамках кредитного договора № A-2 в размере 20 руб., "
        "из которых: 18 руб. – основной долг, 2 руб. – неустойка."
    )
    result = fields(text)
    first, second = result["claims"]["value"]
    assert (first["contract_number"], second["contract_number"]) == ("A-1", "A-2")
    assert (first["claimed_amount"], second["claimed_amount"]) == ("100.00", "20.00")
    assert second["financial_sanctions"] == "2.00"
    assert (first["pledge_resolution"], second["pledge_resolution"]) == ("yes", None)
    assert result["pledge_resolution"]["value"] is None
    assert result["secured_by_pledge"]["value"] is None


def test_collateral_refusal_is_linked_to_its_contract_in_the_same_queue():
    text = (
        "ОПРЕДЕЛИЛ:\nВключить требования ПАО «Пример» в третью очередь "
        "по кредитному договору № A-1 в размере 100 руб. основного долга, как обеспеченные залогом. "
        "Включить требования ПАО «Пример» в третью очередь "
        "по кредитному договору № A-2 в размере 100 руб. основного долга. "
        "В удовлетворении заявления о признании требований по кредитному договору № A-2 "
        "обеспеченными залогом отказать."
    )
    result = fields(text)
    rows = result["claims"]["value"]
    assert [(row["contract_number"], row["pledge_resolution"], row["claimed_amount"]) for row in rows] == [
        ("A-1", "yes", "100.00"), ("A-2", "no", "100.00"),
    ]
    assert result["total_claimed_amount"]["value"] == "200.00"
    assert result["pledge_resolution"]["value"] is None
    assert result["secured_by_pledge"]["value"] is None
    assert "validation_issue" not in result["pledge_resolution"]


def test_missing_explicit_basis_and_publication_stay_null_in_model_fallback():
    from ocr_platform.services.rtk3_evidence import Fact
    from ocr_platform.services.rtk3_regex_jev import _scalar_fields

    order = "Включить требования АО „Банк Русский Стандарт“ в размере 100 руб. основного долга."
    publication = "Дата публикации в КАД: 05.10.2026"
    text = f"{publication}\nОПРЕДЕЛИЛ:\n{order}"
    proposals = extract_candidates(text).fields
    result = _scalar_fields(text, {"creditor": {}, "grounds": {}, "kad_publication_date": {}}, {}, {
        "creditor": Fact(value="БРС", quote=order),
        "grounds": Fact(value="Кредитный договор", quote=order),
        "kad_publication_date": Fact(value="05.10.2026", quote=publication),
    }, proposals)
    assert result["creditor"]["value"] == "АО «Банк Русский Стандарт»"
    assert result["grounds"]["value"] is None
    assert result["kad_publication_date"]["value"] is None


def test_a_single_listed_cost_supports_the_gross_but_has_unknown_provenance():
    text = (
        "ОПРЕДЕЛИЛ:\nВключить требования ПАО «Пример» в третью очередь "
        "по договору займа на сумму 5 руб., в том числе: 5 руб. – судебные расходы."
    )
    row, = fields(text)["claims"]["value"]
    assert row["total_amount"] == "5.00"
    assert row["financial_sanctions"] == "0.00"
    assert row["principal_debt"] is None
    assert row["pau_only_amount"] is None


def test_loan_interest_confirms_non_salary_claim_with_unclassified_cost():
    text = (
        "ОПРЕДЕЛИЛ:\nВключить требования ПАО «Пример» в третью очередь в размере 100 руб., "
        "из которых: 95 руб. – проценты за пользование займом, 5 руб. – судебные расходы."
    )
    row, = fields(text)["claims"]["value"]
    assert row["is_salary_or_severance"] is False
    assert row["pau_only_amount"] is None


def test_nested_contract_subtotals_are_not_counted_twice():
    quote = (
        "Включить 300 руб., в том числе: "
        "- по кредитному договору №1 в размере 100 руб., из которых: "
        "80 руб. основной долг, 20 руб. проценты; "
        "- по кредитному договору №2 в размере 200 руб., из которых: "
        "150 руб. основной долг, 50 руб. проценты."
    )
    data, total = claim_amounts(quote, None)
    assert total.amount == "300 руб."
    assert len(data["principal_debt"].items) == 4
    assert data["financial_sanctions"].items == []
    assert nested_contract_amounts(quote.replace("300 руб.", "301 руб.", 1), None) is None


def test_numbered_contracts_are_preserved_when_the_printed_parent_total_disagrees(monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock
    from ocr_platform.services import rtk3_regex_jev as service

    text = (
        "ОПРЕДЕЛИЛ:\n1. Включить требования ПАО «Пример» в третью очередь в сумме 121 руб., в том числе: "
        "1.1. требования по кредитному договору № A-1 в размере 100 руб., "
        "из которых 100 руб. – основной долг, как обеспеченные залогом; "
        "1.2. требования по кредитному договору № A-2 в размере 20 руб., из которых 20 руб. – основной долг."
    )
    result = fields(text)
    first, second = result["claims"]["value"]
    assert [(row["contract_number"], row["claimed_amount"], row["pledge_resolution"]) for row in (first, second)] == [
        ("A-1", "100.00", "yes"), ("A-2", "20.00", None),
    ]
    assert result["total_claimed_amount"]["value"] is None
    assert "Общий итог в акте расходится" in result["claims"]["validation_issue"]
    balanced = fields(text.replace("121 руб.", "120 руб.", 1))
    assert balanced["total_claimed_amount"]["value"] == "120.00"
    profile = {
        "models": {"verification": {}, "llm_extraction": {}},
        "fields": {"claims": {}, "total_claimed_amount": {}},
    }
    monkeypatch.setattr(service, "_verify", AsyncMock(return_value={}))
    monkeypatch.setattr(service, "_gemini", AsyncMock(return_value={"claims_complete": True, "document_included_total": service.CourtTotal(
        amount="121 руб.", scope="without_duty", quote="в сумме 121 руб., в том числе")}))
    monkeypatch.setattr(service, "_model_claims", lambda *args: balanced)
    fallback = asyncio.run(service.extract_fields(text, profile))
    assert fallback["total_claimed_amount"]["value"] is None
    assert "Общий итог в акте расходится" in fallback["claims"]["validation_issue"]


def test_contract_list_with_date_before_number_and_unreconciled_itemization():
    text = (
        "ОПРЕДЕЛИЛ:\nПризнать установленным и подлежащим включению в третью очередь реестра требований "
        "кредиторов требование ПАО «Пример» на общую сумму 120 руб., в том числе: "
        "по кредитному договору от 01.01.2025 № A-1 на сумму 100 руб., в том числе "
        "90 руб. – основной долг, 9 руб. – неустойка; "
        "по кредитному договору от 02.02.2025 № A-2 на сумму 20 руб., из которых 20 руб. – основной долг."
    )
    result = fields(text)
    first, second = result["claims"]["value"]
    assert (first["contract_number"], first["total_amount"], first["claimed_amount"]) == ("A-1", "100.00", None)
    assert (second["contract_number"], second["total_amount"], second["claimed_amount"]) == ("A-2", "20.00", "20.00")
    assert result["total_claimed_amount"]["value"] is None


def test_contract_list_after_iz_kotorykh_preserves_the_separate_amounts():
    text = (
        'ОПРЕДЕЛИЛ:\nВключить требования ПАО «Пример» в третью очередь в размере 120 руб., из которых: '
        'по кредитному договору № A-1 составляет – 100 руб., из них: 100 руб. – основной долг; '
        'по кредитному договору № A-2 составляет – 20 руб., из них: 20 руб. – основной долг.'
    )
    rows = fields(text)["claims"]["value"]
    assert [(r["contract_number"], r["claimed_amount"]) for r in rows] == [("A-1", "100.00"), ("A-2", "20.00")]


def test_credit_agreements_listed_without_a_printed_overall_total():
    text = (
        'ОПРЕДЕЛИЛ:\nПризнать требование ПАО «Пример» обоснованным и включить в третью очередь '
        'реестра требований кредиторов должника: по соглашению о кредитовании № A-1 в сумме: '
        '100 руб. – основной долг; 10 руб. – проценты; '
        'по соглашению о кредитовании № A-2 в сумме: 20 руб. – основной долг; 2 руб. – неустойка.'
    )
    result = fields(text)
    assert [(r["contract_number"], r["claimed_amount"]) for r in result["claims"]["value"]] == [
        ("A-1", "110.00"), ("A-2", "22.00"),
    ]
    assert result["total_claimed_amount"]["value"] == "132.00"
    with_fee = text.rstrip(".") + (
        '; 5 руб. – расходы на оплату государственной пошлины, в порядке положений пункта 3 статьи 137.'
    )
    rows = fields(with_fee)["claims"]["value"]
    assert [(row["contract_number"], row["pau_only_amount"]) for row in rows] == [
        ("A-1", "0.00"), ("A-2", "0.00"), (None, "5.00"),
    ]


def test_penalty_label_with_a_qualifying_clause_before_the_amount():
    text = (
        "ОПРЕДЕЛИЛ:\nВключить требования ПАО «Пример» в третью очередь в размере 120 руб., в том числе: "
        "- основной долг – 100 руб.; - пени за просрочку возврата кредита, "
        "начисленные на сумму невозвращенного в срок кредита – 20 руб."
    )
    row, = fields(text)["claims"]["value"]
    assert (row["principal_debt"], row["financial_sanctions"], row["claimed_amount"]) == ("100.00", "20.00", "120.00")
    unlabeled = text.replace("пени за просрочку возврата кредита, ", "")
    assert fields(unlabeled)["claims"]["value"][0]["financial_sanctions"] is None


def test_embedded_current_fee_and_net_total():
    text = (
        "ОПРЕДЕЛИЛ:\nВключить требования ПАО «Тест» в третью очередь в размере 100 руб., "
        "из которых: 80 руб. основной долг, 20 руб. проценты. "
        "Судебные расходы по уплате государственной пошлины в размере 5 руб., "
        "подлежащие взысканию в пользу ПАО «Тест», учесть по п. 3 ст. 137."
    )
    result = fields(text)
    row = result["claims"]["value"][0]
    assert row["principal_debt"] == "100.00"
    assert row["pau_only_amount"] == "5.00"
    assert row["claimed_amount"] == "100.00"
    assert row["total_amount"] == "105.00"
    assert result["pledge_resolution"]["value"] is None


def test_multiple_queues_do_not_allocate_an_unassigned_fee():
    text = (
        "ОПРЕДЕЛИЛ:\nВключить требования ПАО «Тест» во вторую очередь "
        "в размере 100 руб. основного долга. "
        "Включить требования ПАО «Тест» в третью очередь в размере 20 руб. штрафов. "
        "Взыскать в пользу ПАО «Тест» 5 руб. государственной пошлины по настоящему заявлению."
    )
    assert extract_candidates(text).claims == []


def test_separate_fee_has_the_queue_expressly_stated_in_subsequent_accounting():
    text = (
        "ОПРЕДЕЛИЛ:\nВключить требования ПАО «Пример» в третью очередь "
        "в размере 120 руб., в том числе: "
        "- по кредитному договору № A-1 в размере 100 руб., из которых 100 руб. – основной долг; "
        "- по кредитному договору № A-2 в размере 20 руб., из которых 20 руб. – основной долг. "
        "Взыскать в пользу ПАО «Пример» 5 руб. расходов по оплате государственной пошлины. "
        "Учесть требование в части штрафа, судебных расходов по уплате государственной пошлины "
        "в составе третьей очереди реестра требований кредиторов после погашения основной суммы задолженности."
    )
    first, second, fee = fields(text)["claims"]["value"]
    assert (first["pau_only_amount"], second["pau_only_amount"]) == ("0.00", "0.00")
    assert (fee["priority_queue"], fee["pau_only_amount"], fee["contract_number"]) == ("3 очередь", "5.00", None)
    assert fee["pledge_resolution"] is None
    without_queue = text.split(" Учесть")[0]
    assert fields(without_queue)["claims"]["value"][-1]["priority_queue"] is None
    secured_fee = text.replace("пошлины. ", "пошлины, учесть как обеспеченные залогом. ", 1)
    result = fields(secured_fee)
    assert result["claims"]["value"][-1]["pledge_resolution"] == "yes"
    assert result["claims"]["value"][-1]["secured_by_pledge"] is True
    assert result["pledge_resolution"]["value"] is None


def test_tax_creditors_keep_the_name_printed_in_the_act():
    federal = "ОПРЕДЕЛИЛ:\nВключить требования ФНС России в третью очередь в размере 100 руб. основного долга."
    name = "Межрайонной инспекции Федеральной налоговой службы № 7 по Омской области"
    inspection = federal.replace("ФНС России", name)
    assert extract_candidates(federal).fields["creditor"].value == "ФНС России"
    assert extract_candidates(federal.replace("ФНС России", "Федеральной налоговой службы")).fields["creditor"].value == (
        "Федеральной налоговой службы"
    )
    candidates = extract_candidates(inspection)
    assert candidates.fields["creditor"].value == name
    assert candidates.creditor_tax_subject == name


def test_tax_creditor_picks_the_named_inspection_inn_from_the_application():
    text = (
        "Рассмотрев заявление ФНС России (ИНН 0123456789) в лице МИФНС России №7 по Омской области "
        "(ИНН 1111111199) о включении в реестр требований.\n"
        "ОПРЕДЕЛИЛ:\nВключить требования ФНС России (ИНН 0123456789) в третью очередь."
    )
    candidates = extract_candidates(text)
    assert candidates.fields["creditor"].value == "ФНС России в лице МИФНС России №7 по Омской области"
    assert candidates.fields["creditor_inn"].value == "1111111199"
    missing = extract_candidates(text.replace("(ИНН 1111111199)", ""))
    assert "creditor_inn" not in missing.fields


def test_tax_creditor_merges_an_office_with_its_explicit_federal_representation():
    text = (
        "ОПРЕДЕЛИЛ:\nЗаявление ФНС России в лице МИФНС России №7 по Омской области удовлетворить. "
        "Включить требования Межрайонной ИФНС России №7 по Омской области (ИНН 1111111199) в третью очередь. "
        "Признать требование ФНС России (ИНН 0123456789) обоснованным."
    )
    candidates = extract_candidates(text)
    assert candidates.fields["creditor"].value == "ФНС России в лице МИФНС России №7 по Омской области"
    assert candidates.fields["creditor_inn"].value == "1111111199"


def test_tax_inn_fallback_checks_the_office_named_after_v_litse():
    from ocr_platform.services.rtk3_evidence import Fact
    from ocr_platform.services.rtk3_regex_jev import _model_fact

    federal = "ФНС России (ИНН 0123456789)"
    office = "МИФНС России №7 по Омской области (ИНН 1111111199)"
    other_office = "МИФНС России №8 по Омской области (ИНН 2222222299)"
    text = f"{federal} в лице {office}. {other_office}"
    subject = "МИФНС России №7 по Омской области"
    claimant = f"ФНС России в лице {subject}"
    assert _model_fact("creditor_inn", Fact(value="1111111199", quote=office),
                       text, claimant, subject) == "1111111199"
    assert _model_fact("creditor_inn", Fact(value="0123456789", quote=federal),
                       text, claimant, subject) is None
    assert _model_fact("creditor_inn", Fact(value="2222222299", quote=other_office),
                       text, claimant, subject) is None


def test_tax_registry_checks_exact_office_and_registration_period(monkeypatch):
    from datetime import datetime, timezone
    from ocr_platform.services import rtk3_regex_jev as service

    def timestamp(year):
        return int(datetime(year, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)

    def entry(name, inn, status, registered, liquidated=None):
        return {"value": name, "data": {"inn": inn, "state": {
            "status": status, "registration_date": timestamp(registered),
            "liquidation_date": timestamp(liquidated) if liquidated else None,
        }}}

    office = "Межрайонная ИФНС России № 7 по Омской области"
    other = "Межрайонная ИФНС России № 8 по Омской области"
    queries = []

    def suggestions(query):
        queries.append(query)
        return [
            entry(other, "0123456789", "ACTIVE", 2010),
            entry(office, "1111111199", "LIQUIDATED", 2010, 2020),
            entry(office, "2222222299", "ACTIVE", 2020),
            entry("Федеральная налоговая служба", "7707329152", "ACTIVE", 2004),
        ]

    monkeypatch.setattr(service, "dadata_party_suggestions", suggestions)
    assert service.registered_tax_inn("МИФНС России №7 по Омской области", "18.11.2026") == "2222222299"
    assert service.registered_tax_inn(office, "01.01.2018") == "1111111199"
    assert service.registered_tax_inn("ФНС России №7 по Омской области", "18.11.2026") == "2222222299"
    assert service.registered_tax_inn("ФНС России", "18.11.2026") == "7707329152"
    assert service.registered_tax_inn("Федеральной налоговой службы России", "18.11.2026") == "7707329152"
    assert service.registered_tax_inn("ФНС России", "01.01.2003") is None
    assert service.registered_tax_inn("МИФНС России №7", "18.11.2026") is None
    assert queries == ["Межрайонная ИФНС России № 7 по омской области"] * 2 + ["ИФНС № 7 по омской области"] + ["ФНС России"] * 3


def test_tax_registry_checks_printed_and_ambiguous_inns_and_marks_the_route(monkeypatch):
    import asyncio
    from ocr_platform.services import rtk3_regex_jev as service

    office = "МИФНС России №7 по Омской области"
    calls = []

    def lookup(subject, act_date):
        calls.append((subject, act_date))
        return "1111111199"

    monkeypatch.setattr(service, "registered_tax_inn", lookup)
    result = {"creditor": {"value": f"ФНС России в лице {office}"}, "creditor_inn": {"value": None},
              "inclusion_date": {"value": "18.11.2026"}}
    printed = f"{office} (ИНН 2222222299)"
    asyncio.run(service._enrich_creditor_inn(result, printed, office))
    asyncio.run(service._enrich_creditor_inn(result, f"{printed}. {office} (ИНН 3333333399)", office))
    assert calls == [(office, "18.11.2026")] * 2
    asyncio.run(service._enrich_creditor_inn(result, office, office))
    assert calls == [(office, "18.11.2026")] * 3
    assert result["creditor_inn"]["value"] == "1111111199"
    assert result["creditor_inn"]["extraction_route"] == "dadata_party_registry"


def test_extract_fields_preserves_tax_spelling_and_looks_up_the_named_authority(monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock, Mock

    from ocr_platform.services import rtk3_regex_jev as service
    from ocr_platform.services.rtk3_evidence import Fact

    async def verify(text, candidates, config, context):
        return {name: service.Verified(value, .99) for name, value in candidates.fields.items()}

    profile = {"models": {"verification": {}, "llm_extraction": {}}, "fields": {"creditor": {}, "creditor_inn": {}}}
    office = "МИФНС России №7 по Омской области"
    lookup = Mock(side_effect={"ФНС России": "7707329152", office: "1111111199"}.get)
    monkeypatch.setattr(service, "_verify", verify)
    monkeypatch.setattr(service, "_gemini", AsyncMock(return_value={}))
    monkeypatch.setattr(service, "registered_tax_inn", lookup)

    federal = "Арбитражный суд г. Кирова.\nОПРЕДЕЛИЛ:\nВключить требования ФНС России в третью очередь."
    fields = asyncio.run(service.extract_fields(federal, profile))
    assert (fields["creditor"]["value"], fields["creditor_inn"]["value"]) == ("ФНС России", "7707329152")
    lookup.assert_called_once_with("ФНС России", None)
    lookup.reset_mock()

    printed = federal.replace("ФНС России в третью", "ФНС России (ИНН 7707329152) в третью")
    fields = asyncio.run(service.extract_fields(printed, profile))
    assert fields["creditor_inn"]["value"] == "7707329152"
    lookup.assert_called_once_with("ФНС России", None)
    lookup.reset_mock()

    representation = f"Рассмотрев заявление ФНС России (ИНН 7707329152) в лице {office}.\n{federal}"
    monkeypatch.setattr(service, "_verify", AsyncMock(return_value={}))
    monkeypatch.setattr(service, "_gemini", AsyncMock(return_value={
        "creditor": Fact(value="ФНС России", quote="требования ФНС России в третью очередь"),
    }))
    fields = asyncio.run(service.extract_fields(representation, profile))
    assert fields["creditor"]["value"] == f"ФНС России в лице {office}"
    assert fields["creditor_inn"]["value"] == "1111111199"
    lookup.assert_called_once_with(office, None)


def test_company_registry_checks_exact_legal_name_registration_dates_and_ambiguity(monkeypatch):
    from datetime import datetime, timezone
    from unittest.mock import Mock
    from requests.exceptions import ReadTimeout
    from ocr_platform.services import rtk3_regex_jev as service

    def timestamp(year):
        return int(datetime(year, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)

    def entry(name, inn, *, branch="MAIN", closed=None, ogrn="1000000000000"):
        return {"value": name, "data": {"inn": inn, "ogrn": ogrn, "type": "LEGAL", "branch_type": branch, "state": {
            "status": "LIQUIDATED" if closed else "ACTIVE",
            "registration_date": timestamp(2010),
            "liquidation_date": timestamp(closed) if closed else None,
        }}}

    matches = [
        entry('ООО "Пример-Регион"', "1111111111"),
        entry('ООО "Пример"', "2222222222", branch="BRANCH"),
        entry('ООО "Пример"', "9999999999", ogrn=None),
        entry('ООО "Пример"', "3333333333", closed=2019),
        entry('Общество с ограниченной ответственностью "ПРИМЕР"', "4444444444"),
    ]
    lookup = Mock(side_effect=[ReadTimeout(), matches, matches, matches])
    monkeypatch.setattr(service, "dadata_party_suggestions", lookup)
    assert service.registered_company_inn("ООО «Пример»", "18.11.2026") == "4444444444"
    assert lookup.call_count == 2
    assert service.registered_company_inn("ООО «Пример»", "18.11.2017") is None
    matches.append(entry('ООО "Пример"', "5555555555"))
    assert service.registered_company_inn("ООО «Пример»", "18.11.2026") is None
    matches[:] = [entry('НЕПУБЛИЧНОЕ АКЦИОНЕРНОЕ ОБЩЕСТВО "ПРИМЕР-БАНК"', "4444444444")]
    lookup.side_effect = None
    lookup.return_value = matches
    assert service.registered_company_inn("НАО «Пример - Банк»", "18.11.2026") == "4444444444"
    lookup.assert_called_with("НАО «Пример-Банк»")
    matches[:] = [entry('ООО "ПКО "ФИНТРАСТ"', "4444444444")]
    assert service.registered_company_inn("ООО ПКО «Финтраст»", "18.11.2026") == "4444444444"


def test_extract_fields_queries_company_with_printed_ambiguous_and_missing_inns(monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock, Mock
    from ocr_platform.services import rtk3_regex_jev as service

    async def verify(text, candidates, config, context):
        return {name: service.Verified(value, .99) for name, value in candidates.fields.items()}

    company = "ООО «Пример»"
    text = f"ОПРЕДЕЛИЛ:\nВключить требования {company} в третью очередь в размере 100 руб. основного долга."
    profile = {"models": {"verification": {}, "llm_extraction": {}}, "fields": {"creditor": {}, "creditor_inn": {}}}
    monkeypatch.setattr(service, "_verify", verify)
    monkeypatch.setattr(service, "_gemini", AsyncMock(return_value={}))
    lookup = Mock(return_value="1111111111")
    monkeypatch.setattr(service, "registered_company_inn", lookup)

    printed = text.replace(company, f"{company} (ИНН 2222222222)")
    result = asyncio.run(service.extract_fields(printed, profile))
    assert result["creditor_inn"]["value"] == "1111111111"
    assert "2222222222" in result["creditor_inn"]["reasoning"]
    ambiguous = f"{printed}\n{company} (ИНН 3333333333)."
    result = asyncio.run(service.extract_fields(ambiguous, profile))
    assert result["creditor_inn"]["value"] == "1111111111"
    result = asyncio.run(service.extract_fields(text, profile))
    assert lookup.call_count == 3
    lookup.assert_called_with(company, None)
    assert result["creditor_inn"]["value"] == "1111111111"
    assert result["creditor_inn"]["extraction_route"] == "dadata_party_registry"
    assert "external_data_required" not in result["creditor_inn"]


def test_explicit_uncertain_fee_origin_does_not_become_zero():
    text = (
        "ОПРЕДЕЛИЛ:\nВключить требования ПАО «Тест» в третью очередь в размере 105 руб., "
        "из которых: 100 руб. основного долга и 5 руб. госпошлины."
    )
    result = fields(text)["claims"]["value"][0]
    assert result["pau_only_amount"] is None
    assert result["claimed_amount"] is None


def test_previous_surname_label_and_debtor_role_after_name():
    from ocr_platform.services.rtk3_requisites import source_inn
    text = ('Рассмотрев заявление о включении в реестр требований кредиторов '
            'Тестова (ранее - Примерова) Анна Ивановна (ИНН 012345678901). ОПРЕДЕЛИЛ:')
    assert source_inn(text, 'debtor_inn').value == '012345678901'
    text = 'к Тестовой Анне Ивановне (должник, ИНН 012345678901). ОПРЕДЕЛИЛ:'
    assert source_inn(text, 'debtor_inn').value == '012345678901'


def test_unreadable_separate_fee_preserves_itemized_debt():
    text = (
        'ОПРЕДЕЛИЛ:\nВключить требования ПАО «Тест» в третью очередь в размере 120 руб., '
        'из которых: 100 руб. основной долг, 20 руб. неустойка. '
        'Взыскать с должника расходы по оплате государственной пошлины за подачу '
        'заявления о рассмотрении требования о включении в реестр требований кредиторов '
        'по платежному поручению.'
    )
    result = fields(text)
    row = result['claims']['value'][0]
    assert row['priority_queue'] == '3 очередь'
    assert row['principal_debt'] == '100.00'
    assert row['financial_sanctions'] == '20.00'
    assert row['claimed_amount'] == '120.00'
    assert row['pau_only_amount'] is None
    assert row['total_amount'] is None
    assert result['total_claimed_amount']['value'] == '120.00'
    # A separate fee to another creditor cannot be attached to this claim.
    other = text.replace('Взыскать с должника', 'Взыскать в пользу ПАО «Другой» с должника')
    assert extract_candidates(other).claims == []


def test_costs_explicitly_recovered_by_previous_judgment():
    text = (
        'ОПРЕДЕЛИЛ:\nВключить требования ПАО «Тест» в третью очередь в размере 125 руб., '
        'из которых: 100 руб. основной долг, 20 руб. неустойка, '
        '5 руб. судебные расходы по кредитному договору №1 от 01.01.2020, '
        'взысканные решением Примерного районного суда от 02.02.2021.'
    )
    row = fields(text)['claims']['value'][0]
    assert row['principal_debt'] == '105.00'
    assert row['financial_sanctions'] == '20.00'
    assert row['pau_only_amount'] == '0.00'
    assert row['total_amount'] == '125.00'
    # An instruction to recover costs now is not evidence of a prior cost.
    uncertain = text.replace('взысканные решением', 'подлежащие взысканию по определению')
    row = fields(uncertain)['claims']['value'][0]
    assert row['principal_debt'] is None


def test_creditor_legal_form_variants_and_own_inn():
    from ocr_platform.services.rtk3_requisites import source_inn
    for name, expected in [
        ('ООО МКК «Тест»', 'ООО МКК «Тест»'),
        ('ООО МФК «Тест»', 'ООО МФК «Тест»'),
        ('Банк «Пример» (АО)', 'АО «Банк Пример»'),
        ('«Пример» (Акционерное общество)', 'АО «Пример»'),
        ('ПАО Банк «ВТБ»', 'Банк ВТБ'),
        ('УФНС России по Кировской области', 'УФНС России по Кировской области'),
    ]:
        text = f'ОПРЕДЕЛИЛ:\nВключить требования {name} в третью очередь в размере 100 руб. основного долга.'
        assert extract_candidates(text).fields['creditor'].value == expected
    record = 'заявление Банка ВТБ (ПАО) (ИНН 7702070139, ОГРН 1027739609391)'
    assert source_inn(record, 'creditor_inn', 'Банк ВТБ').value == '7702070139'
    expanded = record.replace('(ПАО)', '(публичное акционерное общество)')
    assert source_inn(expanded, 'creditor_inn', 'Банк ВТБ').value == '7702070139'
    crossed = 'Банк ВТБ (ПАО) (ОГРН 1027739609391), должник (ИНН 123456789012)'
    assert source_inn(crossed, 'creditor_inn', 'Банк ВТБ').value is None


def test_partial_refusal_of_collateral_is_not_a_refusal_of_debt():
    text = (
        'ОПРЕДЕЛИЛ:\nВключить требования ПАО «Тест» в третью очередь в размере 100 руб. основного долга. '
        'В удовлетворении заявления ПАО «Тест» в части признания требования по кредитному договору '
        '№1 как обеспеченного залогом имущества отказать.'
    )
    result = fields(text)
    assert result['claims']['value'][0]['principal_debt'] == '100.00'
    assert result['pledge_resolution']['value'] == 'no'
    assert result['secured_by_pledge']['value'] is False


def test_ground_is_explicit_obligation_not_creditor_brand():
    for phrase, category in [
        ('по договору потребительского займа №12 от 01.01.2025', 'Договор займа'),
        ('проценты за пользование займом', 'Договор займа'),
        ('долг по транспортному налогу за 2025 год', 'Налоговая задолженность'),
        ('на предоставление возобновляемой кредитной линии', 'Кредитный договор'),
    ]:
        text = 'ОПРЕДЕЛИЛ:\nВключить требования ПАО «Тест» в третью очередь в размере 100 руб. ' + phrase
        assert extract_candidates(text).fields['grounds'].value == category
    text = 'ОПРЕДЕЛИЛ:\nВключить требования ООО МКК «Займер» в размере 100 руб. основного долга.'
    assert 'grounds' not in extract_candidates(text).fields
    text = ('ОПРЕДЕЛИЛ:\nВключить требование по договору займа '
            '(Индивидуальные условия договора потребительского кредита (займа)) №12.')
    assert extract_candidates(text).fields['grounds'].value == 'Договор займа'
    mixed = ('ОПРЕДЕЛИЛ:\nВключить требования по договору займа №12 '
             'и договору потребительского кредита №13.')
    assert 'grounds' not in extract_candidates(mixed).fields


def test_uncertain_cost_preserves_sanctions_and_reconciled_gross():
    text = (
        'ОПРЕДЕЛИЛ:\nВключить требования ПАО «Тест» в третью очередь в размере 125 руб., '
        'из которых: 100 руб. основной долг, 20 руб. неустойка, 5 руб. судебные расходы.'
    )
    row = fields(text)['claims']['value'][0]
    assert row['financial_sanctions'] == '20.00'
    assert row['principal_debt'] is None
    assert row['pau_only_amount'] is None
    assert row['claimed_amount'] is None
    assert row['total_amount'] == '125.00'
    mismatched, = fields(text.replace('125 руб.', '126 руб.'))["claims"]["value"]
    assert mismatched["total_amount"] is None
    for bad in [text.replace('125 руб.', '126 руб.'), text.replace('судебные расходы', 'задолженность')]:
        assert fields(bad)['claims']['value'][0]['financial_sanctions'] is None


def test_pledged_claim_survives_page_header_and_separate_fee():
    text = (
        'ОПРЕДЕЛИЛ:\nВключить требования ПАО «Тест» в третью очередь в размере 100 руб. основного долга, '
        'и установить её обеспеченной залогом квартиры. 2 А13-123/2025 '
        'Взыскать с должника в пользу ПАО «Тест» 5 руб. госпошлины по настоящему заявлению.'
    )
    result = fields(text)
    assert result['pledge_resolution']['value'] == 'yes'
    assert result['claims']['value'][0]['pau_only_amount'] == '5.00'
    assert result['claims']['value'][0]['total_amount'] == '105.00'


def test_repeated_inclusion_of_awarded_fee_is_not_second_claim():
    text = (
        'ОПРЕДЕЛИЛ:\nВключить требования ПАО «Тест» в третью очередь в размере 100 руб. основного долга, '
        'как обеспеченного залогом автомобиля Гос. номер ТС - А123БВ77 '
        'Взыскать с должника в пользу ПАО «Тест» 5 руб. госпошлины. '
        'Включить в порядке части 3 статьи 137 в реестр требований кредиторов должника '
        'требование ПАО «Тест» в размере 5 руб.'
    )
    result = fields(text)
    assert len(result['claims']['value']) == 1
    assert result['claims']['value'][0]['total_amount'] == '105.00'
    assert result['pledge_resolution']['value'] == 'yes'
    different = text.replace('требование ПАО «Тест» в размере 5 руб.', 'требование ПАО «Другой» в размере 5 руб.')
    assert extract_candidates(different).claims == []


def test_literal_contract_reference_does_not_guess_its_category():
    text = ('ОПРЕДЕЛИЛ:\nВключить требование ПАО «Тест» в третью очередь '
            'на основании договора №123 от 01.01.2025 в размере 100 руб. основного долга.')
    result = extract_candidates(text).fields
    assert result['grounds_ocr'].value == 'на основании договора №123 от 01.01.2025'
    assert 'grounds' not in result
    multiple = text.replace('в размере', 'и по договору №456 от 02.02.2025 в размере')
    assert 'grounds_ocr' not in extract_candidates(multiple).fields
    pledged = text.replace('на основании', 'обеспеченное залогом на основании')
    assert 'grounds_ocr' not in extract_candidates(pledged).fields


def test_tax_ground_uses_operative_obligation_not_authority_name():
    for phrase in ['по обязательным платежам', 'по налогам и пеням']:
        text = f'ОПРЕДЕЛИЛ:\nВключить требование ФНС России {phrase} в размере 100 руб. основного долга.'
        assert extract_candidates(text).fields['grounds'].value == 'Налоговая задолженность'
    text = 'ОПРЕДЕЛИЛ:\nВключить требование ФНС России в размере 100 руб. основного долга.'
    assert 'grounds' not in extract_candidates(text).fields


def test_tax_basis_keeps_the_period_printed_with_the_tax_name():
    text = (
        "ОПРЕДЕЛИЛ:\nТребования ФНС России в сумме 120 руб., из которых "
        "100 руб. – транспортный налог за 2023-2025 гг., 20 руб. – пени, "
        "признать установленными и подлежащими включению в третью очередь."
    )
    candidates = extract_candidates(text)
    assert candidates.fields["grounds_ocr"].value == "транспортный налог за 2023-2025 гг."
    assert candidates.fields["grounds"].value == "Налоговая задолженность"


def test_case_quote_normalizes_year_and_dispute_without_losing_identity():
    from ocr_platform.services.rtk3_regex_jev import _case_in_quote
    assert _case_in_quote('А53-49302/2025', 'А53-49302-2/25', main_case=True)
    assert _case_in_quote('А53-49302-2/2025', 'А53-49302-2/25')
    assert not _case_in_quote('А53-49302/2025', 'А53-49302-2/25')
    assert not _case_in_quote('А53-49302/2025', 'А53-49303-2/25', main_case=True)


def test_fee_with_words_between_digits_and_currency():
    text = ('ОПРЕДЕЛИЛ:\nВключить требования ПАО «Тест» в третью очередь в сумме 100 руб. основного долга. '
            'Признать требование ПАО «Тест» в размере 9 296 (девять тысяч двести девяносто шесть) рублей '
            'госпошлины по настоящему заявлению обоснованным по пункту 3 статьи 137.')
    row = fields(text)['claims']['value'][0]
    assert row['pau_only_amount'] == '9296.00'
    assert row['total_amount'] == '9396.00'
    unreadable = text.replace('9 296 (девять тысяч двести девяносто шесть) рублей', 'нечитаемой суммы')
    row = fields(unreadable)['claims']['value'][0]
    assert row['pau_only_amount'] is None
    assert row['principal_debt'] == '100.00'


def test_creditor_from_admitted_claim_not_fee_explanation():
    name = 'Межрайонной инспекции Федеральной налоговой службы №4 по Республике Башкортостан'
    text = (f'ОПРЕДЕЛИЛ:\nПризнать требование уполномоченного органа в лице {name} '
            'в размере 100 руб. основного долга обоснованным и включить в третью очередь. '
            'Взыскать с должника 5 руб. пошлины. Разъяснить, что требование МИФНС №32 по Челябинской области '
            'подлежит удовлетворению по пункту 3 статьи 137.')
    assert '№4' in extract_candidates(text).creditor_tax_subject
    ambiguous = text + ' Включить требования МИФНС №32 по Челябинской области в третью очередь в размере 10 руб.'
    assert 'creditor' not in extract_candidates(ambiguous).fields


def test_contract_list_keeps_every_reference():
    text = ('ОПРЕДЕЛИЛ:\nВключить требование по кредитным договорам №A-1 от 01.01.2025; '
            '№B-2 от 02.02.2025; №C-3 от 03.03.2025 в сумме 100 руб. основного долга.')
    value = extract_candidates(text).fields['grounds_ocr'].value
    assert all(ref in value for ref in ['A-1', 'B-2', 'C-3'])


def test_literal_basis_keeps_bare_identifiers_and_printed_dates():
    for basis in (
        'задолженность, основанную на договоре займа №2844 от 05.098.2024',
        'по кредитному договору ABC-123456 от 15.03.2018',
        'по кредитному договору № 123 от 23.10.2019г.',
        'по кредитному договору от 29.11.2022 (кредитная карта)',
        'по кредитному договору № 123 от О5.1О.2О24',
        'задолженность, основанную на кредитных договорах о предоставлении '
        'возобновляемой кредитной линии от 08.12.2023 и от 23.12.2021',
    ):
        text = f'ОПРЕДЕЛИЛ:\nВключить требования ПАО «Тест» {basis} в размере 100 руб.'
        fact = extract_candidates(text).fields['grounds_ocr']
        assert fact.value == fact.quote == basis
    text = 'ОПРЕДЕЛИЛ:\nВключить требования ПАО «Тест» по кредитному договору 100,00 руб.'
    assert extract_candidates(text).fields['grounds_ocr'].value == 'по кредитному договору'


def test_typo_in_literal_basis_does_not_set_a_category():
    basis = 'по кредтному дoговoру А-123 от 05.098.2024'
    text = f'ОПРЕДЕЛИЛ:\nВключить требования ПАО «Тест» {basis} в размере 100 руб.'
    result = extract_candidates(text).fields
    assert result['grounds_ocr'].value == basis
    assert 'grounds' not in result
    collateral = text.replace('по кредтному', 'обеспеченные залогом по кредтному')
    assert 'grounds_ocr' not in extract_candidates(collateral).fields


def test_itemized_contracts_share_a_single_literal_basis_with_both_references():
    basis = (
        'по кредитному договору A-1 от 01.01.2025 в размере 100 руб., '
        'из которых 100 руб. – основной долг; - по кредитному договору B-2 от 02.02.2025'
    )
    text = (
        'ОПРЕДЕЛИЛ:\nВключить требования ПАО «Тест» в третью очередь '
        'в размере 120 руб., в том числе: - '
        + basis + ' в размере 20 руб., из которых 20 руб. – основной долг.'
    )
    result = extract_candidates(text)
    assert [row.contract_number for row in result.claims] == ['A-1', 'B-2']
    assert result.fields['grounds_ocr'].value == basis


def test_credit_line_and_credit_contract_in_a_single_list():
    basis = (
        'по договору на предоставление возобновляемой кредитной линии путем подписания '
        'эмиссионного контракт от 25.03.2022 №A-1, кредитному договору от 03.02.2023 №B-2'
    )
    text = f'ОПРЕДЕЛИЛ:\nПризнать требование ПАО «Тест» {basis} обоснованным.'
    fact = extract_candidates(text).fields['grounds_ocr']
    assert fact.value == fact.quote == basis


def test_unquoted_bank_and_former_surname_requisites():
    from ocr_platform.services.rtk3_requisites import source_inn
    text = ('Рассмотрев заявление БАНК ГПБ (АО) (ИНН 7744001497) в рамках дела о банкротстве '
            'Тестовой (ранее фамилия Примерова) Анны Ивановны (ИНН 123456789012). '
            'ОПРЕДЕЛИЛ:\nВключить требования БАНК ГПБ (АО) в третью очередь в размере 100 руб. основного долга.')
    result = extract_candidates(text).fields
    assert result['creditor'].value == 'АО «Банк ГПБ»'
    assert result['creditor_inn'].value == '7744001497'
    assert result['debtor_inn'].value == '123456789012'
    address = 'ПАО «Тест», 117312, г. Москва, ул. Тестовая, д. 1, ОГРН 1234567890123, ИНН 7707083893'
    assert source_inn(address, 'creditor_inn', 'ПАО «Тест»').value == '7707083893'


def test_single_bare_amount_with_explicit_outside_satisfaction():
    text = ('ОПРЕДЕЛИЛ:\nПризнать обоснованными и подлежащими включению в третью очередь '
            'требования ООО «Тест» в размере 171 291,65 основной долг, отдельно, как подлежащие '
            'удовлетворению за счет имущества должника, оставшегося после удовлетворения требований '
            'кредиторов, включенных в реестр требований кредиторов должника.')
    row = fields(text)['claims']['value'][0]
    assert row['priority_queue'] == 'За реестром'
    assert row['principal_debt'] == '171291.65'
    assert row['financial_sanctions'] == '0.00'
    assert row['total_amount'] == '171291.65'


def test_duty_repayment_clause_after_comma_belongs_to_same_amount():
    text = ('ОПРЕДЕЛИЛ:\nВключить требования ПАО «Тест» в третью очередь: '
            '100 руб. - основной долг; 20 руб. - проценты; 3 руб. - неустойка; '
            '5 руб. - расходы на оплату государственной пошлины, в порядке положений пункта 3 статьи 137.')
    row = fields(text)['claims']['value'][0]
    assert row['principal_debt'] == '120.00'
    assert row['financial_sanctions'] == '3.00'
    assert row['pau_only_amount'] == '5.00'
    assert row['claimed_amount'] == '123.00'
    assert row['total_amount'] == '128.00'
    unknown = text.replace(', в порядке положений пункта 3 статьи 137', '')
    assert fields(unknown)['claims']['value'][0]['pau_only_amount'] is None


def test_named_embedded_fee_does_not_assign_its_queue_to_debt():
    text = ('ОПРЕДЕЛИЛ:\nВключить в реестр требования ПАО «Тест» в размере 100 руб., '
            'в том числе: 80 руб. основной долг, 20 руб. проценты, как обеспеченные залогом квартиры. '
            'Судебные расходы ПАО «Тест» по уплате государственной пошлины в размере 5 руб. '
            'подлежат учету в третьей очереди реестра по п. 3 ст. 137.')
    row = fields(text)['claims']['value'][0]
    assert row['principal_debt'] == '100.00'
    assert row['pau_only_amount'] == '5.00'
    assert row['priority_queue'] is None


def test_collateral_in_capacity_wording():
    from ocr_platform.services.rtk3_regex_candidates import pledge_candidate
    assert pledge_candidate('установить требование в качестве обеспеченного залогом автомобиля').value == 'yes'
    assert pledge_candidate('отказать в признании требования в качестве обеспеченного залогом автомобиля').value == 'no'


def test_separate_admission_after_approved_plan_does_not_include_plan_amounts():
    text = ('ОПРЕДЕЛИЛ:\nУтвердить локальный план реструктуризации в следующей редакции: '
            '1. Задолженность составляет 900 руб. 2. Должник уплачивает 50 руб. госпошлины. '
            '3. Требования обеспечены залогом квартиры. '
            'Производство по заявлению ПАО «Тест» о включении в реестр требований '
            'по кредитному договору №1 от 01.01.2025 - прекратить. '
            'Включить в третью очередь реестра денежное требование ПАО «Тест» в размере 100 руб. '
            'Признать судебные расходы ПАО «Тест» по уплате госпошлины в размере 5 руб. '
            'обоснованными по пункту 3 статьи 137. Возвратить ПАО «Тест» 2 руб. госпошлины.')
    result = fields(text)
    assert len(result['claims']['value']) == 1
    row = result['claims']['value'][0]
    assert row['priority_queue'] == '3 очередь'
    assert row['pau_only_amount'] == '5.00'
    assert row['principal_debt'] is None  # A total alone does not prove its composition.
    assert row['total_amount'] is None
    assert result['pledge_resolution']['value'] is None
    no_boundary = text.replace(' - прекратить.', ' продолжает действовать.')
    assert extract_candidates(no_boundary).claims == []


def test_refusal_of_secured_creditor_status_and_separate_fee_sentence():
    text = ('ОПРЕДЕЛИЛ:\nПризнать обоснованными и включить в третью очередь '
            'требования ПАО «Тест» в сумме 100 руб., из которой: '
            '80 руб. основной долг, 17 руб. проценты, 3 руб. неустойка. '
            'В удовлетворении требования о признании за обществом статуса залогового кредитора отказать. '
            'Государственная пошлина по настоящему требованию в размере 5 руб. '
            'учитывается в реестре применительно к пункту 3 статьи 137.')
    result = fields(text)
    row = result['claims']['value'][0]
    assert result['pledge_resolution']['value'] == 'no'
    assert result['secured_by_pledge']['value'] is False
    assert row['pau_only_amount'] == '5.00'
    assert row['total_amount'] == '105.00'


@pytest.mark.parametrize("printed,expected", [("100,00,руб.", "105.00"), ("100,01,руб.", None)])
def test_punctuated_court_total_is_reconciled_with_a_separate_fee(printed, expected):
    text = (
        "ОПРЕДЕЛИЛ:\nПризнать обоснованными и включить в третью очередь реестра требований кредиторов "
        f"требования ПАО «Тест» в общей сумме {printed}, из которой: "
        "80 руб. основной долг, 17 руб. проценты, 3 руб. неустойка. "
        "В удовлетворении требования о признании за обществом статуса залогового кредитора отказать. "
        "Государственная пошлина по настоящему требованию в размере 5 руб. "
        "учитывается в реестре применительно к пункту 3 статьи 137."
    )
    [claim] = extract_candidates(text).claims
    model_total = CourtTotal(amount=printed, quote=printed, scope="without_duty")
    reply = {_claim_key(0, name): value for name, value in _claim_values(claim).items()}
    reply["claim_1_included_total"] = model_total
    result = _fixed_claims([claim], {}, reply, text)
    [row] = result["claims"]["value"]
    assert row["principal_debt"] == "97.00"
    assert row["financial_sanctions"] == "3.00"
    assert row["pau_only_amount"] == "5.00"
    assert row["total_amount"] == expected
    assert row["claimed_amount"] == ("100.00" if expected is not None else None)
    assert claim.total == model_total
    assert ("Денежные компоненты не совпадают" in result["claims"].get("validation_issue", "")) is (expected is None)


def test_exclusion_stops_before_independent_fee_but_still_rejects_refund():
    from ocr_platform.services.rtk3_evidence import excluded_component
    rejected = 'В удовлетворении требования о взыскании 10 руб. отказать.'
    awarded = 'Государственная пошлина 5 руб. учитывается по пункту 3 статьи 137.'
    refunded = 'Государственная пошлина 2 руб. подлежит возврату заявителю.'
    text = rejected + ' ' + awarded + ' ' + refunded
    assert excluded_component('10 руб.', text)
    assert not excluded_component(awarded, text)
    assert excluded_component(refunded, text)

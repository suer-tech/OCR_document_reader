"""Offline regressions for independently stated RTK3 sums and fees."""
from ocr_platform.services.rtk3_regex_candidates import (
    claim_amounts, extract_candidates, nested_contract_amounts,
)
from ocr_platform.services.rtk3_regex_jev import _claim_key, _claim_values, _fixed_claims
from ocr_platform.services.rtk3_requisites import source_inn
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
        ("по договору займа от 29.04.2025", None),
    ):
        row, = fields(text.format(contract=contract))["claims"]["value"]
        assert row["contract_number"] == number


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


def test_tax_creditors_use_the_common_public_name():
    federal = "ОПРЕДЕЛИЛ:\nВключить требования ФНС России в третью очередь в размере 100 руб. основного долга."
    inspection = federal.replace("ФНС России", "Межрайонной инспекции Федеральной налоговой службы №7 по Омской области")
    assert extract_candidates(federal).fields["creditor"].value == "ФНС России"
    candidates = extract_candidates(inspection)
    assert candidates.fields["creditor"].value == "ФНС России"
    assert "№7" in candidates.creditor_tax_subject


def test_tax_creditor_picks_the_named_inspection_inn_from_the_application():
    text = (
        "Рассмотрев заявление ФНС России (ИНН 0123456789) в лице МИФНС России №7 по Омской области "
        "(ИНН 1111111199) о включении в реестр требований.\n"
        "ОПРЕДЕЛИЛ:\nВключить требования ФНС России (ИНН 0123456789) в третью очередь."
    )
    candidates = extract_candidates(text)
    assert candidates.fields["creditor"].value == "ФНС России"
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
    assert candidates.fields["creditor"].value == "ФНС России"
    assert candidates.fields["creditor_inn"].value == "1111111199"


def test_tax_inn_fallback_checks_the_office_despite_the_common_public_name():
    from ocr_platform.services.rtk3_evidence import Fact
    from ocr_platform.services.rtk3_regex_jev import _model_fact

    federal = "ФНС России (ИНН 0123456789)"
    office = "МИФНС России №7 по Омской области (ИНН 1111111199)"
    other_office = "МИФНС России №8 по Омской области (ИНН 2222222299)"
    text = f"{federal} в лице {office}. {other_office}"
    subject = "МИФНС России №7 по Омской области"
    assert _model_fact("creditor_inn", Fact(value="1111111199", quote=office),
                       text, "ФНС России", subject) == "1111111199"
    assert _model_fact("creditor_inn", Fact(value="0123456789", quote=federal),
                       text, "ФНС России", subject) is None
    assert _model_fact("creditor_inn", Fact(value="2222222299", quote=other_office),
                       text, "ФНС России", subject) is None


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
        ]

    monkeypatch.setattr(service, "_tax_office_registrations", {})
    monkeypatch.setattr(service, "dadata_party_suggestions", suggestions)
    assert service.registered_tax_inn("МИФНС России №7 по Омской области", "18.11.2026") == "2222222299"
    assert service.registered_tax_inn(office, "01.01.2018") == "1111111199"
    assert service.registered_tax_inn("ФНС России №7 по Омской области", "18.11.2026") == "2222222299"
    assert service.registered_tax_inn("ФНС России", "18.11.2026") is None
    assert service.registered_tax_inn("МИФНС России №7", "18.11.2026") is None
    assert queries == ["Межрайонная ИФНС России № 7 по омской области", "ИФНС № 7 по омской области"]


def test_tax_registry_enrichment_yields_to_document_inns_and_marks_the_route(monkeypatch):
    import asyncio
    from ocr_platform.services import rtk3_regex_jev as service

    office = "МИФНС России №7 по Омской области"
    calls = []

    def lookup(subject, act_date):
        calls.append((subject, act_date))
        return "1111111199"

    monkeypatch.setattr(service, "registered_tax_inn", lookup)
    result = {"creditor": {"value": "ФНС России"}, "creditor_inn": {"value": None},
              "inclusion_date": {"value": "18.11.2026"}}
    printed = f"{office} (ИНН 2222222299)"
    asyncio.run(service._enrich_tax_inn(result, printed, office))
    asyncio.run(service._enrich_tax_inn(result, f"{printed}. {office} (ИНН 3333333399)", office))
    assert not calls
    asyncio.run(service._enrich_tax_inn(result, office, office))
    assert calls == [(office, "18.11.2026")]
    assert result["creditor_inn"]["value"] == "1111111199"
    assert result["creditor_inn"]["extraction_route"] == "dadata_party_registry"


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
        ('УФНС России по Кировской области', 'ФНС России'),
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


def test_exclusion_stops_before_independent_fee_but_still_rejects_refund():
    from ocr_platform.services.rtk3_evidence import excluded_component
    rejected = 'В удовлетворении требования о взыскании 10 руб. отказать.'
    awarded = 'Государственная пошлина 5 руб. учитывается по пункту 3 статьи 137.'
    refunded = 'Государственная пошлина 2 руб. подлежит возврату заявителю.'
    text = rejected + ' ' + awarded + ' ' + refunded
    assert excluded_component('10 руб.', text)
    assert not excluded_component(awarded, text)
    assert excluded_component(refunded, text)

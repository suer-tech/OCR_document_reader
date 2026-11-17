"""Offline regressions for independently stated RTK3 sums and fees."""
from ocr_platform.services.rtk3_regex_candidates import (
    claim_amounts, extract_candidates, nested_contract_amounts,
)
from ocr_platform.services.rtk3_regex_jev import _claim_key, _claim_values, _fixed_claims


def fields(text):
    candidates = extract_candidates(text)
    patch = {_claim_key(i, name): value for i, claim in enumerate(candidates.claims)
             for name, value in _claim_values(claim).items()}
    return _fixed_claims(candidates.claims, {}, patch, text)


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


def test_creditor_federal_and_inspection_names_stay_distinct():
    federal = "ОПРЕДЕЛИЛ:\nВключить требования ФНС России в третью очередь в размере 100 руб. основного долга."
    inspection = federal.replace("ФНС России", "Межрайонной инспекции Федеральной налоговой службы №7 по Омской области")
    assert extract_candidates(federal).fields["creditor"].value == "ФНС России"
    assert "№7" in extract_candidates(inspection).fields["creditor"].value


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


def test_uncertain_cost_does_not_erase_independent_sanctions():
    text = (
        'ОПРЕДЕЛИЛ:\nВключить требования ПАО «Тест» в третью очередь в размере 125 руб., '
        'из которых: 100 руб. основной долг, 20 руб. неустойка, 5 руб. судебные расходы.'
    )
    row = fields(text)['claims']['value'][0]
    assert row['financial_sanctions'] == '20.00'
    assert row['principal_debt'] is None
    assert row['pau_only_amount'] is None
    assert row['claimed_amount'] is None
    assert row['total_amount'] is None
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

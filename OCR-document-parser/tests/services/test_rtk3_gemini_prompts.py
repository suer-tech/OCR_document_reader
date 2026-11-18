"""Prompt/schema and deterministic ledger regressions. No real model calls."""
import asyncio
import copy
from types import SimpleNamespace

import pytest
from pydantic import ConfigDict, ValidationError, create_model

from ocr_platform.orchestration.router import load_profile, resolve_profile
from ocr_platform.services import rtk3_regex_jev as service
from ocr_platform.services.rtk3_evidence import ClaimEvidence, DocumentEvidence, build_fields
from ocr_platform.services.rtk3_gemini_prompts import CLAIM_RULES, SCALAR_RULES, build_gemini_prompt
from ocr_platform.services.rtk3_regex_candidates import Claim


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Network/model calls are forbidden in these tests")
    # Windows asyncio uses a local socket pair; block HTTP transport instead.
    monkeypatch.setattr("httpx.Client.send", forbidden)
    monkeypatch.setattr("httpx.AsyncClient.send", forbidden)
    monkeypatch.setattr(service, "call_llm_json_with_fallback", forbidden)


def schema(layout):
    return create_model("Rtk3Test", __config__=ConfigDict(extra="forbid"),
                        **service._fallback_schema(layout, {}, set(SCALAR_RULES)))


def test_full_and_fixed_prompts_follow_their_actual_schemas():
    full = schema([])
    prompt = build_gemini_prompt(set(full.model_fields), full_claims=True)
    assert all(f"Поле {name}:" in prompt for name in SCALAR_RULES)
    assert all(f"Поле claims[].{name}:" in prompt for name in CLAIM_RULES)
    assert 'components_complete' in prompt
    assert 'claim_N_' not in prompt and 'items:' not in prompt
    fixed = schema([Claim("Подтверждённая строка", {}, None)])
    prompt = build_gemini_prompt(set(fixed.model_fields), full_claims=False)
    assert 'Поле claim_1_pau_only_amount:' in prompt
    assert 'items:' in prompt and 'components_complete' not in prompt
    # Public calculated fields and external KAD date are absent from both requests.
    for model in (full, fixed):
        assert not {'total_claimed_amount', 'secured_by_pledge', 'kad_publication_date'} & set(model.model_fields)


def test_only_requested_field_instructions_are_sent():
    prompt = build_gemini_prompt({'creditor_inn'}, full_claims=False)
    assert 'Поле creditor_inn:' in prompt
    assert 'ФНС' in prompt and 'инспекции' in prompt
    assert 'поиск по установленному наименованию выполняется после извлечения' in prompt
    assert 'Поле grounds:' not in prompt and 'Формат полного' not in prompt
    assert 'Формат отдельных' not in prompt


def test_full_prompt_and_schema_require_one_contract_per_breakdown():
    model = schema([])
    description = ClaimEvidence.model_json_schema()['properties']['breakdown_quote']['description']
    assert 'required exact quote of this single contract' in description
    prompt = build_gemini_prompt(set(model.model_fields), full_claims=True)
    assert 'breakdown_quote в каждой такой строке обязательна' in prompt
    assert 'все денежные цитаты строки должны находиться внутри неё' in prompt
    assert 'даже если grounds_ocr равно null' in prompt
    assert 'Неясные отдельные расходы не меняют' in prompt


@pytest.mark.parametrize('full_claims', [True, False])
def test_queue_and_amount_rules_match_both_fallback_formats(full_claims):
    layout = [] if full_claims else [Claim("Подтверждённая строка", {}, None)]
    prompt = build_gemini_prompt(set(schema(layout).model_fields), full_claims=full_claims)
    assert 'неизвестную очередь передавай как null' in prompt
    assert 'компонентом той же строки третьей очереди' in prompt
    assert 'проценты по просроченному основному долгу' in prompt
    assert 'долг, — prior_cost' in prompt
    assert 'строка из одних пеней или штрафов имеет нулевой основной долг' in prompt
    if full_claims:
        assert 'только компоненты kind=sanction\nи components_complete=true' in prompt
    else:
        assert 'items=[], complete=true и цитату о пенях' in prompt


def test_nullable_salary_and_allowed_queue_and_pledge_values():
    assert 'null' in str(ClaimEvidence.model_json_schema()['properties']['is_salary_or_severance'])
    fixed = schema([Claim("Строка", {}, None)])
    fields = fixed.model_fields
    from pydantic import TypeAdapter
    assert TypeAdapter(fields['claim_1_is_salary_or_severance'].annotation).validate_python(
        {'value': None, 'quote': ''}).value is None
    queue = TypeAdapter(fields['claim_1_priority_queue'].annotation)
    assert queue.validate_python({'value': 'Иное', 'quote': 'цитата'}).value == 'Иное'
    assert queue.validate_python({'value': None, 'quote': ''}).value is None
    for key, bad in [('claim_1_priority_queue', 'Иное либо неизвестно'), ('claim_1_pledge_resolution', 'maybe')]:
        with pytest.raises(ValidationError):
            TypeAdapter(fields[key].annotation).validate_python({'value': bad, 'quote': 'цитата'})


def ledger(statuses):
    claims = []
    for i, status in enumerate(statuses, 1):
        quote = f'Включить в третью очередь требование {i}: {i * 100} руб. налогового долга.'
        claims.append(dict(
            priority_queue={'value': '3 очередь', 'quote': 'третью очередь'}, decision_quote=quote,
            breakdown_quote='', components=[{'kind': 'principal', 'amount': f'{i * 100}', 'quote': quote}],
            components_complete=True, included_total={'value': str(i * 100), 'quote': quote},
            total_scope='without_duty', pledge_resolution='unknown', pledge_quote='',
            is_salary_or_severance=status, salary_quote=quote if status is not None else '', uncertainty='',
        ))
    empty = {'value': None, 'quote': ''}
    evidence = DocumentEvidence(**{name: copy.deepcopy(empty) for name in (
        'document_case_number', 'debtor_inn', 'creditor', 'creditor_inn', 'inclusion_date',
        'kad_publication_date', 'grounds')}, claims=claims, claims_complete=True, uncertainty='')
    text = '\n'.join(c['decision_quote'] for c in claims)
    return evidence, text


def test_sanctions_only_queue_has_zero_principal_in_both_fallback_formats():
    evidence, _ = ledger([False, False])
    first, second = evidence.claims
    first.decision_quote = first.decision_quote.replace('третью очередь', 'вторую очередь')
    first.priority_queue.value = '2 очередь'
    first.priority_queue.quote = 'вторую очередь'
    first.components[0].quote = first.included_total.quote = first.salary_quote = first.decision_quote
    second.decision_quote = 'Включить в третью очередь 20 руб. пеней по налоговой задолженности.'
    second.components[0].kind = 'sanction'
    second.components[0].amount = second.included_total.value = '20 руб.'
    second.components[0].quote = '20 руб. пеней'
    second.included_total.quote = second.salary_quote = second.decision_quote
    text = first.decision_quote + '\n' + second.decision_quote
    profile = load_profile('rtk3')
    full_rows = build_fields(evidence, text, profile, recover_from_source=False)['claims']['value']

    layout = [Claim(claim.decision_quote, {}, None) for claim in evidence.claims]
    raw_fields = {}
    for index, claim in enumerate(evidence.claims, start=1):
        for name, kind in (('principal_debt', 'principal'), ('financial_sanctions', 'sanction'),
                           ('pau_only_amount', 'duty')):
            raw_fields[f'claim_{index}_{name}'] = {
                'items': [item.model_dump() for item in claim.components if item.kind == kind],
                'complete': True, 'quote': claim.decision_quote,
            }
        raw_fields[f'claim_{index}_priority_queue'] = claim.priority_queue.model_dump()
        raw_fields[f'claim_{index}_included_total'] = {
            'amount': claim.included_total.value, 'quote': claim.included_total.quote, 'scope': claim.total_scope,
        }
        raw_fields[f'claim_{index}_pledge_resolution'] = None
        raw_fields[f'claim_{index}_is_salary_or_severance'] = {'value': False, 'quote': claim.salary_quote}
    types = service._fallback_schema(layout, {}, set())
    fixed = create_model('FixedClaims', **types).model_validate(raw_fields)
    fixed_rows = service._fixed_claims(layout, {}, dict(fixed), text)['claims']['value']

    expected = [('2 очередь', '100.00', '0.00'), ('3 очередь', '0.00', '20.00')]
    for rows in (full_rows, fixed_rows):
        assert [(row['priority_queue'], row['principal_debt'], row['financial_sanctions'])
                for row in rows] == expected


def test_two_contracts_in_one_order_keep_amounts_and_collateral_independent():
    evidence, _ = ledger([False, False])
    heading = 'Включить в третью очередь требования ПАО «Пример»: '
    first = 'в рамках кредитного договора № A-1 в размере 100 руб., из них: 100 руб. основного долга, обеспеченного залогом;'
    second = 'в рамках кредитного договора № A-2 в размере 20 руб., из них: 20 руб. основного долга.'
    text = heading + first + ' ' + second + ' Признать судебные расходы в размере 7 руб. обоснованными.'
    evidence.uncertainty = 'Суд отдельно указал 7 руб. судебных расходов без уточнения их вида.'
    for i, (clause, amount) in enumerate(((first, '100 руб.'), (second, '20 руб.'))):
        claim = evidence.claims[i]
        claim.decision_quote = heading + first + (' ' + second if i else '')
        claim.breakdown_quote = clause
        claim.components[0].amount = amount
        claim.components[0].quote = f'{amount} основного долга'
        claim.included_total.value = amount
        claim.included_total.quote = f'в размере {amount}'
        claim.salary_quote = clause
    evidence.claims[0].pledge_resolution = 'yes'
    evidence.claims[0].pledge_quote = 'обеспеченного залогом'
    result = build_fields(evidence, text, load_profile('rtk3'), recover_from_source=False)
    assert [(row['contract_number'], row['pledge_resolution'], row['claimed_amount'])
            for row in result['claims']['value']] == [('A-1', 'yes', '100.00'), ('A-2', None, '20.00')]
    assert result['total_claimed_amount']['value'] == '120.00'
    assert result['pledge_resolution']['value'] is None

    # The second row can't be trusted with a breakdown containing two contracts.
    evidence.claims[1].breakdown_quote = first + ' ' + second
    partial = build_fields(evidence, text, load_profile('rtk3'), recover_from_source=False)
    assert len(partial['claims']['value']) == 1
    assert partial['claims']['value'][0]['pledge_resolution'] == 'yes'
    assert partial['pledge_resolution']['value'] is None
    assert partial['secured_by_pledge']['value'] is None
    assert partial['total_claimed_amount']['value'] is None


def test_explicitly_incomplete_model_list_cannot_set_overall_pledge():
    evidence, _ = ledger([False])
    evidence.claims_complete = False
    claim = evidence.claims[0]
    claim.decision_quote += ' Требование обеспечено залогом автомобиля.'
    claim.pledge_resolution = 'yes'
    claim.pledge_quote = 'Требование обеспечено залогом автомобиля.'
    result = build_fields(evidence, claim.decision_quote, load_profile('rtk3'), recover_from_source=False)
    assert result['claims']['value'][0]['pledge_resolution'] == 'yes'
    assert result['pledge_resolution']['value'] is None
    assert result['secured_by_pledge']['value'] is None


@pytest.mark.parametrize('statuses', [[None], [False], [None, False], [None, None]])
def test_salary_status_and_money_are_preserved_per_claim(statuses):
    evidence, text = ledger(statuses)
    profile = load_profile('rtk3')
    result = build_fields(evidence, text, profile, recover_from_source=False)
    rows = result['claims']['value']
    assert [row['is_salary_or_severance'] for row in rows] == statuses
    assert [row['principal_debt'] for row in rows] == [f'{100 * i}.00' for i in range(1, len(statuses) + 1)]
    assert result['total_claimed_amount']['value'] == f'{sum(100 * i for i in range(1, len(statuses) + 1))}.00'


def test_unsupported_salary_true_is_unknown_not_false_and_does_not_erase_money():
    evidence, text = ledger([True])  # Quote is tax debt, not a salary claim.
    result = build_fields(evidence, text, load_profile('rtk3'), recover_from_source=False)
    row = result['claims']['value'][0]
    assert row['is_salary_or_severance'] is None
    assert row['principal_debt'] == '100.00'


def test_gemini_gateway_receives_selected_prompt_and_schema(monkeypatch):
    captured = {}
    def fake_gateway(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(parsed={'creditor_inn': {'value': None, 'quote': ''}})
    monkeypatch.setattr(service, 'call_llm_json_with_fallback', fake_gateway)
    config = {'provider': 'router_ai', 'model': 'google/gemini-4-argon'}
    types = service._fallback_schema([], {}, {'creditor_inn'})
    types = {'creditor_inn': types['creditor_inn']}
    result = asyncio.run(service._gemini('Синтетический документ', config, {}, types, [], {}))
    assert result['creditor_inn'].value is None
    assert captured['models'] == ['google/gemini-4-argon']
    assert captured['system_prompt'] == build_gemini_prompt({'creditor_inn'}, full_claims=False)
    assert set(captured['response_schema']['json_schema']['schema']['properties']) == {'creditor_inn'}


def test_profile_has_one_prompt_source_and_no_legacy_amount_instruction():
    resolution = resolve_profile(source_type='external', requested_document_type='rtk3')
    assert resolution.profile_id == 'rtk3'
    profile = load_profile(resolution.profile_id)
    assert profile['fields'] and profile['pipeline']
    assert profile['models']['llm_extraction']['model'] == 'google/gemini-4-argon'
    assert profile['extraction_mode'] == 'regex_jev'
    for config in profile['fields'].values():
        assert 'prompt_instruction' not in config
        assert 'claimed_amount_instruction' not in config
    assert profile['fields']['kad_publication_date']['extraction_method'] == 'fixed'
    assert profile['fields']['total_claimed_amount']['extraction_method'] == 'computed'

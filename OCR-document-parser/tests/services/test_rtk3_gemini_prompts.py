"""Prompt/schema and deterministic ledger regressions. No real model calls."""
import asyncio
import copy
from types import SimpleNamespace

import pytest
from pydantic import ConfigDict, ValidationError, create_model

from ocr_platform.orchestration.router import load_profile
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
    assert 'Поле grounds:' not in prompt and 'Формат полного' not in prompt
    assert 'Формат отдельных' not in prompt


def test_nullable_salary_and_allowed_queue_and_pledge_values():
    assert 'null' in str(ClaimEvidence.model_json_schema()['properties']['is_salary_or_severance'])
    fixed = schema([Claim("Строка", {}, None)])
    fields = fixed.model_fields
    from pydantic import TypeAdapter
    assert TypeAdapter(fields['claim_1_is_salary_or_severance'].annotation).validate_python(
        {'value': None, 'quote': ''}).value is None
    for key, bad in [('claim_1_priority_queue', '5 очередь'), ('claim_1_pledge_resolution', 'maybe')]:
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
    profile = load_profile('rtk3')
    assert profile['models']['llm_extraction']['model'] == 'google/gemini-4-argon'
    assert profile['extraction_mode'] == 'regex_jev'
    for config in profile['fields'].values():
        assert 'prompt_instruction' not in config
        assert 'claimed_amount_instruction' not in config
    assert profile['fields']['kad_publication_date']['extraction_method'] == 'fixed'
    assert profile['fields']['total_claimed_amount']['extraction_method'] == 'computed'

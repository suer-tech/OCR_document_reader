"""Regressions from the October 22 source review; all providers are mocked."""
import asyncio
from types import SimpleNamespace

import pytest

from ocr_platform.services import court_regex_jev as routing, court_selective_llm as selective
from ocr_platform.services.court_regex_candidates import extract_court_candidates
from ocr_platform.services.court_regex_debtor_inn import extract_debtor_inn_candidates
from ocr_platform.services.court_regex_early_report_required import extract_early_report_required
from ocr_platform.services.court_regex_report_deadline import extract_report_deadline_candidates
from ocr_platform.services.llm_gateway import _chat_response_text, LlmResponseFailure


def test_creditor_legal_entity_inn_does_not_compete_with_individual_debtor():
    text = ('Признать Иванова Ивана Ивановича (ИНН 123456789012) банкротом. '
            'Должник имеет задолженность, в том числе перед ООО «Кредитор» ИНН 7707782563, '
            'ООО «Другой кредитор» ИНН 7714704125.')
    result = extract_debtor_inn_candidates(text)
    assert result.problem is None
    assert {c.value for c in result.candidates} == {'123456789012'}


@pytest.mark.parametrize('term', ['сроком шесть месяцев', 'продолжительностью 6 месяцев'])
def test_current_explicit_duration_without_na_calculates_end_and_boolean(term):
    text = f'РЕШЕНИЕ\n20 октября 2026 года\nРЕШИЛ:\nВвести процедуру реализации имущества {term}.'
    fields = extract_court_candidates(text, ['procedure_end_date', 'procedure_end_date_is_calculated']).fields
    assert fields['procedure_end_date'].value == '20.04.2027'
    assert fields['procedure_end_date_is_calculated'].value is True


def test_financial_analysis_before_end_does_not_date_report_after_settlements():
    text = ('РЕШИЛ:\nОбязать финансового управляющего:\n'
            '– не позднее чем за пять дней до истечения срока представить в арбитражный суд:\n'
            'анализ финансового состояния должника; сведения о ходе мероприятий.\n'
            '- после завершения расчетов с кредиторами представить в арбитражный суд:\n'
            'отчет о результатах реализации имущества с приложением документов.')
    assert extract_early_report_required(text).value is False
    assert extract_report_deadline_candidates(text, '20.04.2027').selected is None


def test_provide_report_is_same_order_as_submit_report():
    text = 'РЕШИЛ:\nОбязать финансового управляющего заблаговременно предоставить арбитражному суду отчет.'
    assert extract_early_report_required(text).value is True
    assert extract_report_deadline_candidates(text, '24.10.2025').selected.value == '14.10.2025'


def test_new_assignment_after_comma_does_not_supply_previous_missing_report():
    text = ('РЕШИЛ:\nФинансовому управляющему заблаговременно представить в суд, '
            'обязать финансового управляющего после расчетов представить отчет.')
    assert extract_report_deadline_candidates(text, '24.10.2025').selected is None


def test_reference_to_unambiguous_current_date_is_literal_due_date():
    text = ('РЕШИЛ:\nВвести процедуру реализации имущества до 13.04.2027. '
            'Финансовому управляющему к указанной дате представить в суд отчет.')
    assert extract_early_report_required(text).value is True
    result = extract_report_deadline_candidates(text, '13.04.2027')
    assert result.selected.value == '13.04.2027'
    assert result.selected.basis == 'explicit_report_date'
    assert text[result.selected.start:result.selected.end] == result.selected.quote


def test_unresolved_reference_is_not_arbitrary_next_date():
    text = 'РЕШИЛ:\nФинансовому управляющему к указанной дате представить в суд отчет.'
    result = extract_report_deadline_candidates(text, '13.04.2027')
    assert result.selected is None
    assert result.problem == 'unresolved_report_date_reference'


def test_future_conditional_hearing_does_not_create_current_report_order():
    text = ('РЕШИЛ:\nВ случае назначения заседания по инициативе суда финансовый управляющий '
            'обязан за пять дней до заседания представить в суд отчет.')
    assert extract_early_report_required(text).value is False
    assert extract_report_deadline_candidates(text, '13.04.2027').selected is None


@pytest.mark.parametrize('flag', [None, False])
def test_missing_or_contradictory_calculated_flag_cannot_pass_silently(flag):
    text = 'РЕШЕНИЕ\n20 октября 2026 года\nРЕШИЛ:\nВвести процедуру реализации имущества сроком шесть месяцев.'
    output = {'procedure_end_date': {'value': '20.04.2027'},
              'procedure_end_date_is_calculated': {'value': flag}}
    routing._validate_relationships(output, text)
    assert output['procedure_end_date']['value'] == '20.04.2027'
    assert output['procedure_end_date_is_calculated']['validation_issue']


@pytest.mark.parametrize('reply,code', [
    ({'choices': []}, 'empty_choices'),
    ({'choices': [{'message': {'content': None}, 'finish_reason': 'stop'}]}, 'empty_content'),
    ({'choices': [{'message': {'content': None, 'refusal': 'blocked'}, 'finish_reason': 'content_filter'}]}, 'provider_refusal'),
    ({'choices': [{'message': {'content': '{'}, 'finish_reason': 'length'}]}, 'truncated_response'),
    ({'error': {'code': 'error'}}, 'provider_error'),
])
def test_provider_failure_is_not_json_or_absence(reply, code):
    with pytest.raises(LlmResponseFailure) as failure:
        _chat_response_text(reply)
    assert failure.value.code == code
    assert failure.value.terminal is (code == 'provider_refusal')


def test_empty_unexplained_null_retries_only_invalid_field(monkeypatch):
    calls = []
    def field(value, confidence=.9, reason='Данные проверены в тексте'):
        return dict(value=value, confidence=confidence, reasoning=reason, evidence='source', ambiguous=False)
    def provider(**kwargs):
        calls.append(kwargs)
        parsed = ({'debtor_inn': field(None, 0, ''), 'case_number': field('А01-1/2026')}
                  if len(calls) == 1 else {'debtor_inn': field('123456789012')})
        return SimpleNamespace(parsed=parsed, winner_model='google/gemini-4-argon')
    monkeypatch.setattr(selective, 'call_llm_json_with_fallback', provider)
    monkeypatch.setattr(selective, 'get_field_instruction', lambda *a, **kw: kw.get('default', ''))
    config = {'models': {'llm_extraction': {'model': 'google/gemini-4-argon'}}}
    result = asyncio.run(selective.extract_court_llm_fields('source', {'debtor_inn': {}, 'case_number': {}}, config))
    assert len(calls) == 2
    assert set(calls[1]['response_schema']['json_schema']['schema']['properties']) == {'debtor_inn'}
    assert result['debtor_inn']['value'] == '123456789012'
    assert all(c['models'] == ['google/gemini-4-argon'] for c in calls)


def test_refusal_requires_review_without_three_identical_retries(monkeypatch):
    calls = []
    def provider(**kwargs):
        calls.append(kwargs)
        kwargs['failure_details'].update(code='provider_refusal', terminal=True, model='configured')
        return None
    monkeypatch.setattr(selective, 'call_llm_json_with_fallback', provider)
    monkeypatch.setattr(selective, 'get_field_instruction', lambda *a, **kw: '')
    result = asyncio.run(selective.extract_court_llm_fields('source', {'case_number': {}},
        {'models': {'llm_extraction': {'model': 'configured', 'fallback_models': ['forbidden']}}}))
    assert len(calls) == 1
    assert result['case_number']['value'] is None
    assert result['case_number']['provider_failure']['code'] == 'provider_refusal'
    assert 'provider_refusal' in result['case_number']['validation_issue']


def test_gateway_propagates_refusal_details_without_switching_model(monkeypatch):
    from ocr_platform.services import llm_gateway as gateway

    calls = []
    class Client:
        def __init__(self, **kwargs):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def post(self, url, **kwargs):
            calls.append(kwargs['json']['model'])
            return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {'choices': [
                {'finish_reason': 'content_filter', 'message': {'content': None, 'refusal': 'blocked'}}]})
    monkeypatch.setattr(gateway.httpx, 'Client', Client)
    monkeypatch.setattr(gateway, '_resolve_provider_credentials', lambda *a: ('test', 'https://test.invalid'))
    monkeypatch.setattr(gateway, '_log_llm_call_to_mlflow', lambda **kw: None)
    details = {}
    result = gateway.call_llm_json_with_fallback(task_name='court_selective_llm', provider='router_ai',
        request_type='field_extraction', timeout_seconds=10, models=['configured', 'must-not-run'],
        system_prompt='', user_content='source', response_schema={}, failure_details=details)
    assert result is None
    assert calls == ['configured']
    assert details == {'code': 'provider_refusal', 'terminal': True, 'model': 'configured'}


def test_confirmed_absent_flag_cannot_survive_unconfirmed_end(monkeypatch):
    from ocr_platform.services.court_regex_candidates import CourtCandidates

    calls = []
    monkeypatch.setattr(routing, 'extract_court_candidates', lambda *a: CourtCandidates({},
        {'procedure_end_date': 'not_found', 'procedure_end_date_is_calculated': 'not_found'}))
    def verify(**kwargs):
        calls.append('jev')
        return SimpleNamespace(parsed={'answers': {'procedure_end_date_is_calculated': {'type': 'noul', 'noul': .99}}},
                               winner_model='typesafe/jev-1.14')
    async def extract(text, fields, config, **kwargs):
        calls.append('llm')
        assert set(fields) == {'procedure_end_date', 'procedure_end_date_is_calculated'}
        return {'procedure_end_date': {'value': '20.04.2027', 'source': 'court_llm_fallback'},
                'procedure_end_date_is_calculated': {'value': True, 'source': 'court_llm_fallback'}}
    monkeypatch.setattr(routing, 'call_llm_json_with_fallback', verify)
    monkeypatch.setattr(routing, 'extract_court_llm_fields', extract)
    text = 'РЕШЕНИЕ\n20 октября 2026 года\nРЕШИЛ:\nВвести процедуру реализации имущества сроком шесть месяцев.'
    output = asyncio.run(routing.extract_court_regex_jev(text, {
        'fields': {'procedure_end_date': {'required': True}, 'procedure_end_date_is_calculated': {}},
        'models': {'verification': {'threshold': .6}}}, document_id='test', pipeline_run_id='test'))
    assert output['procedure_end_date_is_calculated']['value'] is True
    assert calls == ['jev', 'llm']

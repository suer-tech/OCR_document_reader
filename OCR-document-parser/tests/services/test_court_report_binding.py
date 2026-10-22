"""Report subject, source date basis and pair consistency regressions."""
import asyncio
from types import SimpleNamespace

import pytest

from ocr_platform.services import court_regex_jev as routing, court_selective_llm as selective
from ocr_platform.services.court_regex_candidates import CourtCandidate, CourtCandidates
from ocr_platform.services.court_regex_early_report_required import extract_early_report_required
from ocr_platform.services.court_regex_report_deadline import extract_report_deadline_candidates
from ocr_platform.services.court_report_validation import validate_report_deadline


@pytest.mark.parametrize('separator', ['; ', ';\n', '\n- '])
def test_report_after_settlements_does_not_inherit_financial_analysis_due_date(separator):
    text = ('РЕШИЛ: Обязать финансового управляющего: не позднее чем за пять дней до истечения срока '
            'представить в суд анализ финансового состояния; анализ сделок; сведения о ходе мероприятий'
            + separator + 'после завершения расчетов с кредиторами представить в арбитражный суд '
            'отчет о результатах реализации имущества.')
    assert extract_early_report_required(text).value is False
    assert extract_report_deadline_candidates(text, '08.04.2027').selected is None


@pytest.mark.parametrize('objects', ['отчет; анализ сделок', 'анализ сделок; отчет', 'анализ сделок и отчет'])
def test_object_enumeration_can_share_the_same_deadline(objects):
    text = f'РЕШИЛ: Финансовому управляющему до 15.04.2027 представить в суд {objects}.'
    assert extract_early_report_required(text).value is True
    assert extract_report_deadline_candidates(text, None).selected.value == '15.04.2027'


@pytest.mark.parametrize('instruction', [
    'До 16.04.2027 финансовому управляющему представить отчет.',
    'До даты окончания процедуры реализации (до 16.04.2027), после завершения расчетов '
    'с кредиторами финансовому управляющему представить ходатайство о завершении процедуры, отчет о результатах реализации имущества.',
    'Финансовому управляющему до 16.04.2027 представить отчет.',
])
def test_printed_deadline_can_precede_the_manager(instruction):
    text = 'РЕШИЛ: ' + instruction
    result = extract_report_deadline_candidates(text, '16.04.2027')
    assert result.selected.value == '16.04.2027'
    assert result.selected.deadline_basis == 'explicit_date'
    assert text[result.selected.start:result.selected.end] == result.selected.quote


@pytest.mark.parametrize('marker', ['за три дня до окончания процедуры', 'за 5 рабочих дней до окончания процедуры', 'заблаговременно'])
def test_relative_basis_keeps_agreed_ten_calendar_day_policy(marker):
    text = f'РЕШИЛ: Финансовому управляющему {marker} представить в суд отчет.'
    candidate = extract_report_deadline_candidates(text, '08.04.2027').selected
    assert candidate.value == '29.03.2027'
    assert candidate.deadline_basis == 'relative_advance'
    validate_report_deadline(text, candidate.value, candidate.deadline_basis, candidate.quote, '08.04.2027')


def test_named_appointment_reference_uses_appointment_not_other_end_date():
    text = ('РЕШИЛ: Ввести процедуру реализации имущества до 20.04.2027. '
            'Назначить судебное заседание на 15.04.2027. '
            'Финансовому управляющему к назначенной дате представить в суд отчет.')
    candidate = extract_report_deadline_candidates(text, '15.04.2027').selected
    assert candidate.value == '15.04.2027'
    assert candidate.deadline_basis == 'date_reference'


def test_past_hearing_is_not_an_operative_appointment_for_report_reference():
    text = ('Заявление принято к производству, заседание назначено на 12.10.2026. '
            'РЕШИЛ: Ввести процедуру реализации имущества до 12.04.2027. '
            'Финансовому управляющему к назначенной дате представить в суд отчет.')
    assert extract_early_report_required(text).value is None
    assert extract_report_deadline_candidates(text, '12.04.2027').problem == 'unresolved_report_date_reference'
    fields = {'early_report_required': {'value': False}, 'early_report_deadline': {'value': None},
              'next_session_date': {'value': '12.04.2027'}}
    routing._validate_relationships(fields, text)
    assert all(fields[k]['value'] is None and fields[k]['validation_issue'] for k in ('early_report_required', 'early_report_deadline'))


def test_correct_first_gemini_printed_deadline_is_not_retried_as_minus_ten(monkeypatch):
    text = ('РЕШИЛ: До даты окончания процедуры (до 16.04.2027), после завершения расчетов '
            'финансовому управляющему представить ходатайство о завершении процедуры, отчет о результатах реализации имущества.')
    calls = []
    def provider(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(winner_model='google/gemini-4-argon', parsed={'early_report_deadline': {
            'value': '16.04.2027', 'confidence': 1, 'ambiguous': False, 'reasoning': 'Явная дата поручения',
            'evidence': text, 'deadline_basis': 'explicit_date'}})
    monkeypatch.setattr(selective, 'call_llm_json_with_fallback', provider)
    monkeypatch.setattr(selective, 'get_field_instruction', lambda *a, **kw: kw.get('default', ''))
    output = asyncio.run(selective.extract_court_llm_fields(text, {'early_report_deadline': {}},
        {'models': {'llm_extraction': {'model': 'google/gemini-4-argon'}}},
        reference_fields={'next_session_date': {'value': '16.04.2027'}}))
    assert len(calls) == 1
    assert output['early_report_deadline']['value'] == '16.04.2027'
    schema = calls[0]['response_schema']['json_schema']['schema']
    assert 'deadline_basis' in str(schema)


def test_unknown_printed_syntax_cannot_be_forced_into_subtraction():
    with pytest.raises(ValueError, match='Вычитание дней не требуется'):
        validate_report_deadline('РЕШИЛ: Срок по специальному условию.', '16.04.2027',
                                 'explicit_date', 'Срок по специальному условию.', '16.04.2027')


def test_analysis_quote_cannot_support_a_relative_report_deadline():
    evidence = 'Финансовому управляющему за пять дней до окончания процедуры представить анализ финансового состояния.'
    text = 'РЕШИЛ: ' + evidence + ' После завершения расчетов представить отчет.'
    with pytest.raises(ValueError, match='самого отчёта'):
        validate_report_deadline(text, '29.03.2027', 'relative_advance', evidence, '08.04.2027')


def test_report_quote_can_inherit_actor_established_in_full_source():
    evidence = 'За пять дней до окончания процедуры представить в суд отчет.'
    text = 'РЕШИЛ: Финансовому управляющему провести мероприятия. ' + evidence
    validate_report_deadline(text, '29.03.2027', 'relative_advance', evidence, '08.04.2027')


def test_false_flag_and_nonempty_deadline_cannot_pass_final_merge():
    fields = {'early_report_required': {'value': False}, 'early_report_deadline': {'value': '02.04.2027'}}
    routing._validate_relationships(fields)
    assert all(v['value'] is None and v['validation_issue'] for v in fields.values())


def test_contradictory_jev_proposals_reopen_only_report_pair_without_reverification(monkeypatch):
    text = 'РЕШИЛ: Финансовому управляющему до 16.04.2027 представить в суд отчет.'
    proposals = {'early_report_required': CourtCandidate(False, text, 0, len(text), 'test', {}),
                 'early_report_deadline': CourtCandidate('16.04.2027', text, 0, len(text), 'test', {'deadline_basis':'explicit_date'})}
    monkeypatch.setattr(routing, 'extract_court_candidates', lambda *a: CourtCandidates(proposals, {}))
    calls = []
    def verify(**kwargs):
        calls.append('jev')
        return SimpleNamespace(winner_model='typesafe/jev-1.14', parsed={'answers': {
            k: {'type': 'noul', 'noul': .99} for k in proposals}})
    async def llm(text, fields, config, **kwargs):
        calls.append('llm')
        assert set(fields) == set(proposals)
        assert not (kwargs.get('reference_fields') or {}).get('early_report_required')
        return {'early_report_required': {'value': True, 'source': 'court_llm_fallback'},
                'early_report_deadline': {'value': '16.04.2027', 'deadline_basis': 'explicit_date', 'evidence': text, 'source': 'court_llm_fallback'}}
    monkeypatch.setattr(routing, 'call_llm_json_with_fallback', verify)
    monkeypatch.setattr(routing, 'extract_court_llm_fields', llm)
    result = asyncio.run(routing.extract_court_regex_jev(text, {
        'fields': {k:{} for k in proposals}, 'models': {'verification': {'threshold': .6}}}, document_id='test', pipeline_run_id='test'))
    assert result['early_report_required']['value'] is True
    assert result['early_report_deadline']['value'] == '16.04.2027'
    assert calls == ['jev', 'llm']

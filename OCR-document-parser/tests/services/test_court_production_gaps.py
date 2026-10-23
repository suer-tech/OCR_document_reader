"""Source-shaped regressions from the 23 October production audit."""
import asyncio
from types import SimpleNamespace

import pytest

from ocr_platform.services import court_regex_jev as routing, court_selective_llm as selective
from ocr_platform.services.court_decision_sections import alternative_motivation_span, extract_motivating_part
from ocr_platform.services.court_regex_early_report_required import extract_early_report_required
from ocr_platform.services.court_regex_report_deadline import extract_report_deadline_candidates
from ocr_platform.services.court_report_validation import validate_report_deadline

MODEL='google/gemini-4-argon'
CONFIG={'models':{'llm_extraction':{'model':MODEL}}}

def item(value,**extra):
    return {'value':value,'confidence':.98,'ambiguous':False,'reasoning':'Проверено по текущему распоряжению',**extra}

def test_report_uses_validated_dates_from_same_response_even_in_reverse_field_order(monkeypatch):
    text=('РЕШЕНИЕ\n21 октября 2026 года\nРЕШИЛ:\n'
          'Открыть процедуру реализации имущества гражданина сроком до 12 апреля 2027.\n'
          'Финансовому управляющему после завершения расчетов с кредиторами, за десять дней до даты '
          'окончания срока процедуры, представить в арбитражный суд отчет о результатах реализации имущества.')
    parsed={'early_report_deadline':item('02.04.2027',deadline_basis='relative_advance',evidence=text),
            'next_session_date':item('12.04.2027',selection_basis='procedure_end_fallback',evidence=text),
            'procedure_end_date':item('12.04.2027',evidence=text)}
    calls=[]
    def provider(**kw):
        calls.append(kw)
        return SimpleNamespace(winner_model=MODEL,parsed=parsed)
    monkeypatch.setattr(selective,'call_llm_json_with_fallback',provider)
    monkeypatch.setattr(selective,'get_field_instruction',lambda *a,**kw:kw.get('default',''))
    fields={k:{} for k in parsed}
    result=asyncio.run(selective.extract_court_llm_fields(text,fields,CONFIG))
    assert result['early_report_deadline']['value']=='02.04.2027'
    assert len(calls)==1
    assert not any(v.get('validation_issue') for v in result.values())

def test_approved_absent_next_date_depends_on_rejected_end_and_is_sent_to_llm(monkeypatch):
    text=('РЕШЕНИЕ\n20 октября 2026 года\nРЕШИЛ:\n'
          'Ввести процедуру реализации имущества гражданина на срок до шести месяцев.')
    jev_calls=[]
    requested=[]
    def jev(**kw):
        jev_calls.append(kw)
        return SimpleNamespace(winner_model='typesafe/jev-1.14',parsed={'answers':{
            'procedure_end_date':{'type':'noul','noul':.25},'next_session_date':{'type':'noul','noul':.8}}})
    async def llm(text,fields,config,**kw):
        requested.append(list(fields))
        return {k:{'value':'20.04.2027','confidence':.98,'source':'court_llm_fallback','evidence':text,
                   'selection_basis':'procedure_end_fallback'} for k in fields}
    monkeypatch.setattr(routing,'call_llm_json_with_fallback',jev)
    monkeypatch.setattr(routing,'extract_court_llm_fields',llm)
    config={'fields':{'procedure_end_date':{},'next_session_date':{}},'models':{'verification':{'threshold':.6}}}
    result=asyncio.run(routing.extract_court_regex_jev(text,config,document_id='d',pipeline_run_id='r'))
    assert requested==[['procedure_end_date','next_session_date']]
    assert result['next_session_date']['value']==result['procedure_end_date']['value']=='20.04.2027'
    assert len(jev_calls)==1
    assert result['next_session_date']['extraction_method']=='court_llm_fallback'

def test_next_date_null_is_retried_using_confirmed_end_instead_of_surviving_as_absence(monkeypatch):
    text='РЕШИЛ: Ввести процедуру реализации имущества гражданина до 20.04.2027.'
    calls=[]
    def provider(**kw):
        calls.append(kw)
        value=None if len(calls)==1 else '20.04.2027'
        return SimpleNamespace(winner_model=MODEL,parsed={'next_session_date':item(value,
            selection_basis='not_found' if value is None else 'procedure_end_fallback',evidence=text)})
    monkeypatch.setattr(selective,'call_llm_json_with_fallback',provider)
    monkeypatch.setattr(selective,'get_field_instruction',lambda *a,**kw:kw.get('default',''))
    result=asyncio.run(selective.extract_court_llm_fields(text,{'next_session_date':{}},CONFIG,
        reference_fields={'procedure_end_date':{'value':'20.04.2027'}}))
    assert result['next_session_date']['value']=='20.04.2027'
    assert len(calls)==2

def test_relative_deadline_does_not_use_unvalidated_sibling_date(monkeypatch):
    text='РЕШИЛ: Финансовому управляющему за десять дней до окончания процедуры представить в суд отчет.'
    def provider(**kw):
        return SimpleNamespace(winner_model=MODEL,parsed={
            'next_session_date':item('32.04.2027',selection_basis='procedure_end_fallback',evidence=text),
            'early_report_deadline':item('02.04.2027',deadline_basis='relative_advance',evidence=text)})
    monkeypatch.setattr(selective,'call_llm_json_with_fallback',provider)
    monkeypatch.setattr(selective,'get_field_instruction',lambda *a,**kw:kw.get('default',''))
    result=asyncio.run(selective.extract_court_llm_fields(text,{'next_session_date':{},'early_report_deadline':{}},CONFIG))
    assert all(result[k]['value'] is None and result[k]['validation_issue'] for k in result)

@pytest.mark.parametrize('action',['поступило','передано'])
@pytest.mark.parametrize('wrap',[' ','\n'])
def test_prose_motivation_keeps_earlier_facts_and_complete_legal_reasoning(action,wrap):
    value=(f'На рассмотрение Арбитражного суда Республики Башкортостан{wrap}{action} заявление гражданина.\n'
           'Заявление подано в связи с задолженностью.\n'
           'Изучив материалы дела, исследовав представленные документы, арбитражный суд приходит к следующему выводу.\n'
           'Долг подтвержден. Руководствуясь статьями закона, суд')
    text='РЕШЕНИЕ\nСуд рассмотрел заявление.\n'+value+'\nРЕШИЛ:\nПризнать банкротом.'
    assert extract_motivating_part(text)==value
    span=alternative_motivation_span(text)
    assert text[span[0]:span[1]]==value
    raw=item({'start_anchor':value.split('\n')[0]+'\n'+value.split('\n')[1] if wrap=='\n' else value.split('\n')[0],
              'end_anchor':'Долг подтвержден. Руководствуясь статьями закона, суд'},evidence=None)
    assert selective._field_output('motivating_part',raw,text)['value']==value

def test_prose_motivation_without_narrative_uses_unique_legal_opening():
    value=('Изучив материалы дела, исследовав представленные документы, арбитражный суд приходит к следующему выводу.\n'
           'Долг подтвержден. Руководствуясь законом, суд')
    text='РЕШЕНИЕ\nСуд рассмотрел заявление.\n'+value+'\nРЕШИЛ:\nПризнать банкротом.'
    assert extract_motivating_part(text)==value

def test_source_with_two_narrative_openings_is_not_guessed():
    text=('РЕШЕНИЕ\nНа рассмотрение суда поступило заявление.\n'
          'На рассмотрение суда передано заявление.\n'
          'Изучив материалы дела, исследовав представленные документы, арбитражный суд приходит к следующему выводу.\n'
          'Обоснование.\nРЕШИЛ:\nПризнать банкротом.')
    assert alternative_motivation_span(text) is None

def test_recognized_prose_part_cannot_be_accepted_as_ordinary_high_confidence_null():
    text=('РЕШЕНИЕ\nНа рассмотрение суда поступило заявление.\n'
          'Изучив материалы дела, исследовав представленные документы, арбитражный суд приходит к следующему выводу.\n'
          'Долг доказан.\nРЕШИЛ:\nПризнать банкротом.')
    with pytest.raises(ValueError,match='альтернативное начало'):
        selective._field_output('motivating_part',item(None,evidence=None),text)
    ambiguous=selective._field_output('motivating_part',item(None,ambiguous=True,evidence=None),text)
    assert ambiguous['value'] is None and ambiguous['validation_issue']

def test_llm_explicit_appointment_is_not_replaced_by_end_when_regex_misses_its_wording(monkeypatch):
    text='РЕШИЛ: Вызвать стороны для обсуждения отчета 20.03.2027. Завершить процедуру до 20.04.2027.'
    calls=[]
    def provider(**kw):
        calls.append(kw)
        return SimpleNamespace(winner_model=MODEL,parsed={'next_session_date':item('20.03.2027',
            selection_basis='explicit_appointment',evidence=text)})
    monkeypatch.setattr(selective,'call_llm_json_with_fallback',provider)
    monkeypatch.setattr(selective,'get_field_instruction',lambda *a,**kw:kw.get('default',''))
    result=asyncio.run(selective.extract_court_llm_fields(text,{'next_session_date':{}},CONFIG,
        reference_fields={'procedure_end_date':{'value':'20.04.2027'}}))
    assert result['next_session_date']['value']=='20.03.2027'
    assert len(calls)==1

def test_plural_legal_transition_keeps_debtor_narrative():
    value=('Должник обратился в Арбитражный суд с заявлением о банкротстве.\n'
           'Задолженность подтверждена.\n'
           'Изучив материалы дела, арбитражный суд приходит к следующим выводам.\n'
           'Применяются нормы закона. Руководствуясь законом, суд')
    text='РЕШЕНИЕ\nРассмотрено заявление должника.\n'+value+'\nРЕШИЛ:\nПризнать банкротом.'
    assert extract_motivating_part(text)==value

def test_explicit_due_date_applies_to_report_in_a_shared_submission_object_list():
    text=('РЕШИЛ:\nФинансовому управляющему в срок до 15.04.2027 года представить в суд:\n'
          '- ходатайство о продлении/завершении процедуры реализации имущества\n'
          '- отчет о ходе проведения процедуры реализации имущества с приложением подтверждающих документов;\n'
          'Разъяснить последствия неисполнения решения.')
    assert extract_early_report_required(text).value is True
    r=extract_report_deadline_candidates(text,'15.04.2027')
    assert r.selected.value=='15.04.2027'
    assert r.selected.deadline_basis=='explicit_date'
    validate_report_deadline(text,'15.04.2027','explicit_date',r.selected.quote,'15.04.2027')

def test_bullet_with_a_new_temporal_instruction_does_not_share_analysis_due_date():
    text=('РЕШИЛ:\nФинансовому управляющему до 15.04.2027 представить в суд:\n'
          '- анализ финансового состояния;\n'
          '- после завершения расчетов с кредиторами представить отчет о результатах реализации имущества.\n')
    assert extract_early_report_required(text).value is False
    assert extract_report_deadline_candidates(text,'15.04.2027').selected is None

def test_proof_of_report_delivery_in_object_list_is_not_the_report_itself():
    text=('РЕШИЛ:\nФинансовому управляющему до 15.04.2027 представить в суд:\n'
          '- доказательства направления отчета кредиторам;\n'
          '- анализ финансового состояния.\n')
    assert extract_early_report_required(text).value is False
    assert extract_report_deadline_candidates(text,'15.04.2027').selected is None

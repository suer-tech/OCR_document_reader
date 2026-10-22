"""Source-observed failures plus safeguards against unsafe generalization."""
import pytest
import asyncio
from types import SimpleNamespace

from ocr_platform.services.court_regex_candidates import extract_court_candidates
from ocr_platform.services.court_regex_resolutive_part_date import extract_resolutive_part_date_candidates
from ocr_platform.services.court_decision_sections import extract_motivating_part
from ocr_platform.services.court_regex_jev import _validate_relationships
from ocr_platform.services.court_selective_llm import _field_output
from ocr_platform.services.court_field_provenance import finalize_court_fields
from ocr_platform.services.validation_service import court_field_issues, review_requirement


@pytest.mark.parametrize('title', ['РЕЗОЛ ЮТИВ НА Я ЧА С ТЬ', 'Р Е З О Л Ю Т И В Н А Я Ч А С Т Ь',
                                 'Резолютивная\u00a0ча\u00a0сть'])
def test_spaced_standalone_title_retains_literal_offsets(title):
    text=f'АРБИТРАЖНЫЙ СУД\nРЕШЕНИЕ\n{title}\n30 апреля 2026 года\nСуд в составе судьи\nРЕШИЛ:\nПризнать банкротом.'
    result=extract_resolutive_part_date_candidates(text)
    assert result.selected.value=='30.04.2026'
    assert text[result.selected.start:result.selected.end]==result.selected.quote
    item=_field_output('resolutive_part_date', {'value':'30.04.2026','confidence':.98,
        'ambiguous':False,'evidence':'30 апреля 2026 года','reasoning':'Дата отдельного акта.'},text)
    assert item['value']=='30.04.2026'


@pytest.mark.parametrize('prefix', ['Цитата из другого акта:\n', 'Из решения другого суда:\n'])
def test_spaced_title_does_not_accept_a_quoted_foreign_act(prefix):
    text=prefix+'РЕШЕНИЕ\nРЕЗОЛ ЮТИВ НА Я ЧА С ТЬ\n30 апреля 2026 года\nРЕШИЛ:\nПризнать банкротом.'
    assert extract_resolutive_part_date_candidates(text).selected is None


def test_spaced_resolutive_only_title_cannot_establish_a_motivation():
    text=('РЕШЕНИЕ\nРЕЗОЛ ЮТИВ НА Я ЧА С ТЬ\n30 апреля 2026 года\n'
          'При рассмотрении заявления арбитражным судом установлены следующие обстоятельства.\n'
          'Долг установлен.\nРЕШИЛ:\nПризнать банкротом.')
    assert extract_motivating_part(text) is None


@pytest.mark.parametrize('opening',[
    'При рассмотрении заявления арбитражным судом установлены следующие обстоятельства.',
    'При рассмотрении заявления арбитражным судом установлены следующие\nобстоятельства.',
    'При рассмотрении материалов дела, арбитражным судом установлены следующие обстоятельства:',
    'Изучив материалы дела, арбитражный суд приходит к следующему.',
    'Изучив материалы дела,\nарбитражный суд приходит к следующему.',
])
def test_alternative_prose_opening_works_in_regex_and_llm_validator(opening):
    value=opening+'\nДолг подтвержден. Суд установил, что гражданин неплатежеспособен.\nРуководствуясь законом, арбитражный суд'
    text='РЕШЕНИЕ\nСуд рассмотрел заявление.\n'+value+'\nР Е Ш И Л:\nВвести процедуру.'
    assert extract_motivating_part(text)==value
    item={'value':{'start_anchor':opening,'end_anchor':'Руководствуясь законом, арбитражный суд'},
          'confidence':.98,'ambiguous':False,'reasoning':'Подтверждённые границы.','evidence':opening}
    assert _field_output('motivating_part',item,text)['value']==value
    item['value']['end_anchor']='Долг подтвержден. Суд установил, что гражданин неплатежеспособен.'
    with pytest.raises(ValueError):
        _field_output('motivating_part',item,text)


def test_standard_heading_before_prose_opening_keeps_initial_paragraphs():
    value=('Начальные факты.\nПри рассмотрении заявления арбитражным судом установлены '
           'следующие обстоятельства.\nДополнительные факты.')
    text='РЕШЕНИЕ\nУСТАНОВИЛ:\n'+value+'\nРЕШИЛ:\nВвести процедуру.'
    assert extract_motivating_part(text)==value
    item={'value':{'start_anchor':'Начальные факты.','end_anchor':'Дополнительные факты.'},
          'confidence':.98,'ambiguous':False,'reasoning':'Полные границы.','evidence':'Начальные факты.'}
    assert _field_output('motivating_part',item,text)['value']==value


@pytest.mark.parametrize('prefix',['Цитата:\n','Из решения другого суда:\n'])
def test_reasoning_transition_in_quote_is_not_a_current_section(prefix):
    text=prefix+'Изучив материалы дела, арбитражный суд приходит к следующему.\nФакты.\nРЕШИЛ:\nПризнать банкротом.'
    assert extract_motivating_part(text) is None


@pytest.mark.parametrize('number,expected',[
    ('А40-123/26-187-90 «Б»','А40-123/2026-187-90«Б»'),
    ('A40-123/26 "Б"','А40-123/2026"Б"'),
    ('А43-123/26 (3-484)','А43-123/2026(3-484)'),
    ('А40-123/26-73-581Ф','А40-123/2026-73-581Ф'),
])
def test_case_tail_is_kept_with_complete_source_quote(number,expected):
    text=f'РЕШЕНИЕ\nДело № {number}\n29 апреля 2026 года\nУСТАНОВИЛ:\nФакты.\nРЕШИЛ:\nВвести процедуру.'
    candidate=extract_court_candidates(text,['case_number']).fields['case_number']
    assert candidate.value==expected
    assert candidate.quote=='Дело № '+number
    assert text[candidate.start:candidate.end]==candidate.quote


@pytest.mark.parametrize('tail',['\n«Б»',' «Решение другого суда»',' (судья Иванов)'])
def test_unrelated_prose_or_next_line_is_not_a_case_tail(tail):
    text='Дело № А40-123/26'+tail+'\n29 апреля 2026 года'
    assert extract_court_candidates(text,['case_number']).fields['case_number'].value=='А40-123/2026'


@pytest.mark.parametrize('act_field', ['decision_date','resolutive_part_date'])
def test_contradictory_source_date_is_retained_and_forces_review(act_field):
    quote='Определением суда от 07 ноября 2026 года заявление принято к производству.'
    text='РЕШЕНИЕ\n29 апреля 2026 года\nУСТАНОВИЛ:\n'+quote+'\nРЕШИЛ:\nПризнать банкротом.'
    start=text.index(quote)
    fields={'application_acceptance_date':{'value':'07.11.2026','confidence':.98,'source':'regex_jev',
        'evidence':quote,'evidence_start':start,'evidence_end':start+len(quote)},
        act_field:{'value':'29.04.2026','source':'regex_jev'}}
    _validate_relationships(fields)
    fields=finalize_court_fields(text,fields)
    assert fields['application_acceptance_date']['value']=='07.11.2026'
    assert quote in fields['application_acceptance_date']['reasoning']
    assert 'позже' in fields['application_acceptance_date']['validation_issue']
    issues=court_field_issues(fields)
    assert [issue.field_name for issue in issues]==['application_acceptance_date']
    assert review_requirement(.99,issues)==(True,'court_field_requires_review')


@pytest.mark.parametrize('acceptance',['28.04.2026','29.04.2026',None])
def test_consistent_or_missing_date_does_not_invent_a_conflict(acceptance):
    fields={'application_acceptance_date':{'value':acceptance},'decision_date':{'value':'29.04.2026'}}
    _validate_relationships(fields)
    assert 'validation_issue' not in fields['application_acceptance_date']


@pytest.mark.parametrize('deadline,base', [('25.08.2026','10.09.2026'),('30.11.2026','07.12.2026')])
def test_literal_report_deadline_survives_jev_dependencies_and_relationships(deadline,base):
    from ocr_platform.services.court_jev_questions import verification_question

    text=f'РЕШИЛ:\nФинансовому управляющему:\n- представить в суд отчет о своей деятельности в срок до {deadline}.'
    candidate=extract_court_candidates(text,['early_report_deadline']).fields['early_report_deadline']
    assert candidate.value==deadline and candidate.dependencies==()
    assert candidate.metadata['basis']=='explicit_report_date'
    assert 'прямо как срок' in verification_question('early_report_deadline',candidate)['instructions']
    item={'value':deadline,'confidence':.98,'source':'regex_jev','regex_rule':candidate.rule,
          'evidence':candidate.quote,'evidence_start':candidate.start,'evidence_end':candidate.end}
    fields={'early_report_required':{'value':True},'early_report_deadline':item,'next_session_date':{'value':base}}
    _validate_relationships(fields,text)
    final=finalize_court_fields(text,fields)['early_report_deadline']
    assert final['value']==deadline and 'validation_issue' not in final
    assert 'явно напечатанный срок' in final['reasoning']
    assert candidate.quote in final['reasoning']


def test_llm_cannot_replace_a_literal_report_date_with_minus_ten():
    text='РЕШИЛ:\nФинансовому управляющему в срок до 25.08.2026 представить в суд отчет.'
    item={'value':'31.08.2026','confidence':.98,'ambiguous':False,'reasoning':'Вычтено 10 дней.',
          'evidence':'в срок до 25.08.2026 представить в суд отчет', 'deadline_basis':'explicit_date'}
    with pytest.raises(ValueError,match='явно указанной'):
        _field_output('early_report_deadline',item,text)
    item['value']='25.08.2026'
    assert _field_output('early_report_deadline',item,text)['value']=='25.08.2026'


def test_conflicting_literal_report_dates_are_not_resolved_by_calculation():
    from ocr_platform.services.court_regex_report_deadline import extract_report_deadline_candidates

    text=('РЕШИЛ: Финансовому управляющему до 22.03.2027 представить отчет; '
          'финансовому управляющему до 23.03.2027 представить отчет.')
    result=extract_report_deadline_candidates(text,'15.04.2027')
    assert result.selected is None and result.problem=='conflicting_explicit_report_dates'


def test_card_transfer_act_date_cannot_borrow_report_from_the_next_assignment():
    from ocr_platform.services.court_regex_report_deadline import extract_report_deadline_candidates

    text=('РЕШИЛ: Финансовому управляющему в срок до 14.10.2026 представить в суд '
          'Акт приема-передачи банковских карт, материальных и иных ценностей.\n'
          'Финансовому управляющему в срок до 30.03.2027 представить в суд: '
          'актуальный отчет о результатах реализации имущества.')
    result=extract_report_deadline_candidates(text,'15.04.2027')
    assert [c.value for c in result.explicit_dates]==['30.03.2027']
    assert result.selected.value=='30.03.2027'


def test_dated_position_about_a_report_is_not_submission_of_the_report():
    from ocr_platform.services.court_regex_report_deadline import extract_report_deadline_candidates

    text='РЕШИЛ: Лицам, участвующим в деле, до 15.04.2027 представить в суд позицию по отчету управляющего.'
    assert not extract_report_deadline_candidates(text,None).explicit_dates


def test_optional_null_from_llm_cannot_erase_a_known_manager_source_conflict(monkeypatch):
    from ocr_platform.services import court_regex_jev as route

    text=('РЕШЕНИЕ\nРЕШИЛ:\nПризнать Иванова Ивана Ивановича банкротом.\n'
          'Утвердить финансовым управляющим Иванова Ивана Ивановича члена СРО.')
    def jev(**kwargs):
        return SimpleNamespace(parsed={'answers':{'debtor_full_name':{'type':'noul','noul':.99}}},winner_model='test')
    async def llm(*args,**kwargs):
        return {'financial_manager_full_name':{'value':None,'confidence':.3,'source':'court_llm_fallback',
                                             'reasoning':'Надёжное ФИО отсутствует.'}}
    monkeypatch.setattr(route,'call_llm_json_with_fallback',jev)
    monkeypatch.setattr(route,'extract_court_llm_fields',llm)
    fields=asyncio.run(route.extract_court_regex_jev(text,{'fields':{'debtor_full_name':{},'financial_manager_full_name':{}},
        'models':{'verification':{'threshold':.6}}},document_id='d',pipeline_run_id='r'))
    assert fields['financial_manager_full_name']['value'] is None
    assert 'ФИО должника' in fields['financial_manager_full_name']['validation_issue']
    assert review_requirement(.99,court_field_issues(fields))==(True,'court_field_requires_review')


@pytest.mark.parametrize('instruction',[
    'Финансовому управляющему представить в суд за 10 рабочих дней до истечения срока процедуры отчет о результатах.',
    'Финансовому управляющему за десять рабочих дней до истечения срока процедуры представить в суд отчет о результатах.',
    'Финансовому управляющему представить отчет за пять дней до окончания процедуры в суд.',
])
def test_report_verb_before_or_after_advance_marker_keeps_ten_calendar_days(instruction):
    from ocr_platform.services.court_regex_report_deadline import extract_report_deadline_candidates

    result=extract_report_deadline_candidates('РЕШИЛ: '+instruction,'29.10.2026')
    assert result.selected.value=='19.10.2026'


def test_inline_established_the_following_is_a_complete_heading_for_both_paths():
    value='Определением суда заявление принято.\nДолг доказан.\nРуководствуясь законом, суд'
    text='РЕШЕНИЕ\nСуд рассмотрел заявление, установил следующее.\n'+value+'\nРЕШИЛ:\nПризнать банкротом.'
    assert extract_motivating_part(text)==value
    item={'value':{'start_anchor':'Определением суда заявление принято.','end_anchor':'Руководствуясь законом, суд'},
          'ambiguous':False,'confidence':.99,'reasoning':'Границы.','evidence':None}
    assert _field_output('motivating_part',item,text)['value']==value


def test_prior_unrelated_submission_does_not_hide_the_report_verb_after_marker():
    from ocr_platform.services.court_regex_report_deadline import extract_report_deadline_candidates

    text=('РЕШИЛ: Финансовому управляющему представить доказательства публикации.\n'
          'Финансовому управляющему заблаговременно представить в суд отчет.')
    assert extract_report_deadline_candidates(text,'29.10.2026').selected.value=='19.10.2026'

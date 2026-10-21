import asyncio
from types import SimpleNamespace

import pytest

from ocr_platform.services.court_case_number_format import normalize_court_case_number
from ocr_platform.services.court_decision_sections import extract_motivating_part
from ocr_platform.services.court_regex_candidates import extract_court_candidates
from ocr_platform.services.court_regex_debtor_registration_address import extract_debtor_registration_address_candidates
from ocr_platform.services import court_regex_jev as route
from ocr_platform.services.court_regex_report_deadline import extract_report_deadline_candidates
from ocr_platform.services.court_selective_llm import _field_output, _validate_section_boundaries, _validate_value


@pytest.mark.parametrize('raw,expected', [
    ('А41-27363/26','А41-27363/2026'),('A07–5952/26','А07-5952/2026'),
    ('А40-123/26-73-581Ф','А40-123/2026-73-581Ф'),
    ('А01-123/2026','А01-123/2026'),('А01-123/20260','А01-123/20260'),
])
def test_case_year_normalization_preserves_suffix(raw,expected):
    assert normalize_court_case_number(raw)==expected
    assert _validate_value('case_number',raw)==expected


def test_case_number_registry_keeps_original_quote():
    text='РЕШЕНИЕ\nДело № А41-27363/26\nПризнать банкротом.'
    c=extract_court_candidates(text,['case_number']).fields['case_number']
    assert c.value=='А41-27363/2026'
    assert '/26' in c.quote and '/2026' not in c.quote
    assert text[c.start:c.end]==c.quote


@pytest.mark.parametrize('marker',['зарегистрированного(ой)','зарегистрированный(ая)',
                                  'зарегистрирован(а)','зарегистрированной ( ого )'])
def test_registered_template_has_no_suffix_in_address(marker):
    value='456418, Челябинская обл., с. Филимоново, ул. Набережная, д. 53'
    text=f'Признать Иванова Ивана Ивановича (ИНН 123456789012, {marker} по адресу: {value}) банкротом.'
    c=extract_debtor_registration_address_candidates(text).selected
    assert c.value==value
    assert marker in c.quote
    assert text[c.start:c.end]==c.quote


def test_registered_template_address_goes_through_jev_without_llm(monkeypatch):
    value='г. Омск, ул. Ленина, д. 5'
    text=f'Признать Иванова Ивана Ивановича (зарегистрированного(ой) по адресу: {value}) банкротом.'
    def jev(**kw):
        assert kw['decisions_state']['fields']['debtor_registration_address']['value']==value
        return SimpleNamespace(parsed={'answers':{'debtor_registration_address':{'type':'noul','noul':.99}}},winner_model='test')
    async def llm(*a,**kw):
        pytest.fail('Confirmed regex address must not trigger LLM')
    monkeypatch.setattr(route,'call_llm_json_with_fallback',jev)
    monkeypatch.setattr(route,'extract_court_llm_fields',llm)
    output=asyncio.run(route.extract_court_regex_jev(text,{'fields':{'debtor_registration_address':{}},
        'models':{'verification':{'threshold':.6}}},document_id='d',pipeline_run_id='r'))
    assert output['debtor_registration_address']['extraction_method']=='regex_jev'


@pytest.mark.parametrize('opening',[
    'При рассмотрении материалов дела, судом установлены следующие обстоятельства.',
    'При рассмотрении материалов дела, судом установлены следующие\nобстоятельства.',
    'Судом установлены следующие обстоятельства:',
])
def test_alternative_motivation_contains_whole_reasoning_and_no_operative(opening):
    value=opening+'\nДолжник неплатежеспособен. Согласно доказательствам долг установлен.\nРуководствуясь законом, суд'
    text='РЕШЕНИЕ\nСуд рассмотрел заявление.\n'+value+'\nРЕШИЛ:\nПризнать банкротом.'
    assert extract_motivating_part(text)==value
    start=text.index(opening)
    _validate_section_boundaries('motivating_part',start,start+len(value),text)
    with pytest.raises(ValueError):
        _validate_section_boundaries('motivating_part',start,start+len(opening),text)


@pytest.mark.parametrize('prefix',[
    'РЕЗОЛЮТИВНАЯ ЧАСТЬ РЕШЕНИЯ\n',
    'Из решения другого суда:\n',
])
def test_resolutive_only_or_quoted_alternative_is_not_motivation(prefix):
    text=prefix+'Судом установлены следующие обстоятельства:\nДолг установлен.\nРЕШИЛ:\nПризнать банкротом.'
    assert extract_motivating_part(text) is None


def test_resolutive_date_cannot_be_filled_from_full_act_header_by_llm():
    text='РЕШЕНИЕ\n22.09.2026\nСуд рассмотрел дело.\nРЕШИЛ:\nПризнать банкротом.'
    with pytest.raises(ValueError):
        _field_output('resolutive_part_date',{'value':'22.09.2026','reasoning':'Шапка',
            'evidence':'22.09.2026','confidence':.99,'ambiguous':False},text)


@pytest.mark.parametrize('instruction',[
    'Финансовому управляющему представить отчет о результатах процедуры.',
    'Финансовому управляющему после завершения расчетов представить в суд отчет.',
    'Финансовому управляющему представить сведения о публикации.',
    'Рассмотреть отчет после поступления ходатайства.',
])
def test_report_without_advance_or_explicit_timing_does_not_get_minus_ten(instruction):
    assert extract_report_deadline_candidates('РЕШИЛ: '+instruction,'15.03.2027').selected is None


@pytest.mark.parametrize('required',[True,False,None])
def test_unresolved_required_report_is_not_silently_accepted(required):
    fields={'early_report_required':{'value':required},
            'early_report_deadline':{'value':None,'source':'court_llm_fallback'}}
    route._validate_relationships(fields)
    assert fields['early_report_deadline']['value'] is None
    assert ('validation_issue' in fields['early_report_deadline']) is (required is True)
    if required is True:
        from ocr_platform.services.validation_service import court_field_issues, review_requirement
        issues=court_field_issues(fields)
        assert review_requirement(.99,issues)==(True,'court_field_requires_review')

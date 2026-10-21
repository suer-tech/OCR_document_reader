from datetime import date

import pytest

from ocr_platform.services.court_decision_legacy_rules import extract_early_report_deadline
from ocr_platform.services.court_regex_report_deadline import extract_report_deadline_candidates


@pytest.mark.parametrize('marker', [
    'заблаговременно', '– заблаговременно',
    'не позднее чем за три дня до окончания процедуры',
    'за 5 дней до окончания срока процедуры',
    'за пять рабочих дней до даты окончания процедуры',
    'за десять календарных дней до судебного заседания',
])
def test_confirmed_ten_day_policy_ignores_literal_day_count(marker):
    text = f'РЕШИЛ: Финансовому управляющему {marker} представить в суд отчет о результатах реализации имущества.'
    result = extract_report_deadline_candidates(text, '15.03.2027')
    assert result.selected.value == '05.03.2027'
    assert text[result.selected.start:result.selected.end] == result.selected.quote


@pytest.mark.parametrize('base, expected', [
    ('01.03.2028', '20.02.2028'), ('01.01.2027', '22.12.2026'),
    ('2027-03-15', '05.03.2027'),
])
def test_calendar_subtraction(base, expected):
    text = 'РЕШИЛ: Финансовому управляющему – заблаговременно представить в суд отчет.'
    assert extract_report_deadline_candidates(text, base).selected.value == expected


@pytest.mark.parametrize('base', [None, '', '31.02.2027', 'not a date'])
def test_no_next_session_means_no_calculated_report_due_date(base):
    text = 'РЕШИЛ: Финансовому управляющему – заблаговременно представить в суд отчет.'
    result = extract_report_deadline_candidates(text, base)
    assert result.selected is None
    assert result.problem == 'missing_next_session_date'


def test_reasoning_and_quoted_law_are_not_new_requirements():
    text = ('Согласно закону управляющему необходимо заблаговременно представить в суд отчет. '
            'Р Е Ш И Л: Ввести процедуру реализации имущества на шесть месяцев.')
    assert extract_report_deadline_candidates(text, '15.03.2027').selected is None


def test_missing_operative_heading_does_not_expand_reasoning_only_rules():
    text = 'По закону после расчетов заблаговременно представить в суд отчет.'
    result = extract_report_deadline_candidates(text, '15.03.2027')
    assert result.selected is None
    assert result.problem == 'missing_operative_heading'


def test_spaced_heading_without_colon_on_own_line():
    text = 'Суд\nР Е Ш И Л\nФинансовому управляющему – заблаговременно представить в суд отчет.'
    assert extract_report_deadline_candidates(text, '15.03.2027').selected.value == '05.03.2027'


def test_position_before_hearing_is_not_report_submission():
    text = ('РЕШИЛ: Лицам, участвующим в деле, заблаговременно до даты судебного заседания '
            'по рассмотрению отчета представить в суд позицию о наличии оснований освобождения.')
    assert extract_report_deadline_candidates(text, '15.03.2027').selected is None


def test_advance_publication_and_later_report_do_not_merge():
    text = ('РЕШИЛ: Финансовому управляющему – заблаговременно направить публикацию. '
            'Обязать управляющего представить в суд отчет по завершении расчетов.')
    assert extract_report_deadline_candidates(text, '15.03.2027').selected is None


def test_dated_court_filing_with_report_attachment_is_explicit_not_minus_ten():
    text = ('РЕШИЛ: Финансовому управляющему в срок до 15.04.2027 представить в суд '
            'и заблаговременно направить лицам, участвующим в деле, заявление '
            'о завершении с представлением отчета о результатах процедуры.')
    result = extract_report_deadline_candidates(text, '15.04.2027')
    assert result.selected.value == '15.04.2027'
    assert result.selected.basis == 'explicit_report_date'


def test_next_assignment_does_not_supply_missing_report_object():
    text = ('РЕШИЛ: Документы представить за три дня до судебного заседания; обеспечить явку. '
            'Обязать финансового управляющего подготовить и представить совместно с отчетом анализ сделок.')
    assert extract_report_deadline_candidates(text, '25.03.2027').selected is None


@pytest.mark.parametrize('value', ['22.03.2027', '«22» марта 2027', '“22” марта 2027'])
def test_explicit_report_date_has_priority_without_a_next_date(value):
    text = f'РЕШИЛ: Финансовому управляющему в срок до {value} представить в суд отчет.'
    result = extract_report_deadline_candidates(text, None)
    assert [candidate.value for candidate in result.explicit_dates] == ['22.03.2027']
    assert result.selected.value == '22.03.2027'
    assert result.selected.basis == 'explicit_report_date'
    assert result.problem is None
    assert text[result.explicit_dates[0].start:result.explicit_dates[0].end] == result.explicit_dates[0].quote


def test_analysis_due_date_is_not_report_due_date():
    text = ('РЕШИЛ: Финансовому управляющему представить в суд в срок до 29 марта 2027 года '
            'отчеты о своей деятельности; в срок до 12 марта 2027 года представить анализ финансового состояния.')
    assert [item.value for item in extract_report_deadline_candidates(text, None).explicit_dates] == ['29.03.2027']


def test_date_after_days_before_is_base_not_explicit_due_date():
    text = ('РЕШИЛ: Финансовому управляющему не позднее чем за пять дней до 13.04.2027 '
            'представить в суд отчет о проведении процедуры.')
    assert not extract_report_deadline_candidates(text, None).explicit_dates


def test_invalid_explicit_date_is_not_returned():
    text = 'РЕШИЛ: Финансовому управляющему в срок до 31.02.2027 представить в суд отчет.'
    assert not extract_report_deadline_candidates(text, None).explicit_dates


@pytest.mark.parametrize('text,expected', [
    ('РЕШИЛ: Финансовому управляющему заблаговременно представить в суд отчет.','05.03.2027'),
    ('РЕШИЛ: Финансовому управляющему до 22.03.2027 представить в суд отчет.','22.03.2027'),
    ('РЕШИЛ: Отчет представить в суд в срок до 22.03.2027.','22.03.2027'),
])
def test_explicit_priority_changes_only_the_literal_deadline_cases(text,expected):
    old, _ = extract_early_report_deadline(text, '15.03.2027')
    assert old is not None
    result = extract_report_deadline_candidates(text, '15.03.2027')
    assert result.selected.value == expected
    assert date(2027, 3, 5).strftime('%d.%m.%Y') == old


def test_conflicting_exact_dates_remain_separate_review_candidates():
    text = ('РЕШИЛ: Финансовому управляющему до 22.03.2027 представить в суд отчет; '
            'финансовому управляющему до 23.03.2027 представить в суд отчет.')
    result = extract_report_deadline_candidates(text, None)
    assert {item.value for item in result.explicit_dates} == {'22.03.2027', '23.03.2027'}
    assert result.selected is None

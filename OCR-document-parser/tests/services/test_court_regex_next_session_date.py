"""Semantic boundaries for offline, source-bound appointment candidates."""
import pytest

from ocr_platform.services.court_regex_next_session_date import extract_next_session_date_candidates

HEADER = 'РЕШЕНИЕ\n01 октября 2026 года\nСудья Иванов\nРЕШИЛ:\n'


def extract(body):
    return extract_next_session_date_candidates(HEADER + body)


@pytest.mark.parametrize('body', [
    'Назначить судебное заседание по рассмотрению отчета на 10 марта 2027 года.',
    'Судебное заседание по рассмотрению отчета назначить на 10.03.2027.',
    'Дело назначить к рассмотрению на 10.03.2027 в 09:00.',
    'Назначить рассмотрение отчета на 10 марта 2027 года на 10 час. 13 мин.',
    'Назначить с 16 часов 00 минут 10 марта 2027 года судебное заседание.',
    'Назначить судебное заседание на 10 час 25 мин 10 марта 2027 года.',
    'Назначить судебное заседание на «10» марта 2027 года.',
    'Судебное заседание назначено на 10 марта 2027 года.',
    'Судебное заседание состоится 10 марта 2027 года.',
    'Назначить судебное заседание на 10.03.2027. Повторно: назначить судебное заседание на 10.03.2027.',
    'Срок будет продленным.\n4. Назначить судебное заседание на 10.03.2027.',
    'Назначить судебное заседание в отношении гражданина (дата рождения 15.05.1980) на 10.03.2027.',
])
def test_scheduled_consideration(body):
    result = extract(body)
    assert result.selected and result.selected.value == '10.03.2027'
    text = HEADER + body
    for item in result.candidates:
        assert item.quote == text[item.start:item.end]
        assert item.start <= item.date_start < item.date_end <= item.end


@pytest.mark.parametrize('body', [
    'Ввести процедуру реализации имущества до 10.03.2027.',
    'Финансовому управляющему в срок до 10.03.2027 представить отчет.',
    'Суд вправе по своей инициативе назначить судебное заседание. Представить отчет до 10.03.2027.',
    'Судебное заседание будет назначено после поступления ходатайства. Срок процедуры до 10.03.2027.',
    'В случае поступления ходатайства назначить судебное заседание на 10.03.2027.',
    'Назначить финансового управляющего. Судебное заседание не назначается. Срок процедуры до 10.03.2027.',
])
def test_no_fabricated_appointment(body):
    assert extract(body).selected is None


@pytest.mark.parametrize('body,problem', [
    ('Назначить судебное заседание на 31.02.2027.', 'invalid_appointment_date'),
    ('Назначить судебное заседание на 25.02.207.', 'invalid_appointment_date'),
    ('Назначить судебное заседание на 10 марта 2026 года.', 'appointment_not_after_act'),
    ('Назначить судебное заседание на 01.10.2026.', 'appointment_not_after_act'),
    ('Назначить судебное заседание на 10.03.2027 и 11.03.2027.', 'conflicting_appointments'),
    ('Назначить судебное заседание на 10.03.2027. Назначить рассмотрение отчета на 11.03.2027.', 'conflicting_appointments'),
])
def test_review_instead_of_guessing(body, problem):
    result = extract(body)
    assert result.selected is None
    assert result.problem == problem


def test_postponement_supersedes_earlier_appointment():
    result = extract('Назначить судебное заседание на 10.03.2027. Отложить судебное заседание на 20.03.2027.')
    assert result.selected.value == '20.03.2027'


def test_postponement_from_old_to_new_date():
    result = extract('Перенести судебное заседание с 10.03.2027 на 20.03.2027.')
    assert result.selected.value == '20.03.2027'


def test_event_before_postponement_command():
    result = extract('Судебное заседание отложить на 20.03.2027.')
    assert result.selected.value == '20.03.2027'


def test_cancelled_appointment_is_not_selected():
    result = extract('Назначить судебное заседание на 10.03.2027. Отменить ранее назначенное судебное заседание.')
    assert result.selected is None


def test_cancel_old_and_schedule_new():
    result = extract('Назначить судебное заседание на 10.03.2027. Отменить судебное заседание на 10.03.2027. Назначить судебное заседание на 20.03.2027.')
    assert result.selected.value == '20.03.2027'


def test_consideration_without_hearing_is_supported():
    result = extract('Рассмотрение отчета о результатах процедуры осуществляется судьей единолично без проведения судебного заседания и вызова лиц до 10 марта 2027 года.')
    assert result.selected.value == '10.03.2027'
    assert result.selected.role == 'consideration_without_hearing'


def test_prior_narrative_appointment_is_ignored():
    text = 'РЕШЕНИЕ\n01 октября 2026 года\nСудья Иванов\nОпределением назначено заседание на 01.12.2026.\nРЕШИЛ:\nВвести процедуру до 10.03.2027.'
    assert extract_next_session_date_candidates(text).selected is None


def test_relative_to_act_not_current_date():
    text = HEADER.replace('2026', '2016') + 'Назначить судебное заседание на 10.03.2017.'
    assert extract_next_session_date_candidates(text).selected.value == '10.03.2017'


def test_invalid_source_date_cannot_be_replaced_by_later_statute_date():
    result = extract('Назначить судебное заседание на 25.02.207 в помещении суда. С даты акта применяются последствия закона от 26.10.2002.')
    assert result.selected is None
    assert result.problem == 'invalid_appointment_date'


def test_hearing_venue_sentence_does_not_introduce_statute_date():
    result = extract('Назначить судебное заседание на 10.03.2027. Заседание состоится в помещении суда. Согласно закону от 29.05.2024 срок продлевается.')
    assert result.selected.value == '10.03.2027'

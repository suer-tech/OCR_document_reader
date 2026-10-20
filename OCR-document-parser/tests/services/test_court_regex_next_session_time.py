"""Semantic regression cases for research-only next-appointment times."""
import pytest
from ocr_platform.services.court_regex_next_session_time import (
    extract_next_session_time_candidates,
)

HEADER = "РЕШЕНИЕ\n01 октября 2026 года\nСудья Иванов\nРЕШИЛ:\n"


def extract(body):
    return extract_next_session_time_candidates(HEADER + body)


@pytest.mark.parametrize("body,value", [
    ("Назначить судебное заседание на 10.03.2027 в 9:05.", "09:05"),
    ("Судебное заседание назначить на 10 марта 2027 года на 09 час. 33 мин.", "09:33"),
    ("Назначить рассмотрение отчета на 10 марта 2027 г. в 10 часов 00 минут.", "10:00"),
    ("Назначить судебное заседание на 10 марта 2027 года в 08час. 50 мин.", "08:50"),
    ("Назначить судебное заседание на 10 марта 2027 года на 09 час.29 мин.", "09:29"),
    ("Назначить судебное заседание на 10 час 25 мин 10 марта 2027 года.", "10:25"),
    ("Назначить с 16 часов 00 минут 10 марта 2027 года судебное заседание.", "16:00"),
    ("Назначить судебное заседание на 10.03.2027 в 9.05.", "09:05"),
    ("Назначить судебное заседание на 10.03.2027 в 00:00.", "00:00"),
    ("Назначить судебное заседание на 10 марта 2027 года\nв 08 часов 5 минут.", "08:05"),
    ("Назначить судебное заседание гражданина (родился 23.10.1985) на 08 час. 20 мин. 10 марта 2027 г.", "08:20"),
])
def test_explicit_times_and_source_positions(body, value):
    text = HEADER + body
    result = extract_next_session_time_candidates(text)
    assert result.selected.value == value
    for item in result.candidates:
        assert item.quote == text[item.start:item.end]
        assert item.start <= item.date_start < item.date_end <= item.end
        assert item.start <= item.time_start < item.time_end <= item.end
        assert item.session_date == "10.03.2027"
        assert item.requires_confirmation is True


@pytest.mark.parametrize("body", [
    "Назначить судебное заседание на 10.03.2027.",
    "Ввести реализацию имущества до 10.03.2027 в 10:00.",
    "Финансовому управляющему представить отчет до 10.03.2027 в 10:00.",
    "Назначить судебное заседание на 10.03.2027. Подпись ЭП 10.03.2027 11:39:32.",
    "Назначить судебное заседание на 10.03.2027. Приемная суда работает с 09:00.",
    "Назначить судебное заседание на 10.03.2027. Представить отчет до 09:00.",
    "В случае поступления ходатайства назначить судебное заседание на 10.03.2027 в 10:00.",
    "Суд вправе назначить судебное заседание на 10.03.2027 в 10:00.",
    "Назначить судебное заседание на 10.03.2027 в 11:39:32.",
    "Назначить судебное заседание на 10.03.2027 в 10.03.2028.",
    "Назначить судебное заседание на 10.03.2027 в 10 часов.",
])
def test_unrelated_absent_or_incomplete_time_is_not_fabricated(body):
    assert extract(body).selected is None


@pytest.mark.parametrize("body,problem", [
    ("Назначить судебное заседание на 10.03.2027 в 24:10.", "invalid_appointment_time"),
    ("Назначить судебное заседание на 10.03.2027 в 09 час. 60 мин.", "invalid_appointment_time"),
    ("Назначить судебное заседание на 10.03.2027 в 09:00 или 10:00.", "conflicting_appointment_times"),
    ("Назначить судебное заседание на 10.03.2027 в 09:00–10:00.", "conflicting_appointment_times"),
    ("Назначить судебное заседание на 10.03.2027 в 09:00. Назначить судебное заседание на 10.03.2027 в 10:00.", "conflicting_appointment_times"),
    ("Назначить судебное заседание на 10.03.2027 в 09:00. Назначить судебное заседание на 20.03.2027 в 10:00.", "session_date:conflicting_appointments"),
    ("Назначить судебное заседание на 25.02.207 в 09:00.", "session_date:invalid_appointment_date"),
    ("Назначить судебное заседание на 10.03.2026 в 09:00.", "session_date:appointment_not_after_act"),
])
def test_review_on_invalid_or_conflicting_dates_and_times(body, problem):
    result = extract(body)
    assert result.selected is None
    assert result.problem == problem


def test_postponement_selects_only_new_time():
    result = extract("Назначить судебное заседание на 10.03.2027 в 09:00. Перенести судебное заседание на 20.03.2027 в 10:00.")
    assert result.selected.value == "10:00"
    assert result.selected.session_date == "20.03.2027"


def test_postponement_does_not_reuse_old_time():
    result = extract("Назначить судебное заседание на 10.03.2027 в 09:00. Перенести судебное заседание на 20.03.2027.")
    assert result.selected is None
    assert result.problem == "no_explicit_time_for_appointment"


def test_duplicate_same_time_is_unambiguous():
    result = extract("Назначить судебное заседание на 10.03.2027 в 09:00. Назначить судебное заседание на 10.03.2027 в 09:00.")
    assert result.selected.value == "09:00"


def test_cancelled_appointment_has_no_selected_time():
    result = extract("Назначить судебное заседание на 10.03.2027 в 09:00. Отменить ранее назначенное судебное заседание.")
    assert result.selected is None


def test_prior_narrative_time_does_not_supply_current_date():
    text = "РЕШЕНИЕ\n01 октября 2026 года\nОпределением назначено заседание на 01.12.2026 в 09:00.\nРЕШИЛ:\nНазначить судебное заседание на 10.03.2027."
    assert extract_next_session_time_candidates(text).selected is None


def test_current_date_is_not_used():
    text = HEADER.replace("2026", "2016") + "Назначить судебное заседание на 10.03.2017 в 09:00."
    assert extract_next_session_time_candidates(text).selected.value == "09:00"

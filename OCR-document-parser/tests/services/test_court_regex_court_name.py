"""Court-name candidates must identify this act and retain literal evidence."""
import pytest

from ocr_platform.services.court_regex_court_name import extract_court_name_candidates


@pytest.mark.parametrize("heading, expected", [
    ("АРБИТРАЖНЫЙ СУД МОСКОВСКОЙ ОБЛАСТИ", "Арбитражный суд Московской области"),
    ("Арбитражный\nсуд\nПермского края", "Арбитражный суд Пермского края"),
    ("АРБИТРАЖНЫЙ СУД\nРЕСПУБЛИКИ ТАТАРСТАН", "Арбитражный суд Республики Татарстан"),
    ("Арбитражный суд Республики Саха (Якутия)", "Арбитражный суд Республики Саха (Якутия)"),
    ("Арбитражный суд Республики Северная Осетия-Алания",
     "Арбитражный суд Республики Северная Осетия-Алания"),
    ("Арбитражный суд города Санкт-Петербурга и\nЛенинградской области",
     "Арбитражный суд города Санкт-Петербурга и Ленинградской области"),
    ("Арбитражный суд\nХанты-Мансийского автономного округа – Югры",
     "Арбитражный суд Ханты-Мансийского автономного округа – Югры"),
    ("А Р Б И Т Р А Ж Н Ы Й С У Д Т В Е Р С К О Й О Б Л А С Т И",
     "Арбитражный суд Тверской области"),
    ("А Р Б И Т Р А Ж Н Ы Й\nС У Д\nА Л Т А Й С К О Г О К Р А Я",
     "Арбитражный суд Алтайского края"),
    ("А Р Б И Т Р А Ж Н Ы Й С У Д Р Е С П У Б Л И К И Т А Т А Р С Т А Н",
     "Арбитражный суд Республики Татарстан"),
    ("**АРБИТРАЖНЫЙ СУД АРХАНГЕЛЬСКОЙ ОБЛАСТИ**",
     "Арбитражный суд Архангельской области"),
])
def test_letterhead(heading, expected):
    text = f"123_456\n{heading}\nРЕШЕНИЕ\nУСТАНОВИЛ:\nИные сведения."
    result = extract_court_name_candidates(text)
    assert result.problem is None
    assert result.selected.value == expected
    for candidate in result.candidates:
        assert text[candidate.start:candidate.end] == candidate.quote
        assert candidate.requires_confirmation is True


def test_current_court_before_appeal_and_history():
    text = (
        "Арбитражный суд Московской области\nРЕШЕНИЕ\n"
        "Арбитражный суд Московской области в составе судьи Иванова И.И.\n"
        "УСТАНОВИЛ:\nРешением Арбитражного суда Пермского края признан долг.\n"
        "РЕШИЛ:\nРешение может быть обжаловано в Десятый арбитражный апелляционный суд."
    )
    result = extract_court_name_candidates(text)
    assert result.selected.value == "Арбитражный суд Московской области"
    assert len(result.candidates) == 2


@pytest.mark.parametrize("text", [
    "", "Дело №А60-123/2026\nг. Екатеринбург\nарбитражный суд\nРЕШИЛ:\nПризнать банкротом.",
    "РЕШЕНИЕ\nУСТАНОВИЛ:\nАрбитражный суд Московской области\nпризнал ранее долг.",
    "РЕШИЛ:\nОбжаловать через Арбитражный суд Московской области.",
    "Решением Арбитражный суд Московской области в составе судьи ранее рассмотрел спор.",
    "Дело может быть передано в Арбитражный суд Московской области\nРЕШЕНИЕ",
])
def test_absence_or_historical_reference_has_no_candidate(text):
    result = extract_court_name_candidates(text)
    assert result.selected is None
    assert result.candidates == ()


def test_conflicting_headings_require_review():
    result = extract_court_name_candidates(
        "Арбитражный суд Пермского края\nАрбитражный суд Московской области\nРЕШЕНИЕ"
    )
    assert result.selected is None
    assert result.problem == "conflicting_header_courts"
    assert len(result.candidates) == 2


def test_missing_letterhead_but_current_court_rules():
    text = (
        "РЕЗОЛЮТИВНАЯ ЧАСТЬ РЕШЕНИЯ\nРуководствуясь статьями 167, 223 АПК РФ, "
        "Арбитражный суд Красноярского края\nРЕШИЛ:\nПризнать банкротом."
    )
    result = extract_court_name_candidates(text)
    assert result.selected.value == "Арбитражный суд Красноярского края"
    assert result.selected.quote == text[result.selected.start:result.selected.end]


def test_preserves_spelling_from_introductory_repeat():
    text = (
        "АРБИТРАЖНЫЙ СУД ЧУВАШСКОЙ РЕСПУБЛИКИ-ЧУВАШИИ\nРЕШЕНИЕ\n"
        "Арбитражный суд Чувашской Республики – Чувашии в составе судьи Иванова И.И.\n"
        "УСТАНОВИЛ:\nРанее рассмотренный спор."
    )
    result = extract_court_name_candidates(text)
    assert result.selected.value == "Арбитражный суд Чувашской Республики – Чувашии"
    assert len(result.candidates) == 2

"""Explicit separate announcement dates, calendar validation and literal evidence."""
from dataclasses import asdict

import pytest

from ocr_platform.services.court_decision_dates import extract_decision_date_candidates
from ocr_platform.services.court_regex_resolutive_part_date import (
    extract_resolutive_part_date_candidates as extract,
)


@pytest.mark.parametrize("label", [
    "Резолютивная часть решения объявлена",
    "Резолютивная часть решения суда оглашена",
    "Резолютивная часть определения вынесена в судебном заседании",
    "Резолютивная часть решения принята",
    "Резолютивная часть составлена",
    "Дата объявления резолютивной части –",
    "Дата объявления резолютивной части определения суда:",
    "Дата оглашения резолютивной части решения",
    "Резолютивная часть",
])
@pytest.mark.parametrize("day", ["01.10.2026", "1 . 10 . 2026", "«01» октября 2026 года",
                                "1 ОКТЯБРЯ 2026г."])
def test_explicit_dates_and_literal_evidence(label, day):
    text = (f"РЕШЕНИЕ\n05 октября 2026 года\n{label} {day}\n"
            "Полный текст решения изготовлен 05.10.2026\nСуд в составе судьи")
    result = extract(text)
    assert result.selected.value == "01.10.2026"
    assert result.problem == ""
    for candidate in result.candidates:
        assert candidate.quote == text[candidate.start:candidate.end]
        assert candidate.requires_confirmation is True
        assert "confidence" not in asdict(candidate)


@pytest.mark.parametrize("text", [
    "",
    "РЕШЕНИЕ\n05 октября 2026 года\nСуд в составе судьи",
    "РЕШЕНИЕ\nПолный текст решения изготовлен 05.10.2026\nСуд в составе судьи",
    "РЕШЕНИЕ\n05.10.2026\nСуд в составе судьи\n"
    "Резолютивная часть решения объявлена 01.10.2025",
    "РЕШЕНИЕ\nУ С Т А Н О В И Л:\n"
    "Резолютивная часть решения объявлена 01.10.2025",
    "РЕШЕНИЕ\nРешением суда от 01.10.2025 должник признан банкротом.\n"
    "Резолютивная часть решения объявлена 01.10.2025",
    "РЕШЕНИЕ\nРанее вынесен иной акт.\nРезолютивная часть объявлена 01.10.2025",
    "РЕШЕНИЕ\nЭлектронная подпись действительна\n"
    "Резолютивная часть объявлена 01.10.2025\nКому выдана подпись",
    "РЕШЕНИЕ\n05.10.2026\nРешением суда резолютивная часть объявлена 01.10.2025",
])
def test_abstains_without_separate_current_act_date(text):
    result = extract(text)
    assert result.selected is None
    assert result.problem == "no_explicit_resolutive_date"


@pytest.mark.parametrize("day", ["31.02.2026", "29.02.2025", "00.10.2026", "01.13.2026",
                                "01.10.26", "1 октября 26", "01.10.2026abc"])
def test_invalid_calendar_and_malformed_year(day):
    result = extract(f"РЕШЕНИЕ\nРезолютивная часть объявлена {day}\nСуд в составе судьи")
    assert result.selected is None
    assert result.problem == "invalid_resolutive_date"


def test_multiline_label_nbsp_leap_day():
    text = "РЕШЕНИЕ\nРезолютивная часть решения объявлена\n«29» февраля 2024 года\nСуд в составе"
    text = text.replace(" ", "\u00a0")
    candidate = extract(text).selected
    assert candidate.value == "29.02.2024"
    assert candidate.quote == text[candidate.start:candidate.end]
    assert "объявлена" in candidate.quote


@pytest.mark.parametrize("tail,problem", [
    ("Резолютивная часть оглашена 02.10.2026", "conflicting_resolutive_dates"),
    ("Резолютивная часть оглашена 31.02.2026", "invalid_resolutive_date"),
    ("Полный текст решения изготовлен 30.09.2026", "conflicting_resolutive_date"),
    ("Полный текст решения изготовлен 31.02.2026", "invalid_full_text_date"),
    ("Полный текст решения изготовлен 05.10.2026\n"
     "Полный текст решения изготовлен 06.10.2026", "conflicting_full_text_dates"),
])
def test_conflicts_and_calendar_errors_abstain(tail, problem):
    result = extract("РЕШЕНИЕ\nРезолютивная часть объявлена 01.10.2026\n" + tail)
    assert result.selected is None
    assert result.problem == problem


def test_two_dates_on_one_annotation_abstain():
    result = extract("РЕШЕНИЕ\nРезолютивная часть объявлена 01.10.2026 или 02.10.2026")
    assert result.selected is None
    assert result.problem == "conflicting_resolutive_dates"
    assert {candidate.value for candidate in result.candidates} == {"01.10.2026", "02.10.2026"}


@pytest.mark.parametrize("separator", ["; ", ". ", "\n"])
def test_conflicting_annotations_in_one_line(separator):
    result = extract("РЕШЕНИЕ\nРезолютивная часть объявлена 01.10.2026" + separator
                     + "Резолютивная часть оглашена 02.10.2026")
    assert result.selected is None
    assert result.problem == "conflicting_resolutive_dates"


def test_duplicate_equal_dates_are_consistent():
    result = extract("РЕШЕНИЕ\nРезолютивная часть объявлена 01.10.2026\n"
                     "Резолютивная часть оглашена 1 октября 2026 года")
    assert result.selected.value == "01.10.2026"


def test_common_decision_date_still_selects_full_text():
    text = ("РЕШЕНИЕ\n05 октября 2026 года\nРезолютивная часть объявлена 01.10.2026\n"
            "Полный текст решения изготовлен 05.10.2026\nСуд в составе судьи")
    assert extract(text).selected.value == "01.10.2026"
    assert extract_decision_date_candidates(text).selected.value == "05.10.2026"


def test_header_limit_excludes_late_label():
    text = "РЕШЕНИЕ\n" + "текст\n" * 800 + "Резолютивная часть объявлена 01.10.2026"
    assert extract(text).selected is None


@pytest.mark.parametrize("title", [
    "РЕЗОЛЮТИВНАЯ ЧАСТЬ РЕШЕНИЯ",
    "РЕЗОЛЮТИВНАЯ\nЧАСТЬ ОПРЕДЕЛЕНИЯ",
    "РЕШЕНИЕ\n(резолютивная часть)",
    "Р Е Ш Е Н И Е\nрезолютивная часть",
    "РЕШЕНИЕ\nо признании гражданина банкротом\n(резолютивная часть)",
    "Резолютивная часть\n**РЕШЕНИЯ**",
    "РЕЗОЛЮТИВНАЯ ЧАСТЬ\nопределение по делу №А09-123/2026",
])
@pytest.mark.parametrize("gap", ["\n", "\nг. Иркутск\n", "\nпо делу № А33-123/2026\nг. Красноярск\n"])
def test_standalone_resolutive_title_with_unambiguous_current_date(title, gap):
    text = ("123/2026-123456(1)\nАРБИТРАЖНЫЙ СУД ОБЛАСТИ\n" + title + gap
            + "«22» сентября 2026 года\nРуководствуясь законом\nР Е Ш И Л:")
    candidate = extract(text).selected
    assert candidate.value == "22.09.2026"
    assert candidate.quote == text[candidate.start:candidate.end]
    assert "резолютивная" in candidate.quote.casefold()
    assert candidate.requires_confirmation


def test_standalone_repeated_equal_date_and_technical_date_are_not_conflicts():
    text = ("РЕЗОЛЮТИВНАЯ ЧАСТЬ РЕШЕНИЯ\n22 сентября 2026 года\n"
            "по делу № А33-123/2026\nКрасноярск\n22 сентября 2026 года\n"
            "Руководствуясь законом\nРЕШИЛ:\nДата публикации 23.09.2026")
    assert extract(text).selected.value == "22.09.2026"


@pytest.mark.parametrize("text", [
    "РЕШЕНИЕ\n24.09.2026\nСуд в составе судьи\nУСТАНОВИЛ:\n"
    "Цитата:\nРЕЗОЛЮТИВНАЯ ЧАСТЬ РЕШЕНИЯ\n22.09.2026\nРЕШИЛ:",
    "РЕШЕНИЕ\nЦитата из другого акта:\nРЕЗОЛЮТИВНАЯ ЧАСТЬ РЕШЕНИЯ\n22.09.2026",
    "Из решения другого суда:\nРЕЗОЛЮТИВНАЯ ЧАСТЬ РЕШЕНИЯ\n22.09.2026",
    "Ранее вынесен иной акт\nРЕЗОЛЮТИВНАЯ ЧАСТЬ РЕШЕНИЯ\n22.09.2026",
    "РЕЗОЛЮТИВНАЯ ЧАСТЬ РЕШЕНИЯ\n22.09.2026\n23.09.2026\nРЕШИЛ:",
    "РЕЗОЛЮТИВНАЯ ЧАСТЬ РЕШЕНИЯ\n31.02.2026\nРЕШИЛ:",
    "РЕЗОЛЮТИВНАЯ ЧАСТЬ РЕШЕНИЯ\nЭлектронная подпись\nДата 22.09.2026 12:30\n"
    "Кому выдана подпись\nРЕШИЛ:",
    "РЕЗОЛЮТИВНАЯ ЧАСТЬ РЕШЕНИЯ\nПодписано 22.09.2026\nРЕШИЛ:",
    "РЕЗОЛЮТИВНАЯ ЧАСТЬ РЕШЕНИЯ\nДата публикации 22.09.2026\nРЕШИЛ:",
    "РЕЗОЛЮТИВНАЯ ЧАСТЬ РЕШЕНИЯ\nДата рождения 22.09.1980\nРЕШИЛ:",
    "22.09.2026\nРЕЗОЛЮТИВНАЯ ЧАСТЬ РЕШЕНИЯ\n23.09.2026\nРЕШИЛ:",
    "РЕЗОЛЮТИВНАЯ ЧАСТЬ\n22.09.2026\nРЕШИЛ:",
])
def test_standalone_title_abstains_for_wrong_roles_and_ambiguous_dates(text):
    assert extract(text).selected is None


@pytest.mark.parametrize("prefix,expected", [
    ("РЕШЕНИЕ\n22.09.2026", "22.09.2026"),
    ("РЕШЕНИЕ\n22.09.2026\nПолный текст решения изготовлен 24.09.2026", "24.09.2026"),
    ("РЕЗОЛЮТИВНАЯ ЧАСТЬ РЕШЕНИЯ\nПолный текст решения изготовлен 24.09.2026", "24.09.2026"),
    ("РЕЗОЛЮТИВНАЯ ЧАСТЬ РЕШЕНИЯ\n22.09.2026", "22.09.2026"),
    ("РЕЗОЛЮТИВНАЯ ЧАСТЬ РЕШЕНИЯ\n" + " " * 351 + "22.09.2026", "22.09.2026"),
])
@pytest.mark.parametrize("heading", ["РЕШИЛ:", "Р Е Ш И Л:", "РЕШИЛ", "**Р Е Ш И Л:**"])
def test_full_act_header_does_not_fill_missing_resolutive_date(prefix, expected, heading):
    text = prefix + "\nСуд в составе судьи\nУСТАНОВИЛ:\nОбстоятельства дела\n" + heading + "\nПризнать банкротом."
    assert extract(text).selected is None


@pytest.mark.parametrize("body", [
    "Суд решил исследовать документы.",
    "Заявитель просит суд РЕШИЛ: признать банкротом.",
    "УСТАНОВИЛ:\nТолько мотивировочная часть.",
    "Цитата:\nРЕШИЛ:\nПризнать банкротом.",
    "РЕШИЛ:\nПервый акт\nОПРЕДЕЛИЛ:\nВторой акт",
    "ОПРЕДЕЛИЛ:\nВвести процедуру.",
    "О П Р Е Д Е Л И Л :\nВвести процедуру.",
    "ПОСТАНОВИЛ:\nВвести процедуру.",
])
def test_user_policy_requires_current_unambiguous_operative_heading(body):
    assert extract("РЕШЕНИЕ\n22.09.2026\nСуд в составе судьи\n" + body).selected is None


@pytest.mark.parametrize("header", [
    "РЕШЕНИЕ\n22.09.2026\n23.09.2026",
    "РЕШЕНИЕ\n31.02.2026",
    "РЕШЕНИЕ\nДата публикации 22.09.2026",
    "РЕШЕНИЕ\nРезолютивная часть объявлена 31.02.2026\nПолный текст решения изготовлен 24.09.2026",
    "РЕШЕНИЕ\nРезолютивная часть объявлена 22.09.2026\nРезолютивная часть объявлена 23.09.2026",
])
def test_user_policy_does_not_bypass_missing_invalid_or_conflicting_dates(header):
    assert extract(header + "\nСуд в составе судьи\nРЕШИЛ:").selected is None


def test_explicit_resolutive_date_overrides_current_act_convention():
    text = ("РЕШЕНИЕ\n24.09.2026\nРезолютивная часть объявлена 22.09.2026\n"
            "Полный текст решения изготовлен 24.09.2026\nСуд в составе судьи\nРЕШИЛ:")
    assert extract(text).selected.value == "22.09.2026"
    assert extract_decision_date_candidates(text).selected.value == "24.09.2026"


def test_quoted_complete_foreign_act_is_not_current_decision():
    text = "Из решения другого суда:\nРЕШЕНИЕ\n24.09.2026\nСуд в составе судьи\nРЕШИЛ:"
    assert extract(text).selected is None


def test_registry_does_not_substitute_current_act_date():
    from ocr_platform.services.court_regex_candidates import extract_court_candidates

    text = "РЕШЕНИЕ\n24.09.2026\nСуд в составе судьи\nРЕШИЛ:\nПризнать банкротом."
    result = extract_court_candidates(text, ["decision_date", "resolutive_part_date"])
    assert "resolutive_part_date" not in result.fields
    assert result.problems["resolutive_part_date"] == "no_explicit_resolutive_date"

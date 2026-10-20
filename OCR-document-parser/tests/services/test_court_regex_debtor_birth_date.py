"""Debtor identity, calendar correctness, source offsets and refusal behavior."""

import pytest

from ocr_platform.services.court_regex_debtor_birth_date import (
    extract_debtor_birth_date_candidates as extract,
)


@pytest.mark.parametrize("identity", [
    "Иванова Ивана Ивановича (дата рождения: 01.02.1980)",
    "Иванова Ивана Ивановича (01.02.1980 года рождения)",
    "Иванова Ивана Ивановича, 01.02.1980 г.р.",
    "Иванова Ивана Ивановича (дата рожд. - 01.02.1980)",
    "Иванова Ивана Ивановича (родился 01.02.1980)",
    "Иванова Ивана Ивановича (род. 01.02.1980)",
    "Иванова Ивана Ивановича (г.р. 01.02.1980)",
    "Иванова Ивана Ивановича (ИНН 123456789012; дата рождения: 01.02.1980)",
    "Иванова Ивана Ивановича (1.2.1980 года рождения)",
    "Иванова Ивана Ивановича (01 февраля\n1980 года рождения)",
    "Иванова Ивана Ивановича (01.02.1980, ИНН 123456789012)",
    "Иванова Ивана Ивановича, 01.02.1980, СНИЛС 123-456-789 00",
    "Байларова Исы Салман Оглы (01.02.1980 г.р.)",
])
def test_source_bound_debtor_identity(identity):
    text = "Начало\nПризнать " + identity + " банкротом."
    result = extract(text)
    assert result.selected.value == "01.02.1980"
    assert result.problem is None
    for candidate in result.candidates:
        assert candidate.quote == text[candidate.start:candidate.end]
        assert candidate.requires_confirmation is True


@pytest.mark.parametrize("date", ["31.02.1980", "29.02.1900", "32.01.1980",
                                  "00.01.1980", "01.13.1980", "01.01.0000",
                                  "01.02.80", "01.02.980", "01.02.19800"])
def test_invalid_or_incomplete_date_never_repaired(date):
    result = extract(f"Признать Иванова Ивана Ивановича ({date} года рождения) банкротом.")
    assert result.selected is None
    assert result.problem == "invalid_or_incomplete_debtor_birth_date"


def test_gregorian_leap_day():
    result = extract("Признать Иванова Ивана Ивановича (29.02.2000 г.р.) банкротом.")
    assert result.selected.value == "29.02.2000"


@pytest.mark.parametrize("role", ["финансовый управляющий", "супруга должника",
                                  "сын должника", "дочь должника", "кредитор",
                                  "представитель должника", "судья"])
def test_other_people_are_not_debtor(role):
    text = ("Заявление Иванова Ивана Ивановича о банкротстве. "
            f"{role} Петров Петр Петрович (01.02.1980 г.р.).")
    assert extract(text).selected is None


def test_family_dates_do_not_conflict():
    text = ("Заявление Ивановой Анны Ивановны (01.02.1980 г.р.) о банкротстве. "
            "Супруг должника Петров Петр Петрович (03.04.1979 г.р.). "
            "Дочь должника Петрова Анна Петровна (05.06.2010 г.р.). "
            "Истребовать сведения в отношении Петрова Андрея Петровича (07.08.2012 г.р.).")
    result = extract(text)
    assert result.selected.value == "01.02.1980"
    assert {c.value for c in result.candidates} == {"01.02.1980"}


@pytest.mark.parametrize("other_date,problem", [
    ("03.04.1980", "conflicting_debtor_birth_dates"),
    ("31.02.1980", "invalid_or_incomplete_debtor_birth_date"),
    ("01.02.80", "invalid_or_incomplete_debtor_birth_date"),
])
def test_conflicting_repeated_debtor_date_abstains(other_date, problem):
    result = extract("Заявление Иванова Ивана Ивановича (01.02.1980 г.р.) о банкротстве. "
                     f"Иванова Ивана Ивановича ({other_date} г.р.).")
    assert result.selected is None
    assert result.problem == problem


def test_different_debtors_in_header_and_resolution_abstain():
    result = extract("Заявление Иванова Ивана Ивановича (01.02.1980 г.р.). "
                     "Признать Петрова Петра Петровича (03.04.1981 г.р.) банкротом.")
    assert result.selected is None
    assert result.problem == "conflicting_debtor_birth_dates"


def test_no_source_date_is_not_inferred_from_inn_or_hearing_date():
    assert extract("РЕШЕНИЕ 01.02.2026. Признать Иванова Ивана Ивановича "
                   "(ИНН 123456789012, СНИЛС 123-456-789 00) банкротом.").selected is None


def test_date_of_unidentified_person_abstains():
    assert extract("Петров Петр Петрович, дата рождения: 01.02.1980.").selected is None


def test_debtor_without_full_name():
    result = extract("Должник родился 01.02.1980.")
    assert result.selected.value == "01.02.1980"


def test_unnamed_manager_after_debtor_is_not_bound_to_debtor():
    result = extract("Заявление Иванова Ивана Ивановича о банкротстве. "
                     "Финансовый управляющий родился 01.02.1980.")
    assert result.selected is None


@pytest.mark.parametrize("child", ["ребёнок", "ребенок", "ребёнка"])
def test_unnamed_child_birth_does_not_inherit_preceding_debtor_name(child):
    result = extract("Заявление Ивановой Анны Ивановны (01.02.1980 г.р.) "
                     f"о банкротстве. Её {child} родился 05.06.2010.")
    assert result.selected.value == "01.02.1980"
    assert {candidate.value for candidate in result.candidates} == {"01.02.1980"}


@pytest.mark.parametrize("role", ["Ребёнок", "Дочь", "Сын", "Представитель"])
def test_unnamed_relative_of_debtor_is_not_the_debtor(role):
    result = extract(f"{role} должника родился 05.06.2010.")
    assert result.selected is None


def test_unnamed_child_without_source_debtor_birth_date_abstains():
    result = extract("Заявление Ивановой Анны Ивановны о банкротстве. "
                     "Ребёнок родился 05.06.2010.")
    assert result.selected is None


@pytest.mark.parametrize("current_person,expected_selected", [
    ("Иванову Анну Ивановну", True), ("Петрову Елену Петровну", False),
])
def test_historical_foreign_identity_needs_agreeing_current_anchors(current_person, expected_selected):
    text = (
        "Суд рассмотрел заявление Ивановой Анны Ивановны "
        "(01.02.1980 г.р., ИНН: 123456789012, СНИЛС: 123-456-789 01) "
        "о признании несостоятельной (банкротом). УСТАНОВИЛ: "
        "Определением суда от 01.01.2025 принято к производству заявление "
        "должника-гражданина Петровой Елены Петровны "
        "(11.03.1978 г.р., ИНН: 987654321098, СНИЛС: 987-654-321 98). "
        f"РЕШИЛ: Признать должника {current_person} банкротом."
    )
    result = extract(text)
    if expected_selected:
        assert result.selected.value == '01.02.1980'
        assert text[result.selected.start:result.selected.end] == result.selected.quote
    else:
        assert result.selected is None

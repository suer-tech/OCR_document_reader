"""Role, literal evidence and ambiguity checks for debtor INN proposals."""

import pytest

from ocr_platform.services.court_regex_debtor_inn import extract_debtor_inn_candidates


def test_person_preserves_digits_leading_zero_and_original_offsets():
    text = "Шапка\nПризнать Иванова Ивана Ивановича (ИНН: 026 101\n744 251) банкротом."
    result = extract_debtor_inn_candidates(text)
    assert result.problem is None
    assert result.selected.value == "026101744251"
    assert result.selected.requires_confirmation is True
    assert text[result.selected.start:result.selected.end] == result.selected.quote
    assert "026 101\n744 251" in result.selected.quote


@pytest.mark.parametrize("label", ["ИНН", "И Н Н", "идентификационный номер налогоплательщика"])
def test_explicit_label_variants(label):
    result = extract_debtor_inn_candidates(f"Должник Иванов Иван Иванович ({label} 012345678901).")
    assert result.selected.value == "012345678901"


@pytest.mark.parametrize("number", [
    "0123456789", "01234567890", "0123456789012", "０１２３４５６７８９０１",
    "012345678901O", "012345678901-2",
])
def test_no_person_truncation_digit_repair_or_ten_digit_person(number):
    result = extract_debtor_inn_candidates(f"Признать Иванова Ивана Ивановича (ИНН {number}) банкротом.")
    assert result.selected is None
    assert result.candidates == []


def test_ten_digits_require_legal_entity_role():
    result = extract_debtor_inn_candidates('Признать ООО «Ромашка» (ИНН 0123456789) банкротом.')
    assert result.selected.value == "0123456789"


def test_autonomous_district_is_not_a_corporate_role():
    result = extract_debtor_inn_candidates(
        "Признать Иванова (место рождения Ханты-Мансийский АО, ИНН 012345678901) банкротом."
    )
    assert result.selected.value == "012345678901"


@pytest.mark.parametrize("role", [
    "финансовым управляющим должника", "финансовым управляющим имуществом должника",
    "финансовым управляющим гражданина-должника", "арбитражного управляющего",
    "кредитора", "представителя должника", "супруги должника", "САУ «Союз»",
])
def test_other_roles_are_not_debtor_candidates(role):
    result = extract_debtor_inn_candidates(
        f"Признать Иванова (ИНН 012345678901) банкротом.\n"
        f"Утвердить {role} Петрова (ИНН 123456789012)."
    )
    assert {candidate.value for candidate in result.candidates} == {"012345678901"}


def test_creditor_application_cannot_supply_debtor_inn():
    result = extract_debtor_inn_candidates(
        'Заявление ООО «Банк» (ИНН 0123456789) о признании Иванова банкротом.'
    )
    assert result.selected is None


def test_repeated_value_agrees_but_conflicting_debtors_abstain():
    first = "Признать Иванова (ИНН 012345678901) банкротом.\n"
    result = extract_debtor_inn_candidates(first + first)
    assert len(result.candidates) == 2
    assert result.selected.value == "012345678901"
    result = extract_debtor_inn_candidates(first + "Должник Петров (ИНН 123456789012).")
    assert result.selected is None
    assert result.problem == "ambiguous_debtor_inn"
    assert len(result.candidates) == 2


@pytest.mark.parametrize("text", ["", "ИНН 012345678901", "Должник Иванов 012345678901"])
def test_missing_role_or_label_abstains(text):
    result = extract_debtor_inn_candidates(text)
    assert result.selected is None
    assert result.problem == "debtor_inn_not_found"


def test_court_narrative_cannot_leak_manager_inn_from_bankruptcy_law_title():
    result = extract_debtor_inn_candidates(
        "Согласно Закону о банкротстве, суд приходит к выводу, что Петров "
        "(ИНН 012345678901) подлежит утверждению финансовым управляющим."
    )
    assert result.selected is None


@pytest.mark.parametrize("relative", [
    "сына должника", "дочери должника", "ребенка должника", "детей должника",
    "его сына", "его дочери", "его ребёнка",
])
def test_family_member_after_broad_in_relation_to_is_not_a_debtor(relative):
    text = (
        "Признать Иванова Ивана Ивановича (ИНН 012345678901) банкротом. "
        f"Предоставить информацию в отношении {relative} Петрова (ИНН 123456789012)."
    )
    result = extract_debtor_inn_candidates(text)
    assert result.selected.value == "012345678901"
    assert {candidate.value for candidate in result.candidates} == {"012345678901"}
    assert text[result.selected.start:result.selected.end] == result.selected.quote


def test_minor_debtor_is_not_confused_with_a_family_role():
    result = extract_debtor_inn_candidates(
        "В отношении несовершеннолетнего должника Иванова Ивана Ивановича "
        "(ИНН 012345678901) введена процедура."
    )
    assert result.selected.value == "012345678901"


def test_family_member_does_not_hide_a_later_actual_conflicting_debtor():
    result = extract_debtor_inn_candidates(
        "Должник Иванов (ИНН 012345678901). "
        "Предоставить сведения в отношении сына должника Петрова (ИНН 123456789012). "
        "Признать Сидорова (ИНН 987654321098) банкротом."
    )
    assert result.selected is None
    assert result.problem == "ambiguous_debtor_inn"
    assert {candidate.value for candidate in result.candidates} == {"012345678901", "987654321098"}


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
    result = extract_debtor_inn_candidates(text)
    if expected_selected:
        assert result.selected.value == '123456789012'
        assert text[result.selected.start:result.selected.end] == result.selected.quote
    else:
        assert result.selected is None

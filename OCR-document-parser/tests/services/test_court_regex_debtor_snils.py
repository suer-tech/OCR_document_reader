import re

import pytest

from ocr_platform.services.court_regex_debtor_snils import (
    DebtorSnilsResult,
    extract_debtor_snils_candidates,
)


@pytest.mark.parametrize("raw", [
    "11223344595", "112-233-445 95", "112-233-445-95",
    "112 233 445 95", "112–233—445−95", "112-\n233-445\n95",
    "112\u00a0233-445\u00a095",
])
def test_only_separators_are_normalized_and_evidence_is_literal(raw):
    text = f"РЕШИЛ: Признать Иванова Ивана Ивановича (СНИЛС № {raw}) банкротом."
    result = extract_debtor_snils_candidates(text)
    assert isinstance(result, DebtorSnilsResult)
    assert result.problem is None
    assert result.selected.value == "112-233-445 95"
    for candidate in result.candidates:
        assert candidate.requires_confirmation is True
        assert text[candidate.start:candidate.end] == candidate.quote
        assert raw in candidate.quote
        assert re.sub(r"[^0-9]", "", raw) == re.sub(r"[^0-9]", "", candidate.value)


@pytest.mark.parametrize("label", [
    "СНИЛС", "снилс:", "СНИЛС№", "СНИЛС", "страховой номер индивидуального лицевого счета",
    "страхового номера\nиндивидуального лицевого счёта –",
    "номер индивидуального лицевого счета в системе обязательного пенсионного страхования",
    "страховой номер индивидуального лицевого\nсчета (СНИЛС):",
])
def test_labels(label):
    result = extract_debtor_snils_candidates(
        f"Заявление должника Иванова И.И. ({label}11223344595) о признании банкротом."
    )
    assert result.selected.value == "112-233-445 95"


@pytest.mark.parametrize("number", [
    "079-158-968 270", "112233445950", "112-233-445 9", "112-233-445 95-0",
    "112-233-44О 95", "112-233-445\n7 123456_789012\n95",
])
def test_no_truncation_ocr_digit_repair_or_page_artifact_removal(number):
    result = extract_debtor_snils_candidates(f"Признать должника Иванова (СНИЛС {number}) банкротом.")
    assert result.selected is None
    assert not result.candidates
    assert result.problem == "not_found"


@pytest.mark.parametrize("other_role", [
    "финансового управляющего", "арбитражного управляющего", "кредитора",
    "представителя кредитора", "супруга", "поручителя",
])
def test_excludes_other_owners(other_role):
    text = (
        "Заявление должника Иванова (СНИЛС 112-233-445 95) о признании банкротом. "
        f"Реквизиты {other_role} Петрова П.П. (СНИЛС 123-456-789 01)."
    )
    result = extract_debtor_snils_candidates(text)
    assert result.selected.value == "112-233-445 95"
    assert {candidate.value for candidate in result.candidates} == {"112-233-445 95"}


def test_manager_appointment_does_not_establish_manager_number_as_debtor():
    result = extract_debtor_snils_candidates(
        "Утвердить финансовым управляющим должника Петрова П.П. (СНИЛС 123-456-789 01)."
    )
    assert result.selected is None
    assert not result.candidates


def test_new_debtor_clause_after_other_owner_is_accepted():
    result = extract_debtor_snils_candidates(
        "Финансовый управляющий Петров П.П.\nДолжник Иванов (СНИЛС 112-233-445 95)."
    )
    assert result.selected.value == "112-233-445 95"


def test_distinct_debtor_numbers_abstain_even_if_one_is_repeated_more_often():
    result = extract_debtor_snils_candidates(
        "Заявление должника Иванова (СНИЛС 112-233-445 95).\n"
        "Признать Иванова (СНИЛС 11223344595) банкротом.\n"
        "Признать Петрова (СНИЛС 123-456-789 01) банкротом."
    )
    assert result.selected is None
    assert len(result.candidates) == 3
    assert result.problem == "ambiguous"


def test_repetitions_of_same_number_do_not_conflict():
    result = extract_debtor_snils_candidates(
        "Заявление должника (СНИЛС 112-233-445 95). Признать должника (СНИЛС11223344595) банкротом."
    )
    assert result.selected.value == "112-233-445 95"
    assert len(result.candidates) == 2


def test_isolated_label_retains_unselected_candidate_for_review():
    result = extract_debtor_snils_candidates("СНИЛС 112-233-445 95")
    assert result.selected is None
    assert len(result.candidates) == 1
    assert result.problem == "owner_not_established"


def test_selection_has_debtor_context_when_earlier_occurrence_has_unknown_owner():
    text = "СНИЛС 112-233-445 95\n" + "Приложение. " * 130
    text += "Признать должника Иванова (СНИЛС 11223344595) банкротом."
    result = extract_debtor_snils_candidates(text)
    assert result.selected == result.candidates[-1]


def test_never_repairs_checksum_or_substitutes_a_human_value():
    result = extract_debtor_snils_candidates("Признать должника (СНИЛС 199–772–358 31) банкротом.")
    assert result.selected.value == "199-772-358 31"
    assert result.selected.requires_confirmation is True


def test_unlabeled_eleven_digits_and_inn_are_not_snils():
    result = extract_debtor_snils_candidates("Признать должника Иванова, ИНН 123456789012, телефон 11223344595.")
    assert result.selected is None
    assert result.problem == "not_found"


def test_empty_input():
    result = extract_debtor_snils_candidates("")
    assert result == DebtorSnilsResult(None, (), "not_found")


@pytest.mark.parametrize("relative", [
    "сын", "сына", "дочь", "дочери", "ребёнок", "ребенка", "дети", "детей",
])
def test_relative_of_debtor_is_not_debtor_snils_owner(relative):
    text = (
        "Заявление должника Иванова Ивана Ивановича о признании банкротом. "
        f"На иждивении {relative} должника Петров Петр Петрович "
        "(СНИЛС 123-456-789 01)."
    )
    result = extract_debtor_snils_candidates(text)
    assert result.selected is None
    assert not result.candidates


@pytest.mark.parametrize("procedural_phrase", [
    "подал заявление", "просит признать право на имущество", "в отношении имущества",
])
def test_generic_procedural_words_do_not_reassign_child_number(procedural_phrase):
    text = (
        "Должник Иванов И.И. Сын должника Петров П.П. "
        f"{procedural_phrase} (СНИЛС 123-456-789 01) в деле о банкротстве."
    )
    result = extract_debtor_snils_candidates(text)
    assert result.selected is None
    assert not result.candidates


def test_child_number_does_not_create_conflict_with_actual_debtor_number():
    text = (
        "Признать должника Иванова (СНИЛС 112-233-445 95) банкротом. "
        "Его дочь Иванова Анна Ивановна (СНИЛС 123-456-789 01)."
    )
    result = extract_debtor_snils_candidates(text)
    assert result.selected.value == "112-233-445 95"
    assert {item.value for item in result.candidates} == {"112-233-445 95"}


@pytest.mark.parametrize("debtor_clause", [
    "\nДолжник Иванов", ". Признать должника Иванова",
    ". Заявление должника Иванова", ". В отношении должника Иванова",
])
def test_separate_debtor_identity_after_child_is_not_suppressed(debtor_clause):
    text = (
        "Сын должника Петров (СНИЛС 123-456-789 01)."
        f"{debtor_clause} (СНИЛС 112-233-445 95)."
    )
    result = extract_debtor_snils_candidates(text)
    assert result.selected.value == "112-233-445 95"


def test_age_description_does_not_make_debtor_another_owner():
    result = extract_debtor_snils_candidates(
        "Признать несовершеннолетнего должника Иванова (СНИЛС 112-233-445 95) банкротом."
    )
    assert result.selected.value == "112-233-445 95"


def test_children_in_reasoning_do_not_own_current_operative_identity():
    text = (
        "Закон предусматривает содержание детей должника.\n"
        "РЕШИЛ:\nпризнать заявление обоснованным.\n"
        "Признать Иванова Ивана Ивановича (СНИЛС 112-233-445 95) "
        "несостоятельным (банкротом)."
    )
    result = extract_debtor_snils_candidates(text)
    assert result.selected.value == "112-233-445 95"


def test_child_identity_in_operative_part_is_still_another_owner():
    text = (
        "РЕШИЛ:\nПредоставить средства на содержание сына должника "
        "Петрова (СНИЛС 123-456-789 01) в деле о банкротстве."
    )
    result = extract_debtor_snils_candidates(text)
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
    result = extract_debtor_snils_candidates(text)
    if expected_selected:
        assert result.selected.value == '123-456-789 01'
        assert text[result.selected.start:result.selected.end] == result.selected.quote
    else:
        assert result.selected is None

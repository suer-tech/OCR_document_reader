import pytest

from ocr_platform.services.court_regex_debtor_gender import extract_debtor_gender_candidates


@pytest.mark.parametrize(("text", "expected"), [
    ("Должник: Иванов Иван Иванович.", "Мужской"),
    ("Признать Иванова Ивана Ивановича банкротом.", "Мужской"),
    ("Признать Сидорову Анну Ильиничну банкротом.", "Женский"),
    ("заявление Петровой Марии Ивановны о признании банкротом.", "Женский"),
    ("Должник: Иванов Иса Салман Оглы.", "Мужской"),
    ("Должник: Алиева Алия Рашид Кызы.", "Женский"),
    ("Должник: гражданка Ким Саша, признать банкротом.", "Женский"),
    ("Признать гражданина Ким Саша банкротом.", "Мужской"),
    ("Пол должника: женский.", "Женский"),
    ("Должник родился 01.01.1980 в Москве.", "Мужской"),
    ("Должница родилась 01.01.1980 в Москве.", "Женский"),
    ("Должник: Иванов И.И., родился 01.01.1980.", "Мужской"),
    ("Признать должника-гражданина Иванову Анну Ивановну банкротом.", "Женский"),
])
def test_literal_debtor_evidence(text, expected):
    result = extract_debtor_gender_candidates(text)
    assert result.selected is not None
    assert result.selected.value == expected
    assert result.problem is None
    for candidate in result.candidates:
        assert candidate.quote == text[candidate.start:candidate.end]
        assert candidate.requires_confirmation is True


@pytest.mark.parametrize("text", [
    "", "Должник: Ким Саша.", "Должник: Иванов И.И.",
    "Должник: Иванов Иван. Финансовый управляющий: Петрова Анна Ивановна.",
    "Финансовый управляющий гражданин Петров Петр Петрович родился в Москве.",
    "Утвердить финансовым управляющим должника Петрову Анну Ивановну.",
    "Финансовый управляющий должника: гражданка Петрова Анна Ивановна.",
    "Супруг должника родился 01.01.1980.",
    "Жена должника родилась 01.01.1980.",
    "Сын должника: Иванов Иван Иванович.",
    "Гражданин должен погасить задолженность согласно закону о банкротстве.",
    "При признании должника-гражданина банкротом суд назначает управляющего.",
    "Привлечь к участию в деле о банкротстве гражданина Петрову Анну Ивановну.",
])
def test_no_guess_or_other_person(text):
    result = extract_debtor_gender_candidates(text)
    assert result.selected is None
    assert result.problem == "not_found"


def test_manager_and_spouse_do_not_conflict_with_debtor():
    text = ("Должник: Иванов Иван Иванович. Супруга Иванова Анна Ивановна родилась в Москве. "
            "Утвердить финансовым управляющим Петрову Елену Петровну.")
    result = extract_debtor_gender_candidates(text)
    assert result.selected.value == "Мужской"
    assert {candidate.value for candidate in result.candidates} == {"Мужской"}


def test_lower_ranked_manager_name_is_not_a_gender_candidate():
    text = ("Должник: Иванов Иван Иванович. В деле о банкротстве суд считает возможным "
            "утвердить Багиеву Викторию Александровну финансовым управляющим имуществом должника.")
    result = extract_debtor_gender_candidates(text)
    assert result.selected.value == "Мужской"
    assert {candidate.value for candidate in result.candidates} == {"Мужской"}


@pytest.mark.parametrize("other", ["Петров Петр Петрович", "Петрова Анна Петровна"])
def test_identity_conflict_even_with_same_gender(other):
    result = extract_debtor_gender_candidates(
        f"Должник: Иванов Иван Иванович. Должник: {other}.",
    )
    assert result.selected is None
    assert result.problem == "conflicting_debtor_names"


def test_conflicting_literal_gender_requires_fallback():
    result = extract_debtor_gender_candidates("Должник: Иванов Иван Иванович, родилась в Москве.")
    assert result.selected is None
    assert result.problem == "conflicting_debtor_gender"


def test_unicode_original_offsets_and_multiple_occurrences():
    text = ("😀 Преамбула. Должник: Иванов\nИван Иванович. " + "Текст. " * 80
            + "Признать Иванова Ивана Ивановича банкротом.")
    result = extract_debtor_gender_candidates(text)
    assert result.selected.value == "Мужской"
    assert len(result.candidates) >= 2
    for candidate in result.candidates:
        assert candidate.quote == text[candidate.start:candidate.end]


@pytest.mark.parametrize("birth_clause", [
    "Родилась дочь Иванова Мария Ивановна.",
    "Родился сын Иванов Иван Иванович.",
    "Родилась его дочь Иванова Мария Ивановна.",
    "Родилась ее дочь Иванова Мария Ивановна.",
    "Родился ребенок Иванов Иван Иванович.",
])
def test_birth_verb_with_following_other_person_does_not_describe_debtor(birth_clause):
    result = extract_debtor_gender_candidates("Должник: Иванов И.И. " + birth_clause)
    assert result.selected is None
    assert result.problem == "not_found"


@pytest.mark.parametrize(("debtor", "other", "expected"), [
    ("Иванов Иван Иванович", "Родилась дочь Иванова Мария Ивановна.", "Мужской"),
    ("Иванова Анна Ивановна", "Родился сын Иванов Иван Иванович.", "Женский"),
])
def test_child_birth_does_not_override_debtor_patronymic(debtor, other, expected):
    result = extract_debtor_gender_candidates(f"Должник: {debtor}. {other}")
    assert result.selected is not None
    assert result.selected.value == expected
    assert {candidate.value for candidate in result.candidates} == {expected}


@pytest.mark.parametrize("birth", ["родился", "родилась"])
def test_literal_birth_marker_still_available_without_other_subject(birth):
    text = f"Должник: Иванов И.И., {birth} 01.01.1980 в Москве."
    result = extract_debtor_gender_candidates(text)
    assert result.selected is not None
    assert result.selected.value == ("Мужской" if birth == "родился" else "Женский")
    assert result.selected.quote == text[result.selected.start:result.selected.end]


def test_historical_other_person_gender_does_not_override_current_debtor():
    text = (
        "Суд рассмотрел заявление Ивановой Анны Ивановны о признании банкротом. "
        "УСТАНОВИЛ: Определением суда от 01.01.2025 принято к производству "
        "заявление должника-гражданина Петрова Петра Петровича. "
        "РЕШИЛ: Признать должника Иванову Анну Ивановну банкротом."
    )
    result = extract_debtor_gender_candidates(text)
    assert result.selected is not None
    assert result.selected.value == "Женский"
    assert {candidate.value for candidate in result.candidates} == {"Женский"}


def test_current_conflicting_person_remains_a_conflict():
    text = (
        "Суд рассмотрел заявление Ивановой Анны Ивановны о признании банкротом. "
        "УСТАНОВИЛ: Иванова Анна Ивановна (далее должник) обратилась в суд. "
        "РЕШИЛ: Признать должника Иванову Анну Ивановну банкротом. "
        "Открыть в отношении Петрова Петра Петровича процедуру реализации имущества."
    )
    result = extract_debtor_gender_candidates(text)
    assert result.selected is None
    assert result.problem == "conflicting_debtor_names"


def test_sons_patronymic_does_not_conflict_with_mothers_gender():
    result = extract_debtor_gender_candidates(
        "Должник: Иванова Анна Ивановна. "
        "Сын должника: Иванов Иван Иванович, родился 01.01.2010."
    )
    assert result.selected is not None
    assert result.selected.value == "Женский"
    assert {candidate.value for candidate in result.candidates} == {"Женский"}


@pytest.mark.parametrize("surname", ["Лесного", "Кравченка", "Гордана", "Сила"])
def test_unresolved_surname_does_not_erase_literal_patronymic_gender(surname):
    text = f"Признать {surname} Ивана Ивановича банкротом."
    result = extract_debtor_gender_candidates(text)
    assert result.selected is not None
    assert result.selected.value == "Мужской"
    assert result.selected.quote == text[result.selected.start:result.selected.end]

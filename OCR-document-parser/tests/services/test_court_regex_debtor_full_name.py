import pytest

from ocr_platform.services.court_regex_debtor_full_name import (
    extract_debtor_full_name_candidates,
    ignored_historical_debtor_identity_blocks,
)


def selected(text):
    return extract_debtor_full_name_candidates(text).selected


@pytest.mark.parametrize("text", [
    "Должник: Иванов Иван Иванович (дата рождения 01.01.1980).",
    "Признать Иванова Ивана Ивановича несостоятельным (банкротом).",
    "Иванов Иван Иванович (далее – заявитель, должник) обратился в суд.",
    "заявление ИП Иванова Ивана Ивановича о признании банкротом.",
    "Должник: гр. Иванов Иван Иванович, ИНН 123456789012.",
])
def test_debtor_context_and_regular_case_endings(text):
    result = selected(text)
    assert result is not None
    assert result.value == "Иванов Иван Иванович"
    assert result.requires_confirmation is True
    assert text[result.start:result.end] == result.quote
    assert result.source_value in result.quote


@pytest.mark.parametrize("role", [
    "Судья", "кредитор", "представитель", "секретарь", "помощник судьи",
    "финансовый управляющий", "Утвердить финансовым управляющим должника",
    "Утвердить финансовым управляющим имущества должника",
    "Утвердить финансовым управляющим для участия в процедуре реализации имущества должника",
    "супруг должника", "Привлечь к участию в деле о банкротстве гражданина",
])
def test_other_people_are_not_debtors(role):
    assert selected(f"{role} Петров Петр Петрович.") is None


def test_manager_consent_application_is_not_debtor_application():
    text = (
        "Представлено заявление Петрова Петра Петровича, подтверждающее его согласие "
        "на утверждение в деле о банкротстве. Должник: Иванов Иван Иванович."
    )
    result = extract_debtor_full_name_candidates(text)
    assert result.selected.value == "Иванов Иван Иванович"
    assert {candidate.value for candidate in result.candidates} == {"Иванов Иван Иванович"}


def test_conflicting_debtors_require_semantic_resolution():
    text = "Должник: Иванов Иван Иванович. Признать Петрова Петра Петровича банкротом."
    result = extract_debtor_full_name_candidates(text)
    assert result.selected is None
    assert result.problem == "conflicting_debtor_names"
    assert len(result.candidates) == 2


def test_initials_are_never_expanded():
    result = extract_debtor_full_name_candidates("Должник: Иванов И.И. признан банкротом.")
    assert result.selected.value == "Иванов И.И."
    assert result.problem == "initials_only"


def test_conflicting_initials_are_not_silently_selected():
    result = extract_debtor_full_name_candidates("Должник: Иванов И.И. Должник: Петров П.П.")
    assert result.selected is None
    assert result.problem == "conflicting_debtor_names"


def test_all_repeated_evidence_has_original_offsets():
    text = "Преамбула. Должник: Иванов\nИван Иванович.\nПризнать Иванова Ивана Ивановича банкротом."
    result = extract_debtor_full_name_candidates(text)
    assert len(result.candidates) == 2
    for candidate in result.candidates:
        assert candidate.quote == text[candidate.start:candidate.end]
        assert candidate.source_value in candidate.quote
        assert candidate.requires_confirmation is True


def test_maiden_names_are_preserved_in_source():
    text = "заявление Сидоровой (Петровой, Ивановой) Анны Ивановны о признании банкротом."
    result = selected(text)
    assert result.value == "Сидорова Анна Ивановна"
    assert result.source_value == "Сидоровой (Петровой, Ивановой) Анны Ивановны"
    assert "regular_case_endings" in result.normalization
    assert "omit_parenthesized_previous_surnames" in result.normalization
    assert "unresolved_surname_nominative" in result.normalization


def test_unknown_surname_endings_are_not_guessed():
    result = extract_debtor_full_name_candidates("Признать Мирного Алексея Ивановича банкротом.")
    assert result.selected is None
    assert result.candidates[0].value == "Мирного Алексей Иванович"
    assert result.problem == "ambiguous_nominative"


@pytest.mark.parametrize("name", ["Иса", "Муса", "Мустафа", "Илья", "Никита"])
def test_male_nominative_names_ending_in_vowel_are_preserved(name):
    assert selected(f"Должник: Иванов {name} Иванович.").value == f"Иванов {name} Иванович"


def test_ogly_is_part_of_patronymic_and_does_not_create_shifted_name():
    result = extract_debtor_full_name_candidates("Должник: Иванов Иса Салман Оглы.")
    assert result.selected.value == "Иванов Иса Салман Оглы"
    assert len(result.candidates) == 1


def test_absent_or_unrelated_names_return_problem():
    for text in ("", "Решение суда о банкротстве.", "Петров Петр Петрович присутствовал в зале."):
        result = extract_debtor_full_name_candidates(text)
        assert result.selected is None
        assert result.candidates == []
        assert result.problem == "not_found"


@pytest.mark.parametrize("oblique,nominal", [
    ("Марине", "Марина"), ("Диане", "Диана"), ("Галине", "Галина"),
    ("Зое", "Зоя"), ("Дарье", "Дарья"),
])
def test_female_dative_does_not_create_another_person(oblique, nominal):
    text = (
        f"Должник: Иванова {nominal} Ивановна. "
        f"Ввести в отношении Ивановой {oblique} Ивановне реализацию имущества."
    )
    result = extract_debtor_full_name_candidates(text)
    assert result.problem is None
    assert result.selected.value == f"Иванова {nominal} Ивановна"


@pytest.mark.parametrize("oblique,nominal", [
    ("Саркисяна", "Саркисян"), ("Саркисяну", "Саркисян"),
    ("Гофмана", "Гофман"),
])
def test_productive_masculine_surname_suffixes(oblique, nominal):
    result = selected(f"Признать {oblique} Ивана Ивановича банкротом.")
    assert result.value == f"{nominal} Иван Иванович"


def test_unknown_consonant_surname_is_corroborated_by_written_nominative():
    text = (
        "заявление Козуба Ивана Ивановича о признании банкротом. "
        "Козуб Иван Иванович (далее должник) обратился в суд. "
        "Признать Козуба Ивана Ивановича банкротом."
    )
    result = extract_debtor_full_name_candidates(text)
    assert result.problem is None
    assert result.selected.value == "Козуб Иван Иванович"
    assert result.selected.source_value == "Козуб Иван Иванович"
    assert len(result.candidates) == 3


def test_source_biography_can_corroborate_same_debtor_without_guessing():
    text = (
        "заявление Козуба Ивана Ивановича о признании банкротом. "
        "Козуб Иван Иванович, 01.01.1980 года рождения, зарегистрирован в городе."
    )
    result = extract_debtor_full_name_candidates(text)
    assert result.selected.value == "Козуб Иван Иванович"
    assert result.selected.source_value == "Козуб Иван Иванович"
    assert "заявление Козуба Ивана Ивановича" in result.selected.quote
    assert text[result.selected.start:result.selected.end] == result.selected.quote
    assert "source_nominative_corroboration" in result.selected.normalization


def test_other_persons_biography_does_not_rewrite_the_debtor():
    text = (
        "заявление Козуба Ивана Ивановича о признании банкротом. "
        "Козуб Петр Иванович, 01.01.1980 года рождения, зарегистрирован в городе."
    )
    result = extract_debtor_full_name_candidates(text)
    assert result.selected.value == "Козуба Иван Иванович"
    assert result.problem == "ambiguous_nominative"


def test_adjectival_surname_is_resolved_only_with_written_nominative():
    text = (
        "заявление Лесного Ивана Ивановича о признании банкротом. "
        "Должник: Лесной Иван Иванович."
    )
    result = extract_debtor_full_name_candidates(text)
    assert result.problem is None
    assert result.selected.value == "Лесной Иван Иванович"
    assert selected("заявление Лесного Ивана Ивановича о банкротстве.") is None


def test_unknown_fleeting_vowel_surname_is_not_reconstructed():
    result = extract_debtor_full_name_candidates("заявление Левченка Ивана Ивановича о банкротстве.")
    assert result.selected.value == "Левченка Иван Иванович"
    assert result.problem == "ambiguous_nominative"


def test_ogly_regular_surname_and_first_name_case():
    text = "заявление Мамедова Рашида Рахман Оглы о признании банкротом."
    assert selected(text).value == "Мамедов Рашид Рахман Оглы"


def test_ogly_corresponding_source_mentions_do_not_create_false_conflict():
    text = (
        "Должник: Мамедов Расул Аббас оглы. "
        "Признать Мамедова Расула Аббаса оглы банкротом."
    )
    result = extract_debtor_full_name_candidates(text)
    assert result.problem is None
    assert result.selected.value == "Мамедов Расул Аббас оглы"
    assert result.selected.source_value == "Мамедов Расул Аббас оглы"


@pytest.mark.parametrize("second_name", [
    "Козуб Петр Иванович", "Козуб Иван Петорвич", "Козуб Ивван Иванович",
])
def test_different_people_and_ocr_spellings_are_not_merged(second_name):
    text = f"Должник: Козуб Иван Иванович. Должник: {second_name}."
    result = extract_debtor_full_name_candidates(text)
    assert result.selected is None
    assert result.problem == "conflicting_debtor_names"


@pytest.mark.parametrize("role", [
    "Сын должника", "Дочь должника", "Дети должника", "Ребёнок должника",
    "несовершеннолетних детей",
])
def test_named_children_do_not_become_another_debtor(role):
    text = ("Должник: Иванова Анна Ивановна. "
            f"{role}: Иванов Иван Петрович, родился 01.01.2010.")
    result = extract_debtor_full_name_candidates(text)
    assert result.selected.value == "Иванова Анна Ивановна"
    assert result.problem is None
    assert {item.value for item in result.candidates} == {"Иванова Анна Ивановна"}


def test_historical_other_person_does_not_override_agreeing_current_identity():
    text = (
        "Суд рассмотрел заявление Ивановой Анны Ивановны о признании банкротом. "
        "УСТАНОВИЛ: Определением суда от 01.01.2025 принято к производству "
        "заявление должника-гражданина Петровой Елены Петровны. "
        "Р Е Ш И Л: Признать должника Иванову Анну Ивановну банкротом."
    )
    result = extract_debtor_full_name_candidates(text)
    assert result.selected.value == "Иванова Анна Ивановна"
    assert result.problem is None
    assert "current_identity_anchors" in result.selected.normalization
    assert "Ивановой Анны Ивановны" in result.selected.quote
    assert "Признать должника Иванову Анну Ивановну" in result.selected.quote
    assert "Петрова Елена Петровна" in {item.value for item in result.candidates}
    for item in result.candidates:
        assert text[item.start:item.end] == item.quote


@pytest.mark.parametrize("current_other_action", [
    "Признать Петрову Елену Петровну банкротом.",
    "Открыть в отношении Петровой Елены Петровны процедуру реализации имущества.",
    "Ввести в отношении Петровой Елены Петровны процедуру реализации имущества.",
])
def test_two_current_identities_remain_a_conflict_despite_agreeing_header(current_other_action):
    text = (
        "Суд рассмотрел заявление Ивановой Анны Ивановны о признании банкротом. "
        "УСТАНОВИЛ: Иванова Анна Ивановна (далее должник) обратилась в суд. "
        "РЕШИЛ: Признать должника Иванову Анну Ивановну банкротом. "
        + current_other_action
    )
    result = extract_debtor_full_name_candidates(text)
    assert result.selected is None
    assert result.problem == "conflicting_debtor_names"


@pytest.mark.parametrize("prefix,suffix", [
    ("", "РЕШИЛ: Признать должника Иванову Анну Ивановну банкротом."),
    ("заявление Ивановой Анны Ивановны. ", ""),
])
def test_historical_conflict_needs_both_current_identity_anchors(prefix, suffix):
    text = (prefix + "УСТАНОВИЛ: Определением суда от 01.01.2025 принято к производству "
            "заявление должника-гражданина Петровой Елены Петровны. " + suffix)
    result = extract_debtor_full_name_candidates(text)
    assert result.selected is None
    assert result.problem == "conflicting_debtor_names"


@pytest.mark.parametrize("surname", ["Лесного", "Кравченка", "Гордана", "Сила"])
def test_unresolved_reverse_surname_inflection_is_marked_without_guessing(surname):
    result = extract_debtor_full_name_candidates(
        f"Признать {surname} Ивана Ивановича банкротом."
    )
    if surname.endswith(("ого", "его")):
        assert result.selected is None
    else:
        assert result.selected.value == f"{surname} Иван Иванович"
    assert result.candidates[0].value == f"{surname} Иван Иванович"
    assert result.problem == "ambiguous_nominative"
    assert "unresolved_surname_nominative" in result.candidates[0].normalization
    assert result.candidates[0].requires_confirmation is True


def test_written_nominative_resolves_ambiguous_reverse_inflection():
    text = ("Заявление Гордана Ивана Ивановича о банкротстве. "
            "Должник: Гордан Иван Иванович.")
    result = extract_debtor_full_name_candidates(text)
    assert result.selected.value == "Гордан Иван Иванович"
    assert result.problem is None
    assert "unresolved_surname_nominative" not in result.selected.normalization


def test_historical_identity_block_preserves_admission_date_and_following_narrative():
    text = (
        "заявление Ивановой Анны Ивановны о банкротстве. УСТАНОВИЛ: "
        "Определением суда от 01.01.2025 принято к производству заявление "
        "должника-гражданина Петровой Елены Петровны "
        "(11.03.1978 г.р., место рождения: г. Москва, адрес: ул. Садовая "
        "(район Центральный), д. 5, ИНН: 123456789012, СНИЛС: 123-456-789 00) "
        "о признании банкротом. Заседание назначено на 02.02.2025. "
        "РЕШИЛ: Признать Иванову Анну Ивановну банкротом."
    )
    blocks = ignored_historical_debtor_identity_blocks(text)
    assert len(blocks) == 1
    block = blocks[0]
    assert block.value == "Петрова Елена Петровна"
    assert text[block.name_start:block.name_end] == "Петровой Елены Петровны"
    assert text[block.start:block.end] == block.quote
    assert block.quote.endswith("СНИЛС: 123-456-789 00)")
    assert "ИНН: 123456789012" in block.quote
    assert "01.01.2025" not in block.quote
    assert "02.02.2025" not in block.quote
    assert "о признании банкротом" not in block.quote


@pytest.mark.parametrize("personal_block", [
    "", " (11.03.1978 г.р., ИНН: 123456789012",  # Unbalanced: name only.
])
def test_historical_identity_without_closed_parenthesis_masks_only_name(personal_block):
    text = (
        "заявление Ивановой Анны Ивановны. УСТАНОВИЛ: "
        "Определением суда от 01.01.2025 принято к производству заявление "
        f"должника-гражданина Петровой Елены Петровны{personal_block}. "
        "РЕШИЛ: Признать Иванову Анну Ивановну банкротом."
    )
    blocks = ignored_historical_debtor_identity_blocks(text)
    assert len(blocks) == 1
    assert blocks[0].quote == "Петровой Елены Петровны"


def test_historical_block_api_never_masks_conflicting_current_identity():
    text = (
        "заявление Ивановой Анны Ивановны. УСТАНОВИЛ: "
        "Определением суда от 01.01.2025 принято к производству заявление "
        "должника-гражданина Петровой Елены Петровны (ИНН: 123456789012). "
        "РЕШИЛ: Признать Иванову Анну Ивановну банкротом. "
        "Открыть в отношении Сидоровой Марии Сергеевны реализацию имущества."
    )
    assert ignored_historical_debtor_identity_blocks(text) == []

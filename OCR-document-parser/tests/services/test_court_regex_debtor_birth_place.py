from dataclasses import FrozenInstanceError

import pytest

from ocr_platform.services.court_regex_debtor_birth_place import (
    extract_debtor_birth_place_candidates,
)


def identity(place: str, marker: str = "место рождения:") -> str:
    return (
        f"Признать Иванова Ивана Ивановича (01.02.1980 года рождения, "
        f"{marker} {place}, ИНН 123456789012, адрес: г. Казань) банкротом."
    )


@pytest.mark.parametrize(
    "marker",
    [
        "место рождения:",
        "место рождения -",
        "уроженец",
        "уроженка",
        "уроженку",
        "уроженца:",
    ],
)
def test_explicit_markers(marker):
    text = identity("г. Омск, Омская обл.", marker)
    result = extract_debtor_birth_place_candidates(text)
    assert result.problem is None
    assert result.selected.value == "г. Омск, Омская обл."
    assert result.selected.requires_confirmation is True
    for candidate in result.candidates:
        assert text[candidate.start : candidate.end] == candidate.quote
        assert "Иванова Ивана Ивановича" in candidate.quote
        assert marker in candidate.quote


@pytest.mark.parametrize(
    "birth", ["31.08.1971,", "31.08.1971 г.р.,", "31 августа 1971 года,"]
)
def test_combined_date_and_place(birth):
    text = identity(f"{birth} гор. Ростов", "дата и место рождения:")
    assert extract_debtor_birth_place_candidates(text).selected.value == "гор. Ростов"


def test_born_after_date():
    text = "Должник родился 01.01.1980 в г. Омск, СНИЛС 123-456-789 01."
    assert extract_debtor_birth_place_candidates(text).selected.value == "г. Омск"


@pytest.mark.parametrize(
    "ending",
    [
        ", дата рождения: 01.01.1980",
        ", 01.01.1980 года рождения",
        ", СНИЛС 123-456-789 01",
        ", адрес регистрации: г. Казань",
        ", зарегистрирован по адресу: г. Казань",
        ", место жительства: г. Казань",
        ", паспорт 1234 567890",
        ", ул. Ленина, д. 5",
        ", 12 мая 1980 года",
    ],
)
def test_metadata_boundaries(ending):
    result = extract_debtor_birth_place_candidates(identity("г. Омск" + ending))
    assert result.selected.value == "г. Омск"


def test_preserves_balanced_geography_parentheses():
    result = extract_debtor_birth_place_candidates(
        identity("Республика Саха (Якутия), с. Чурапча")
    )
    assert result.selected.value == "Республика Саха (Якутия), с. Чурапча"


def test_removes_metadata_open_parenthesis():
    result = extract_debtor_birth_place_candidates(
        identity("г. Омск (ИНН 123456789012)")
    )
    assert result.selected.value == "г. Омск"


def test_semicolons_inside_literal_place():
    result = extract_debtor_birth_place_candidates(
        identity("Курская обл.; Конышевский; Захарково")
    )
    assert result.selected.value == "Курская обл.; Конышевский; Захарково"


@pytest.mark.parametrize(
    "text",
    [
        "Арбитражный суд г. Омска. Адрес суда: г. Омск, ул. Ленина.",
        "Должник Иванов Иван Иванович зарегистрирован по адресу: г. Омск.",
        "Финансовый управляющий Иванов Иван Иванович (место рождения: г. Омск).",
        "Кредитор Иванов Иван Иванович (место рождения: г. Омск) просит признать должника банкротом.",
        "Супруга должника: место рождения г. Омск.",
        "Признать Иванова Ивана Ивановича банкротом. Его дочь Иванова Анна Ивановна, место рождения: г. Омск.",
        "Истребовать ФИО, дату и место рождения, паспортные данные должника.",
        "Должник, место рождения: , ИНН 123456789012.",
        "Должник, дата и место рождения: г. Омск.",
    ],
)
def test_does_not_infer_or_extract_other_people(text):
    result = extract_debtor_birth_place_candidates(text)
    assert result.selected is None
    assert result.problem == "not_found"


def test_name_with_ogly():
    text = "Заявление Алиева Исы Салман Оглы (место рождения: с. Тест, ИНН 123456789012) о признании банкротом."
    assert extract_debtor_birth_place_candidates(text).selected.value == "с. Тест"


def test_direct_debtor_label():
    text = "Место рождения должника: г. Омск, СНИЛС 123-456-789 01."
    assert extract_debtor_birth_place_candidates(text).selected.value == "г. Омск"


def test_duplicate_and_format_variants_do_not_conflict():
    result = extract_debtor_birth_place_candidates(
        identity("г. Усть-Илимск Иркутской области")
        + identity("гор. Усть- Илимск Иркутской обл.")
    )
    assert result.selected is not None
    assert len(result.candidates) == 2
    assert result.problem is None


def test_literal_conflict_requires_confirmation():
    result = extract_debtor_birth_place_candidates(
        identity("г. Омск") + identity("г. Томск")
    )
    assert result.selected is None
    assert len(result.candidates) == 2
    assert result.problem == "conflicting_debtor_birth_places"


def test_raw_offsets_survive_normalization():
    text = "Преамбула\n" + identity("Г.Омск\nОмская обл.")
    candidate = extract_debtor_birth_place_candidates(text).selected
    assert candidate.value == "г. Омск Омская обл."
    assert "Г.Омск\nОмская обл." in candidate.quote
    assert candidate.quote == text[candidate.start : candidate.end]
    with pytest.raises(FrozenInstanceError):
        candidate.value = "г. Томск"


def test_no_place_name_correction_or_completion():
    result = extract_debtor_birth_place_candidates(
        identity("д. Наратасы Шаранский район Башкирская АССР")
    )
    assert result.selected.value == "д. Наратасы Шаранский район Башкирская АССР"


def test_page_header_is_rejected_as_value():
    result = extract_debtor_birth_place_candidates(
        identity("г. Омск 6 А65-12345/2026 Омская обл.")
    )
    assert result.selected is None


@pytest.mark.parametrize(
    "place", ["не указано", "не установлено", "неизвестно", "отсутствует"]
)
def test_explicit_absence_is_not_a_place(place):
    assert extract_debtor_birth_place_candidates(identity(place)).selected is None


def test_sentence_after_place_is_not_geography():
    text = "Должник родился в г. Омск. Его родители переехали в Томск."
    assert extract_debtor_birth_place_candidates(text).selected.value == "г. Омск"


@pytest.mark.parametrize(
    "place",
    [
        "р. пос. Тугулым Свердловская область",
        "раб. пос. Затон им. Куйбышева",
        "г. Ереван Арм. ССР",
        "сел. Харбук Республики Дагестан",
    ],
)
def test_geographic_abbreviation_periods_are_not_sentences(place):
    assert (
        extract_debtor_birth_place_candidates(identity(place)).selected.value == place
    )


@pytest.mark.parametrize(
    "marker", ["м.р.", "м. р.", "урож.", "место рожд:", "место рожд.:"]
)
def test_abbreviated_explicit_place_markers(marker):
    text = identity("г. Омск", marker)
    candidate = extract_debtor_birth_place_candidates(text).selected
    assert candidate.value == "г. Омск"
    assert candidate.quote == text[candidate.start : candidate.end]


def test_abbreviated_born_with_date():
    text = "Заявление Иванова Ивана Ивановича (род. 01.01.1980 г. в г. Омск, ИНН 123456789012) о признании себя банкротом."
    assert extract_debtor_birth_place_candidates(text).selected.value == "г. Омск"


@pytest.mark.parametrize(
    "text",
    [
        "Должник Иванов Иван Иванович (01.01.1980, г. Омск, ИНН 123456789012).",
        "Должник Иванов Иван Иванович (01.01.1980 г.р., г. Омск, СНИЛС 123-456-789 01).",
        "Заявление Иванова Ивана Ивановича, ИНН 123456789012, 01.01.1980 года рождения, г. Омск, Омской области, о признании его банкротом.",
        "Признать Иванова Ивана Ивановича (01.01.1980 г.р., г. Омск, адрес регистрации: г. Казань, ул. Ленина) банкротом.",
    ],
)
def test_positional_debtor_identity_block(text):
    candidate = extract_debtor_birth_place_candidates(text).selected
    assert candidate.value in {"г. Омск", "г. Омск, Омской области"}
    assert candidate.quote == text[candidate.start : candidate.end]
    assert candidate.requires_confirmation


@pytest.mark.parametrize(
    "text",
    [
        "Признать Иванова Ивана Ивановича (01.01.1980, г. Омск, ул. Ленина, д. 5, ИНН 123456789012) банкротом.",
        "Признать Иванова Ивана Ивановича (01.01.1980, г. Омск, д. 5, СНИЛС 123-456-789 01) банкротом.",
        "Признать Иванова Ивана Ивановича (01.01.1980, адрес регистрации: г. Омск, ИНН 123456789012) банкротом.",
        "Финансовый управляющий Иванов Иван Иванович (01.01.1980 г.р., г. Омск, ИНН 123456789012).",
        "Кредитор Иванов Иван Иванович (01.01.1980, г. Омск, ИНН 123456789012) просит признать должника банкротом.",
        "Признать Иванова Ивана Ивановича банкротом. Судебное заседание: 01.01.2026, г. Омск, ИНН 123456789012.",
        "Должник Иванов Иван Иванович, заявление принято 01.01.2026, г. Омск, ИНН 123456789012.",
        "Признать Иванова Ивана Ивановича (01.01.1980, г. Омск) банкротом.",
        "Должник Иванов Иван Иванович. Дети должника: Петров Петр Петрович (01.01.2010, г. Омск, ИНН 123456789012).",
    ],
)
def test_positional_block_rejects_addresses_dates_other_people_and_weak_boundaries(
    text,
):
    assert extract_debtor_birth_place_candidates(text).selected is None


def test_mixed_script_district_abbreviation_only():
    text = identity("д. Власть труда Рыбинского p-нa Красноярского края", "урож.")
    candidate = extract_debtor_birth_place_candidates(text).selected
    assert candidate.value == "д. Власть труда Рыбинского р-на Красноярского края"
    assert "p-нa" in candidate.quote


def test_short_birth_marker_must_be_a_whole_word():
    text = "Должник Иванов Иван Иванович, город. Омск, ИНН 123456789012."
    assert extract_debtor_birth_place_candidates(text).selected is None


def test_information_request_for_another_person_does_not_conflict_with_debtor():
    text = (
        identity("с. Мокрушенское")
        + " Истребовать сведения в отношении Петрова Петра Петровича "
        "(25.05.2010 года рождения, место рождения: с. Вороковка, ИНН 999999999999)."
    )
    result = extract_debtor_birth_place_candidates(text)
    assert result.selected.value == "с. Мокрушенское"
    assert len(result.candidates) == 1


def test_procedure_introduction_still_establishes_debtor_role():
    text = "Ввести в отношении ИП Иванова Ивана Ивановича (место рождения: гор. Бакал, ИНН 123456789012) процедуру реструктуризации долгов."
    assert extract_debtor_birth_place_candidates(text).selected.value == "гор. Бакал"


def test_full_literal_place_beats_abbreviated_repeat_for_same_person_and_inn():
    text = (
        "Признать Рамазанову Ларису Гасановну (место рождения: РД, г. Избербаш, ИНН 123456789012) банкротом. "
        "Заявление Рамазановой Ларисы Гасановны (место рождения: РД, ИНН 123456789012) о признании банкротом."
    )
    result = extract_debtor_birth_place_candidates(text)
    assert result.selected.value == "РД, г. Избербаш"
    assert len(result.candidates) == 2
    assert result.selected.quote == text[result.selected.start : result.selected.end]


@pytest.mark.parametrize(
    "second",
    [
        "Признать Рамазанову Ларису Гасановну (место рождения: РД, ИНН 999999999999) банкротом.",
        "Признать Иванова Ивана Ивановича (место рождения: РД, ИНН 123456789012) банкротом.",
        "Признать Рамазанову Ларису Гасановну (место рождения: РД) банкротом.",
        "Признать Рамазанову Ларису Гасановну (место рождения: РД, г. Каспийск, ИНН 123456789012) банкротом.",
    ],
)
def test_abbreviated_place_cannot_merge_different_or_unidentified_persons(second):
    text = (
        "Признать Рамазанову Ларису Гасановну (место рождения: РД, г. Избербаш, ИНН 123456789012) банкротом. "
        + second
    )
    result = extract_debtor_birth_place_candidates(text)
    assert result.selected is None
    assert result.problem == "conflicting_debtor_birth_places"


def test_distinct_explicit_bankruptcy_persons_remain_conflicting():
    text = (
        "Заявление Сунгатуллина Зуфара Хамитовича (место рождения: с. Кукиево, ИНН 123456789012) о признании банкротом. "
        "Признать Зеленова Артема Александровича (место рождения: гор. Казань, ИНН 999999999999) банкротом."
    )
    result = extract_debtor_birth_place_candidates(text)
    assert result.selected is None
    assert len(result.candidates) == 2

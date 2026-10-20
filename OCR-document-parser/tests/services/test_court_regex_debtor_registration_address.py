"""Debtor role, address boundaries, ambiguity and literal evidence contracts."""

import pytest

from ocr_platform.services.court_regex_debtor_registration_address import (
    extract_debtor_registration_address_candidates as extract,
)


def _identity(address: str, label: str = "адрес регистрации:") -> str:
    return ("Признать Иванова Ивана Ивановича (01.01.1980 года рождения, "
            f"ИНН 123456789012, {label} {address}) несостоятельным (банкротом).")


@pytest.mark.parametrize("label", [
    "адрес регистрации:", "место регистрации:", "место жительства:",
    "место жительства (регистрации):", "адрес проживания:", "адрес:",
    "зарегистрированного по адресу", "зарегистрированного в", "проживает по адресу:",
])
def test_debtor_identity_labels(label):
    text = _identity("123456, г. Москва, ул. Мира, д. 1, кв. 2", label)
    result = extract(text)
    assert result.problem is None
    assert result.selected.value == "123456, г. Москва, ул. Мира, д. 1, кв. 2"
    assert result.selected.quote == text[result.selected.start:result.selected.end]
    assert result.selected.requires_confirmation is True


def test_whitespace_delimiters_and_nested_geography():
    text = _identity("Республика Саха (Якутия),с. Сыдыбыл,\n ул. Мира, д. 30,  кв. 2")
    assert extract(text).selected.value == (
        "Республика Саха (Якутия), с. Сыдыбыл, ул. Мира, д. 30, кв. 2"
    )


def test_line_wrapped_hyphen_and_no_invented_index():
    result = extract(_identity("г. Санкт-\nПетербург, ул. Мира, д. 1"))
    assert result.selected.value == "г. Санкт-Петербург, ул. Мира, д. 1"


def test_partial_city_is_not_completed():
    assert extract(_identity("г. Пятигорск Ставропольского края")).selected.value == (
        "г. Пятигорск Ставропольского края"
    )


def test_source_proper_name_case_is_preserved():
    text = _identity("Ростовская область, г. Новошахтинск, ул. Новикова-прибоя, д. 5")
    assert "Новикова-прибоя" in extract(text).selected.value


@pytest.mark.parametrize("role", [
    "Финансовый управляющий", "Кредитор", "Представитель должника", "Супруга должника",
])
def test_other_people_are_excluded(role):
    text = (f"{role} Петров Петр Петрович (ИНН 123456789012, "
            "адрес регистрации: г. Казань, ул. Мира, д. 2).")
    assert extract(text).selected is None


@pytest.mark.parametrize("text", [
    "Арбитражный суд: адрес: г. Москва, ул. Мира, д. 1.",
    "СРО: адрес регистрации: г. Москва, ул. Мира, д. 1.",
    "Признать Иванова Ивана Ивановича (место рождения: г. Москва) банкротом.",
    "Должник должен сообщить адрес регистрации, паспортные данные и ИНН.",
    "г. Москва, ул. Мира, д. 1",
    "",
])
def test_unbound_or_nonaddress_text_abstains(text):
    assert extract(text).selected is None


def test_postal_address_is_excluded():
    text = _identity("г. Москва, ул. Мира, д. 1, почтовый адрес: г. Казань, а/я 44")
    result = extract(text)
    assert result.selected.value == "г. Москва, ул. Мира, д. 1"
    assert len(result.candidates) == 1


@pytest.mark.parametrize("metadata", [
    "ИНН 123456789012", "СНИЛС 123-456-789 01", "страховой номер индивидуального лицевого счета 123",
    "идентификационный номер налогоплательщика (ИНН) 123", "дата рождения 01.01.1980",
])
def test_metadata_is_not_part_of_address(metadata):
    text = _identity(f"г. Москва, ул. Мира, д. 1, {metadata}")
    assert extract(text).selected.value == "г. Москва, ул. Мира, д. 1"


def test_conflicting_debtor_addresses_abstain():
    text = _identity("г. Москва, ул. Мира, д. 1") + _identity("г. Казань, ул. Мира, д. 2")
    result = extract(text)
    assert result.selected is None
    assert result.problem == "conflicting_debtor_registration_addresses"
    assert len(result.candidates) == 2


def test_registration_and_residence_conflict():
    text = _identity("г. Москва, д. 1, адрес проживания: г. Казань, д. 2")
    assert extract(text).problem == "conflicting_debtor_registration_addresses"


def test_temporary_registration_conflict():
    text = _identity("г. Москва, д. 1; адрес регистрации по месту пребывания с "
                     "01.01.2025 по 01.01.2027: г. Казань, д. 2")
    assert extract(text).problem == "conflicting_debtor_registration_addresses"


def test_two_debtors_with_same_address_abstain():
    text = _identity("г. Москва, д. 1") + _identity("г. Москва, д. 1").replace(
        "Иванова Ивана Ивановича", "Петрова Петра Петровича"
    )
    assert extract(text).problem == "conflicting_debtors"


def test_named_creditor_application_is_not_debtor_address():
    text = ("заявление Петрова Петра Петровича (ИНН 123456789012, "
            "адрес регистрации: г. Казань, д. 1) о признании "
            "Иванова Ивана Ивановича несостоятельным (банкротом).")
    assert extract(text).selected is None


def test_explicit_second_debtor_without_address_prevents_selection():
    text = _identity("г. Москва, д. 1")
    text += "Признать Петрова Петра Петровича несостоятельным (банкротом)."
    assert extract(text).problem == "conflicting_debtors"


def test_parenthesized_maiden_name_does_not_hide_conflicting_debtor():
    text = _identity("г. Москва, д. 1").replace(
        "Иванова Ивана Ивановича", "Медвецкую (Амочкину) Наталью Сергеевну"
    )
    text += _identity("г. Казань, д. 2")
    assert extract(text).problem == "conflicting_debtors"


def test_next_numbered_order_is_not_an_address_component():
    text = ("Признать Иванова Ивана Ивановича, ИНН 123456789012, "
            "зарегистрированного по адресу: г. Москва, квартира 4.\n"
            "2. Ввести процедуру реализации имущества.")
    assert extract(text).selected.value == "г. Москва, квартира 4"


def test_short_first_name_inflection_does_not_create_conflict():
    text = _identity("г. Москва, д. 1").replace("Иванова Ивана Ивановича", "Володиной Ирины Николаевны")
    text += text.replace("Володиной Ирины Николаевны", "Володину Ирину Николаевну")
    assert extract(text).selected.value == "г. Москва, д. 1"


def test_rare_first_name_and_unicode_offsets():
    text = "🙂\n" + _identity("г. Москва, д. 1").replace(
        "Иванова Ивана Ивановича", "Бейшекееву Джилдыз Замировну"
    )
    result = extract(text)
    assert result.selected.value == "г. Москва, д. 1"
    for candidate in result.candidates:
        assert candidate.quote == text[candidate.start:candidate.end]


@pytest.mark.parametrize("label", [
    "адрес регистрации", "адрес регистрации (проживания):",
    "место жительства по регистрации/Фактическое проживание:",
    "место жительства согласно документам о регистрации по месту жительства:",
    "место регистрации по адресу:", "адрес места жительства:",
    "адрес преимущественного места пребывания:", "зарегистрирован:",
])
def test_explicit_residence_label_variants(label):
    result = extract(_identity("г. Казань, ул. Лесная, д. 12, кв. 3", label))
    assert result.selected.value == "г. Казань, ул. Лесная, д. 12, кв. 3"


@pytest.mark.parametrize("prefix", ["ИП ", "гр. ", "индивидуального предпринимателя "])
def test_debtor_status_prefix(prefix):
    text = _identity("г. Казань, д. 1").replace("Признать ", "Признать должника " + prefix)
    assert extract(text).selected.value == "г. Казань, д. 1"


def test_multiple_maiden_names_and_uppercase_ogly():
    female = _identity("г. Казань, д. 1").replace(
        "Иванова Ивана Ивановича", "Глазунову (Романову, Засову) Альбину Нафисовну"
    )
    male = _identity("г. Казань, д. 1").replace(
        "Иванова Ивана Ивановича", "Байларова Ису Салман Оглы"
    )
    assert extract(female).selected
    assert extract(male).selected


@pytest.mark.parametrize("interrupt", ["5 А65-1234/2026", "8 39_9433375"])
@pytest.mark.parametrize("position", ["begin", "middle"])
def test_complete_service_header_is_removed_with_literal_evidence(interrupt, position):
    clean = "123456, г. Казань, ул. Лесная, д. 7"
    dirty = (f"\n{interrupt}\n{clean}" if position == "begin" else
             f"123456, г. Казань,\n{interrupt}\nул. Лесная, д. 7")
    text = _identity(clean) + _identity(dirty)
    result = extract(text)
    assert result.selected.value == clean
    assert all(c.quote == text[c.start:c.end] for c in result.candidates)
    assert any(interrupt in c.quote for c in result.candidates)


def test_page_number_interrupts_hyphenated_geographic_word():
    text = _identity("РСО-Алания, г. Владикавказ, д. 7")
    text += _identity("РСО-\n8\nАлания, г. Владикавказ, д. 7")
    assert extract(text).selected


def test_actual_house_number_on_separate_line_is_preserved():
    assert extract(_identity("г. Казань, ул. Мира, д.\n8\n, кв. 1")).selected.value == (
        "г. Казань, ул. Мира, д. 8, кв. 1"
    )


@pytest.mark.parametrize("variant", [
    "Калужская область, гор. Калуга, д. 1", "Калужская области, город Калуга, д. 1",
])
def test_geo_designator_synonyms_compare_equal_but_value_stays_literal(variant):
    clean = "Калужская обл., г. Калуга, д. 1"
    assert extract(_identity(clean) + _identity(variant)).selected.value == clean


def test_soft_hyphen_ocr_artifact_is_not_an_address_conflict():
    text = _identity("Республика Башкортостан, г. Уфа, д. 7")
    text += _identity("Республика Баш¬кортостан, г. Уфа, д. 7")
    assert extract(text).selected


def test_next_event_date_is_not_included_in_address():
    text = ("Признать Иванова Ивана Ивановича, адрес регистрации: г. Казань, д. 1, "
            "26 июня 2026 года обратился в суд.")
    assert extract(text).selected.value == "г. Казань, д. 1"


def test_rublevskoe_street_is_not_rejected_as_rubles():
    assert extract(_identity("г. Москва, ш. Рублёвское, д. 18, кв. 2")).selected


def test_bare_address_in_identity_metadata_and_postcode_parenthesis():
    text = ("Заявление Иванова Ивана Ивановича (198303, Санкт-Петербург, "
            "пр. Ветеранов, д. 12, кв. 1) о признании его банкротом.")
    assert extract(text).selected.value == "198303, Санкт-Петербург, пр. Ветеранов, д. 12, кв. 1"
    text = ("Заявление Иванова Ивана Ивановича, 01.01.1980 г.р., место рождения: г. Омск, "
            "ИНН 123456789012, СНИЛС 123-456-789 01, Ставропольский край, "
            "г. Ессентуки, ул. Лесная, д. 7, о признании его банкротом.")
    assert extract(text).selected.value == "Ставропольский край, г. Ессентуки, ул. Лесная, д. 7"


def test_bare_address_label_can_be_followed_by_snils():
    text = ("Признать Иванова Ивана Ивановича (адрес: г. Мытищи, д. 1, "
            "СНИЛС 123-456-789 01) банкротом.")
    assert extract(text).selected.value == "г. Мытищи, д. 1"


def test_no_bare_birth_date_or_birth_place_is_a_residence():
    text = ("Признать Иванова Ивана Ивановича (ИНН 123456789012, "
            "г.р. 01.01.1980, м.р. гор. Москва) банкротом.")
    assert extract(text).selected is None


def test_other_institution_postcode_after_identity_is_not_owned_by_debtor():
    text = _identity("г. Омск, д. 1")
    text += " СРО (123112, г. Москва, ул. Лесная, д. 12) представила кандидатуру."
    assert extract(text).selected.value == "г. Омск, д. 1"


def test_current_second_procedure_target_is_a_real_identity_conflict():
    text = _identity("г. Омск, д. 1")
    text += "Открыть в отношении Петрова Петра Петровича процедуру реализации имущества."
    result = extract(text)
    assert result.problem == "conflicting_debtors"
    assert result.candidates[0].value == "г. Омск, д. 1"


@pytest.mark.parametrize("prefix", [
    "Утвердить финансовым управляющим гражданина-должника – ",
    "Утвердить финансовым управляющим в деле о банкротстве должника ",
])
def test_manager_appointment_does_not_create_another_debtor(prefix):
    text = _identity("г. Омск, д. 1")
    text += prefix + "Петрова Петра Петровича (ИНН 123456789012, адрес: г. Уфа, д. 29)."
    assert extract(text).selected.value == "г. Омск, д. 1"


def test_country_prefix_equivalence_does_not_erase_street_named_rossiya():
    assert extract(_identity("г. Омск, ул. Россия, д. 1") + _identity(
        "г. Омск, ул. Россия, д. 2"
    )).problem == "conflicting_debtor_registration_addresses"


def _with_historical_other_person(current_recognition="Иванову Анну Ивановну"):
    return (
        "Суд рассмотрел заявление Ивановой Анны Ивановны (ИНН 123456789012, "
        "адрес регистрации: г. Москва, ул. Саратовская, д. 5, кв. 137) о банкротстве. "
        "УСТАНОВИЛ: Определением суда от 01.01.2025 принято к производству заявление "
        "должника-гражданина Петровой Елены Петровны (11.03.1978 г.р., ИНН 234567890123, "
        "адрес регистрации: г. Москва, ул. Илимская, д. 2, кв. 447) о банкротстве. "
        f"РЕШИЛ: Признать {current_recognition} (ИНН 123456789012, "
        "адрес регистрации: г. Москва, ул. Саратовская, д. 5, кв. 137) банкротом."
    )


def test_proven_historical_other_person_address_does_not_conflict_with_current_debtor():
    text = "🙂\n" + _with_historical_other_person()
    result = extract(text)
    assert result.selected.value == "г. Москва, ул. Саратовская, д. 5, кв. 137"
    assert all("Илимская" not in item.value for item in result.candidates)
    assert all(item.quote == text[item.start:item.end] for item in result.candidates)
    # Historical source and date remain intact; no whitespace masking.
    assert "01.01.2025" in text and "ул. Илимская" in text


def test_disagreeing_current_recognition_does_not_erase_historical_address():
    result = extract(_with_historical_other_person("Петрову Елену Петровну"))
    assert result.selected is None
    assert any("Илимская" in item.value for item in result.candidates)


@pytest.mark.parametrize("action", [
    "Признать Сидорову Марию Сергеевну банкротом.",
    "Открыть в отношении Сидоровой Марии Сергеевны процедуру реализации имущества.",
    "Ввести в отношении Сидоровой Марии Сергеевны процедуру реструктуризации долгов.",
])
def test_historical_filter_never_hides_another_current_target(action):
    result = extract(_with_historical_other_person() + action)
    assert result.selected is None
    assert result.problem == "conflicting_debtors"
    assert any("Илимская" in item.value for item in result.candidates)

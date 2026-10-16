import pytest

from ocr_platform.services import rtk3_requisites as service


def spouses():
    return (
        "Дело №А07-100/2026. Включить в реестр требований кредиторов "
        "Тестовой (Прежней) Ангелины Николаевны. "
        "Заинтересованное лицо — супруг должницы Тестов Ильсур Филаритович. УСТАНОВИЛ: "
        "Тестов Ильсур Филаритович (ИНН 001111111111), именуемый Должник 1; "
        "Тестова Ангелина Николаевна (СНИЛС 12345678901, ИНН 002222222222), "
        "именуемая Должник 2."
    )


def test_main_debtor_not_spouse_or_first_tax_id():
    found = service.source_inn(spouses(), "debtor_inn")
    assert found.value == "002222222222"
    assert "Ангелина Николаевна" in found.quote
    assert found.quote in spouses()


def test_two_main_debtors_or_two_ids_for_one_person_need_review():
    text = spouses().replace("Заинтересованное лицо — супруг должницы", "и должника")
    assert service.source_inn(text, "debtor_inn").value is None
    text = spouses() + " Тестова Ангелина Николаевна (ИНН 012345678901)."
    assert service.source_inn(text, "debtor_inn").value is None


def test_no_zero_padding_or_snils_as_inn():
    text = spouses().replace("002222222222", "02222222222")
    assert service.source_inn(text, "debtor_inn").value is None


def test_creditor_record_cannot_cross_into_debtor_record():
    text = ("Требование ПАО «Сбербанк» (ОГРН 1027700132195) к должнику "
            "Тестову Ивану Ивановичу (ИНН 012345678901).")
    assert service.source_inn(text, "creditor_inn", "ПАО «Сбербанк»").value is None


def test_creditor_is_selected_by_legal_name_not_first_bank():
    text = ("ПАО «Другой» (ИНН 1111111111). "
            "Требование общества с ограниченной ответственностью профессиональной "
            "коллекторской организации «Тест» (ИНН 1234567890) к должнику.")
    found = service.source_inn(text, "creditor_inn", "ООО ПКО «Тест»")
    assert found.value == "1234567890"
    assert found.quote in text
    assert service.source_inn(text, "creditor_inn", "ПАО «Тест»").value is None


@pytest.mark.parametrize("value,expected", [
    ('публичное акционерное общество "Сбербанк России", г. Москва', 'ПАО «Сбербанк России»'),
    ('ООО профессиональная коллекторская организация “АйДи Коллект”', 'ООО ПКО «АйДи Коллект»'),
    ('АО «Тест», ИНН: 1234567890', 'АО «Тест»'),
    ('Банк ВТБ (ПАО)', 'Банк ВТБ'),
    ('ООО «Вернём»', 'ООО «Вернём»'),
    ('ПАО Сбербанк', 'ПАО «Сбербанк»'),
    ('непубличное акционерное общество «Тест»', 'АО «Тест»'),
])
def test_creditor_typography_preserves_legal_name(value, expected):
    assert service.normalize_creditor(value) == expected


def test_one_generic_word_does_not_prove_invented_creditor():
    quote = "кредитор ПАО Банк Настоящий (ИНН 1234567890)"
    assert service.creditor_value("ПАО Банк Выдуманный", quote, quote).value is None
    assert service.creditor_value("ООО «Настоящий»", quote, quote).value is None
    assert service.creditor_value("АО Банк Настоящий", quote, quote).value is None


def test_fiscal_body_and_person_nominative_match_declined_source():
    quote = "требование Межрайонной инспекции Федеральной налоговой службы №7 по Омской области"
    value = "Межрайонная инспекция Федеральной налоговой службы № 7 по Омской области"
    assert service.creditor_value(value, quote, quote).value == value.replace("№ 7", "№7")
    quote = "требование ИП Иванова Ивана Ивановича (ИНН 012345678901)"
    assert service.creditor_value("ИП Иванов Иван Иванович", quote, quote).value == "ИП Иванов Иван Иванович"
    quote = "требование общества с ограниченной ответственностью ПКО «Тест»"
    assert service.creditor_value("ПКО «Тест»", quote, quote).value == "ООО ПКО «Тест»"


def test_header_date_not_birth_hearing_or_signature():
    text = ("ОПРЕДЕЛЕНИЕ 08 октября 2026 года Дело №А50-100/26. "
            "Судья, ознакомившись с заявлением по делу должника (09.10.1990). "
            "Заседание назначено на 09.10.2026. Электронная подпись 10.10.2026.")
    assert service.ruling_date(text).value == "08.10.2026"
    assert service.publication_date(text).value is None


def test_statutory_date_is_not_current_act_date():
    text = ("ОПРЕДЕЛЕНИЕ (по пункту 3 статьи 100 закона от 26.10.2002 №127-ФЗ) "
            "06 октября 2026 года. Арбитражный суд в составе судьи.")
    assert service.ruling_date(text).value == "06.10.2026"


def test_full_act_and_pronounced_resolution_are_separate_dates():
    text = ("ОПРЕДЕЛЕНИЕ\n30 сентября 2026 года\n"
            "Резолютивная часть объявлена 15 сентября 2026 года\n"
            "Определение изготовлено в полном объеме 30 сентября 2026 года\n"
            "Арбитражный суд в составе судьи.")
    assert service.ruling_date(text).value == "30.09.2026"
    assert service.publication_date(text).value is None


def test_multiple_unexplained_heading_dates_are_not_guessed():
    assert service.ruling_date("Определение 01.10.2026 02.10.2026. В составе судьи.").value is None


def test_kad_date_recovery_and_literal_quote_not_birthday_same_day():
    text = "Дата рождения: 05.10.2026\nДата публикации в КАД: 05.10.2026"
    found = service.publication_date(text)
    assert found.value == "05.10.2026"
    assert found.quote == "Дата публикации в КАД: 05.10.2026"
    text += "\nДата размещения в КАД: 06.10.2026"
    assert service.publication_date(text).value is None
    assert service.publication_date("Дата публикации в КАД:\n05.10.2026 10:30").value == "05.10.2026"


@pytest.mark.parametrize("wording", ["31.02.2026", "31 февраля 2026", "29.02.2025"])
def test_impossible_calendar_date_is_not_returned(wording):
    assert service.dates(wording) == []

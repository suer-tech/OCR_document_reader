"""Venue extraction safety cases; no manual dataset values enter the extractor."""
import pytest

from ocr_platform.services.court_regex_hearing_address import extract_court_hearing_address_candidates as extract


def scheduled(tail):
    return "РЕШИЛ:\nНазначить судебное заседание на 10 марта 2027 года в 10 часов 30 минут " + tail


@pytest.mark.parametrize("address", [
    "г. Пермь, ул. Екатерининская, 177, зал 438",
    "г. Казань, ул. Ново-Песочная, д. 40, зал 4.03",
    "г. Севастополь, ул. Л. Павличенко, д. 5, зал № 7",
    "603082 г.Н.Новгород, Кремль, корпус 9, каб.№204",
    "115191, г.Москва, ул.Б.Тульская, д.17, зал № 3049, 3-й этаж",
    "г.Барнаул, пр.-т Ленина, 76, кааб. № 420",
    "г. Москва, пр. Академика Сахарова, 18",
])
def test_address_abbreviations_and_room_are_preserved(address):
    text = scheduled("в помещении суда по адресу: " + address + ". Обязать управляющего представить отчёт.")
    result = extract(text)
    assert result.selected.value == address
    item = result.selected
    assert item.quote == text[item.start:item.end]
    assert all(source.quote == text[source.start:source.end] for source in item.sources)
    assert item.requires_review is True
    assert not hasattr(item, "confidence")


def test_room_before_street_is_kept_with_its_own_source_span():
    text = scheduled("зал 610 в помещении суда по адресу: г. Москва, пр. Академика Сахарова, 18.")
    item = extract(text).selected
    assert item.value == "г. Москва, пр. Академика Сахарова, 18, зал 610"
    assert len(item.sources) == 2
    assert item.sources[1].quote == "зал 610"


def test_immediate_second_venue_sentence_and_phone_truncation():
    text = scheduled(". Судебное заседание состоится в здании суда по адресу: г. Брянск, ул. Горького, 58, зал 314, тел. 123.")
    assert extract(text).selected.value == "г. Брянск, ул. Горького, 58, зал 314"


def test_direct_local_address():
    assert extract(scheduled("по адресу: г. Пермь, ул. Ленина, д. 1.")).selected.value.endswith("д. 1")


def test_only_room_is_partial_not_full_address():
    text = "Арбитражный суд\n443001, г. Самара, ул. Самарская, 203 Б\n" + scheduled("в помещении суда, зал судебных заседаний 304.")
    result = extract(text)
    assert result.selected is None
    assert result.problem == "partial_location_only"
    assert result.candidates[0].value == "зал судебных заседаний 304"


def test_header_without_location_link_cannot_supply_venue():
    text = "Арбитражный суд\n107053, г. Москва, проспект Академика Сахарова, д. 18\n" + scheduled(". С даты принятия решения наступают последствия.")
    assert extract(text).problem == "no_linked_location"


@pytest.mark.parametrize("tail", [
    ". Обязать управляющего направить отчёт по адресу: г. Уфа, ул. Гафури, 29.",
    ". Должнику представить документы, адрес регистрации: г. Пермь, ул. Ленина, д. 1.",
    ". Кредитор находится по адресу: г. Пермь, ул. Ленина, д. 1.",
    "почтовый адрес для корреспонденции по адресу: г. Уфа, ул. Гафури, 29.",
])
def test_non_venue_addresses_are_rejected(tail):
    assert extract(scheduled(tail)).selected is None


def test_conditional_future_appointment_does_not_become_current():
    text = "РЕШИЛ:\nПри поступлении возражений назначить судебное заседание на 10 марта 2027 года в помещении суда по адресу: г. Пермь, ул. Ленина, 1."
    assert extract(text).selected is None


def test_address_of_prior_hearing_is_not_reused():
    text = "Судебное заседание назначено на 10 марта 2027 года по адресу: г. Пермь, ул. Ленина, 1.\nРЕШИЛ:\nПризнать гражданина банкротом."
    assert extract(text).selected is None


def test_ambiguous_appointment_abstains():
    text = scheduled("в помещении суда по адресу: г. Пермь, ул. Ленина, 1.") + "\nНазначить судебное заседание на 11 марта 2027 года в помещении суда по адресу: г. Пермь, ул. Ленина, 2."
    assert extract(text).selected is None


def test_no_hearing_consideration_role_is_explicit():
    text = "РЕШИЛ:\nРассмотрение отчета осуществляется судьей единолично без проведения судебного заседания до 10 марта 2027 года в помещении арбитражного суда по адресу: г. Барнаул, проспект Ленина, 76, кабинет №206, судья Иванов."
    item = extract(text).selected
    assert item.role == "consideration_without_hearing_venue"
    assert item.value.endswith("кабинет №206")


def test_no_trailing_sentence_punctuation_is_required():
    text = scheduled("в помещении суда по адресу: г. Ижевск, ул. Коммунаров, 365, зал 305 Определение может быть обжаловано.")
    assert extract(text).selected.value.endswith("зал 305")


def test_blank_text():
    assert extract("").selected is None


def test_whitespace_normalization_keeps_literal_evidence():
    text = scheduled("в помещении суда по адресу: г. Пермь,\nул. Ленина,\tд. 1, зал 101.")
    item = extract(text).selected
    assert item.value == "г. Пермь, ул. Ленина, д. 1, зал 101"
    assert "\n" in item.sources[0].quote

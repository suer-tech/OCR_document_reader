import json

import pytest

from ocr_platform.api.schemas import FieldValue
from ocr_platform.services.court_field_format import normalize_court_value as normalize
from ocr_platform.services.court_field_provenance import finalize_court_fields
from ocr_platform.services.court_regex_candidates import extract_court_candidates
from ocr_platform.services.validation_service import court_field_issues


@pytest.mark.parametrize("value", [None, "", "  ", "null", "None", "Не найдено", "Нет данных", "—"])
def test_missing_values_are_null(value):
    assert normalize("debtor_birth_place", value) is None


@pytest.mark.parametrize("name,value,expected", [
    ("debtor_inn", " 0512 04647801 ", "051204647801"),
    ("debtor_snils", "001 002 003 04", "001-002-003 04"),
    ("decision_date", "1.2.2026", "01.02.2026"),
    ("next_session_time", "9:05", "09:05"),
    ("next_session_time", "00:00", "00:00"),  # An explicitly printed midnight is not absence.
    ("procedure_duration_months", 6, 6),
    ("procedure_type", "процедура реализации имущества гражданина", "Реализация имущества"),
    ("procedure_type", "реструктуризация долгов", "Реструктуризация долгов"),
    ("procedure_end_date_source", "not_found", "not_specified"),
    ("early_report_required", False, False),
])
def test_canonical_format_preserves_content(name, value, expected):
    actual = normalize(name, value)
    assert actual == expected and type(actual) is type(expected)


@pytest.mark.parametrize("name,value", [
    ("debtor_inn", 51204647801), ("debtor_snils", 100200304),
    ("procedure_duration_months", "6"), ("procedure_duration_months", 6.0),
    ("procedure_duration_months", True), ("decision_date", "31.02.2026"),
    ("next_session_time", "25:00"), ("early_report_required", "false"),
    ("procedure_type", "наблюдение"), ("debtor_gender", "неизвестен"),
])
def test_invalid_types_and_values_are_not_guessed(name, value):
    with pytest.raises(ValueError):
        normalize(name, value)


def test_partial_name_and_address_keep_unknown_components_null():
    assert normalize("debtor_full_name", {"debtor_last_name": "Иванов", "debtor_first_name": "", "debtor_patronymic": None}) == {
        "debtor_last_name": "Иванов", "debtor_first_name": None, "debtor_patronymic": None}
    assert normalize("debtor_full_name", {"debtor_last_name": ""}) is None
    value = {"post_index": "045000", "region": None, "city": "г. Уфа", "street": "ул. Ленина, д. 1, кв. 5"}
    assert normalize("debtor_registration_address", value) == value
    assert normalize("debtor_registration_address", "с. Степное Степновского р-на Ставропольского края") == "с. Степное Степновского р-на Ставропольского края"


@pytest.mark.parametrize("suffix", ["Оглы", "Кызы", "Гызы", "Уулу"])
def test_all_compound_patronymic_suffixes_survive_regex(suffix):
    text = f"РЕШИЛ: Признать гражданина Гаджиева Фуада Рахман {suffix} несостоятельным (банкротом)."
    result = extract_court_candidates(text, ["debtor_full_name"])
    assert result.fields["debtor_full_name"].value["debtor_patronymic"] == f"Рахман {suffix}"


@pytest.mark.parametrize("number,expected", [
    ("А40-123/26-73-581Ф", "А40-123/2026-73-581Ф"),
    ("А15-1277-1/2026", "А15-1277-1/2026"),
])
def test_registry_preserves_the_selected_complete_case_number(number, expected):
    text = f"РЕШЕНИЕ\nДело № {number}\nРЕШИЛ: Ввести процедуру."
    assert extract_court_candidates(text, ["case_number"]).fields["case_number"].value == expected


def field(value, quote=None, text="", method="regex_jev", **kwargs):
    item = {"value": value, "confidence": .98, "reasoning": "Причина выбора.", "source": method, **kwargs}
    if quote:
        start = text.index(quote)
        item.update(evidence=quote, evidence_start=start, evidence_end=start + len(quote))
    return item


def test_source_follows_actual_quote_location_not_field_name():
    text = "РЕШЕНИЕ\nДело № А40-1/2026\nУСТАНОВИЛ:\nМесто рождения: г. Омск.\nРЕШИЛ:\nВвести процедуру.\nЭлектронная подпись действительна: Иванов."
    raw = {
        "case_number": field("А40-1/2026", "Дело № А40-1/2026", text),
        "debtor_birth_place": field("г. Омск", "Место рождения: г. Омск.", text),
        "procedure_type": field("реализация имущества гражданина", "Ввести процедуру.", text),
        "judge_full_name": field("Иванов", "Иванов.", text, method="court_llm_fallback"),
    }
    output = finalize_court_fields(text, raw)
    assert [output[name]["source"] for name in raw] == ["header", "motivating_part", "resolutive_part", "signature_block"]
    for name, value in output.items():
        assert value["extraction_method"] == raw[name]["source"]
        assert raw[name]["evidence"] in value["reasoning"]
        public = json.loads(FieldValue(name=name, **value).model_dump_json())
        assert public["extraction_method"] == value["extraction_method"]


def test_absence_uncertainty_and_false_remain_distinct_with_review():
    raw = {
        "resolutive_part_date": field(None),
        "debtor_inn": field(None, validation_issue="Нечитаемая цифра ИНН"),
        "early_report_required": field(False),
    }
    output = finalize_court_fields("РЕШИЛ: Ввести процедуру.", raw)
    assert output["resolutive_part_date"]["value"] is None
    assert output["resolutive_part_date"]["confidence"] == .98
    assert output["early_report_required"]["value"] is False
    assert "Цитата" not in output["resolutive_part_date"]["reasoning"]
    assert [issue.field_name for issue in court_field_issues(output)] == ["debtor_inn"]
    assert [issue.field_name for issue in court_field_issues(raw)] == ["debtor_inn"]  # historical payload
    raw["debtor_inn"]["extraction_method"] = None
    assert [issue.field_name for issue in court_field_issues(raw)] == ["debtor_inn"]


def test_calculation_quotes_actual_base_even_if_full_act_date_differs():
    text = ("РЕШЕНИЕ\nРезолютивная часть объявлена 01 октября 2026 года.\n"
            "Полный текст решения изготовлен 05 октября 2026 года.\nУСТАНОВИЛ:\nФакты.\n"
            "РЕШИЛ:\nВвести процедуру реализации имущества сроком на шесть месяцев.")
    # Preserve the existing date convention: the first written date is the
    # calculation base in this source, even though the full act is dated later.
    raw = {"procedure_end_date": field("01.04.2027", text, text),
           "procedure_end_date_is_calculated": field(True, text, text)}
    result = finalize_court_fields(text, raw)
    assert result["procedure_end_date"]["value"] == "01.04.2027"
    for item in result.values():
        assert item["source"] == "resolutive_part"
        assert "рассчитано" in item["reasoning"]
        assert "шесть месяцев" in item["reasoning"]
        assert "01 октября 2026" in item["reasoning"]


def test_long_section_quotes_are_marked_as_separate_exact_fragments():
    value = "Начало. " + "Факты дела. " * 150 + "Конец."
    text = "УСТАНОВИЛ:\n" + value + "\nРЕШИЛ:\nИтог."
    item = finalize_court_fields(text, {"motivating_part": field(value, value, text)})["motivating_part"]
    assert item["source"] == "motivating_part"
    assert item["value"] == value
    assert "Фрагменты начала и конца" in item["reasoning"]
    assert item["evidence"] == value


def test_invalid_format_requires_review_instead_of_invented_repair():
    item = finalize_court_fields("", {"debtor_inn": field(51204647801)})["debtor_inn"]
    assert item["value"] is None
    assert item["confidence"] == 0
    assert item["validation_issue"]

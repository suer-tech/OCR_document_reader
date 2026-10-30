"""Conservative identity fixes use source evidence, never an operator target."""
import copy

import pytest

from ocr_platform.services.court_field_provenance import finalize_court_fields
from ocr_platform.services.court_identifier_evidence import identifier_source_issue
from ocr_platform.services.court_regex_debtor_birth_place import (
    extract_debtor_birth_place_candidates,
)
from ocr_platform.services.court_regex_debtor_full_name import (
    extract_debtor_full_name_candidates,
)
from ocr_platform.services.validation_service import (
    court_field_issues,
    review_requirement,
)


@pytest.mark.parametrize("last,expected", [("Блока", "Блок"), ("Шульца", "Шульц"), ("Цоя", "Цой"), ("Кима", "Ким")])
def test_known_surname_oblique_case_needs_no_fuzzy_prediction(last, expected):
    text = f"РЕШИЛ: Признать {last} Олега Петровича несостоятельным (банкротом)."
    result = extract_debtor_full_name_candidates(text)
    assert result.selected.value == f"{expected} Олег Петрович"
    assert result.problem is None
    assert text[result.selected.start:result.selected.end] == result.selected.quote
    original = {"debtor_last_name": last, "debtor_first_name": "Олег", "debtor_patronymic": "Петрович"}
    output = finalize_court_fields(text, {"debtor_full_name": {"value": original, "source": "court_llm_fallback"}})
    assert output["debtor_full_name"]["value"] == {**original, "debtor_last_name": expected}
    assert original["debtor_last_name"] == last


@pytest.mark.parametrize("name", ["Сила Олег Петрович", "Кима Олег Петрович", "Роза Олег Петрович",
                                   "Сидоренко Анна Петровна", "Шульц Анна Петровна", "Ким Анна Петровна"])
def test_written_nominative_and_invariant_female_surnames_are_preserved(name):
    result = extract_debtor_full_name_candidates(f"Должник: {name}. РЕШИЛ: признать должника банкротом.")
    assert result.selected.value == name


def test_explicit_written_nominative_overrides_dictionary_reverse_case():
    text = "Должник: Кима Олег Петрович. РЕШИЛ: Признать Кима Олега Петровича банкротом."
    assert extract_debtor_full_name_candidates(text).selected.value == "Кима Олег Петрович"


def test_unknown_surname_does_not_lose_a_final_letter():
    result = extract_debtor_full_name_candidates("РЕШИЛ: Признать Сила Олега Петровича банкротом.")
    assert result.selected.value == "Сила Олег Петрович"
    assert result.problem == "ambiguous_nominative"


@pytest.mark.parametrize("name", ["Кима Олег Сидорович", "Кима Пётр Петрович", "Кима О. П."])
def test_different_or_incomplete_identity_is_never_replaced(name):
    text = "РЕШИЛ: Признать Кима Олега Петровича банкротом."
    output = finalize_court_fields(text, {"debtor_full_name": {"value": name, "source": "court_llm_fallback"}})
    assert output["debtor_full_name"]["value"] == name


@pytest.mark.parametrize("abbreviation,kind", [("Тат", "АССР"), ("Арм", "ССР"), ("Чук", "АО")])
def test_historical_region_abbreviation_is_not_a_sentence_end(abbreviation, kind):
    text = ("Признать Иванова Олега Петровича (место рождения: г. Тест "
            f"{abbreviation}. {kind}, ИНН 012345678901) банкротом.")
    result = extract_debtor_birth_place_candidates(text)
    assert result.selected.value == f"г. Тест {abbreviation}. {kind}"
    stored = {"value": f"г. Тест {abbreviation}", "source": "court_llm_fallback"}
    output = finalize_court_fields(text, {"debtor_birth_place": stored})["debtor_birth_place"]
    assert output["value"] == result.selected.value
    assert text[output["evidence_start"]:output["evidence_end"]] == output["evidence"]


def test_missing_region_type_is_not_invented():
    text = "Должник Иванов Олег Петрович (место рождения: г. Тест Тат, ИНН 012345678901)."
    assert finalize_court_fields(text, {"debtor_birth_place": {"value": "г. Тест Тат"}})["debtor_birth_place"]["value"] == "г. Тест Тат"


def test_absent_birthplace_is_not_inferred_from_registration():
    text = "Должник Иванов Олег Петрович (дата рождения 01.02.1980, адрес регистрации: г. Тест)."
    assert extract_debtor_birth_place_candidates(text).selected is None
    assert finalize_court_fields(text, {"debtor_birth_place": {"value": None}})["debtor_birth_place"]["value"] is None


def test_capitalized_district_boundary_changes_only_whitespace():
    text = ("Признать Иванова Олега Петровича (место рождения: пос. ЛеснойСосновский р-н, "
            "Московская обл., ИНН 012345678901) банкротом.")
    output = finalize_court_fields(text, {"debtor_birth_place": {"value": "пос. ЛеснойСосновский р-н, Московская обл."}})
    assert output["debtor_birth_place"]["value"] == "пос. Лесной Сосновский р-н, Московская обл."


@pytest.mark.parametrize("name,value,text", [
    ("debtor_inn", "012345678901", "Должник (ИНН: 012345678901)."),
    ("debtor_inn", "012345678901", "Должник (ИНН: 0123 4567 8901)."),
    ("debtor_inn", "012345678901", "ИНН: 0123456789 01. Суд рассмотрел дело."),
    ("debtor_snils", "001-002-003 04", "Должник (СНИЛС: 001_002_003 04)."),
    ("debtor_snils", "001-002-003 04", "СНИЛС: 001002003 04. Суд рассмотрел дело."),
    ("debtor_snils", "001-002-003 04", "Признать должника (001-002-003 04)."),
    ("debtor_snils", "001-002-003 04", "СНИЛС: 001-002-003 04\n2. Ввести процедуру."),
])
def test_literal_identifier_whole_numbers_keep_leading_zeroes(name, value, text):
    assert identifier_source_issue(name, value, text) is None


def test_truncated_inn_is_rejected_and_forces_review():
    text = "Признать Иванова Олега Петровича (ИНН 0123456789013) банкротом."
    fields = {"debtor_inn": {"value": "012345678901", "source": "court_llm_fallback", "confidence": .99}}
    before = copy.deepcopy(fields)
    output = finalize_court_fields(text, fields)
    assert output["debtor_inn"]["value"] is None
    assert "0123456789013" in output["debtor_inn"]["reasoning"]
    assert review_requirement(1.0, court_field_issues(output)) == (True, "court_field_requires_review")
    assert fields == before


def test_a_second_complete_number_is_not_rejected_as_a_prefix_of_another_one():
    text = "В первом упоминании ИНН 0123456789013. В другом месте ИНН 012345678901."
    assert identifier_source_issue("debtor_inn", "012345678901", text) is None


@pytest.mark.parametrize("name,value,text,expected", [
    ("debtor_inn", 123456789012, "ИНН 123456789012", "123456789012"),
    ("debtor_snils", 12345678901, "СНИЛС 123-456-789 01", "123-456-789 01"),
])
def test_legacy_numeric_identifiers_require_exact_full_source_support(name, value, text, expected):
    output = finalize_court_fields(text, {name: {"value": value}})
    assert output[name]["value"] == expected


@pytest.mark.parametrize("name,value,text", [
    ("debtor_inn", 1234567890, "ИНН 001234567890"),
    ("debtor_inn", 123456789012, "ИНН 1234567890123"),
    ("debtor_inn", 123456789012, "В акте нет номера"),
    ("debtor_snils", 123456789, "СНИЛС 001-234-567 89"),
    ("debtor_inn", True, "ИНН 123456789012"),
])
def test_numeric_identifiers_cannot_gain_lost_zeroes_or_source_digits(name, value, text):
    assert finalize_court_fields(text, {name: {"value": value}})[name]["value"] is None


def test_snils_across_a_repeated_page_header_uses_only_literal_number_fragments():
    text = "1 1234567_9876543\nРЕШЕНИЕ\nСНИЛС 001-002-003\n2 1234567_9876543\n04) банкротом."
    assert identifier_source_issue("debtor_snils", "001-002-003 04", text) is None
    # Other checksum digits cannot be manufactured by this layout handling.
    assert identifier_source_issue("debtor_snils", "001-002-003 05", text) is not None


def test_an_unverified_number_between_snils_fragments_requires_review():
    text = "СНИЛС 001-002-003\n2 1234567_9876543\n04) банкротом."
    assert identifier_source_issue("debtor_snils", "001-002-003 04", text) is not None


@pytest.mark.parametrize("name,value", [("debtor_inn", "012345678901"), ("debtor_snils", "001-002-003 04")])
def test_unconfirmed_identifier_is_preserved_for_manual_review_not_repaired(name, value):
    output = finalize_court_fields("Текст акта не содержит реквизита.", {name: {
        "value": value, "source": "court_llm_fallback", "confidence": .99,
    }})
    assert output[name]["value"] == value
    assert review_requirement(1.0, court_field_issues(output))[0] is True


@pytest.mark.asyncio
async def test_verified_name_and_birthplace_survive_the_full_court_route(monkeypatch):
    from types import SimpleNamespace

    from ocr_platform.services import court_regex_jev as service

    text = ("Признать Шульца Олега Петровича (место рождения: г. Тест Тат. АССР, "
            "ИНН 012345678901) банкротом.")
    def verify(**kwargs):
        assert kwargs["decisions_state"]["document"] == text
        return SimpleNamespace(parsed={"answers": {
            name: {"type": "noul", "noul": .99} for name in kwargs["response_schema"]
        }}, winner_model="offline-test")
    monkeypatch.setattr(service, "call_llm_json_with_fallback", verify)
    output = await service.extract_court_regex_jev(text, {
        "fields": {"debtor_full_name": {"required": True}, "debtor_birth_place": {"required": True}},
        "models": {"verification": {"threshold": .95}},
    }, document_id="test", pipeline_run_id="test")
    assert output["debtor_full_name"]["value"]["debtor_last_name"] == "Шульц"
    assert output["debtor_birth_place"]["value"] == "г. Тест Тат. АССР"
    assert not court_field_issues(output)


@pytest.mark.asyncio
async def test_llm_fallback_cannot_approve_a_truncated_inn(monkeypatch):
    from unittest.mock import AsyncMock

    from ocr_platform.services import court_regex_jev as service

    text = "РЕШИЛ: Признать Иванова Олега Петровича (ИНН 0123456789013) банкротом."
    fallback = AsyncMock(return_value={"debtor_inn": {
        "value": "012345678901", "confidence": .99, "source": "court_llm_fallback",
    }})
    monkeypatch.setattr(service, "extract_court_llm_fields", fallback)
    output = await service.extract_court_regex_jev(text, {
        "fields": {"debtor_inn": {"required": True}}, "models": {"verification": {"threshold": .95}},
    }, document_id="test", pipeline_run_id="test")
    fallback.assert_awaited_once()
    assert output["debtor_inn"]["value"] is None
    assert review_requirement(1.0, court_field_issues(output))[0] is True

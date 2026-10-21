"""The source is based on current procedure terms, with literal verifier input."""
import pytest

from ocr_platform.services.court_regex_procedure_end_date_source import (
    extract_procedure_end_date_source_candidates as extract,
)


@pytest.mark.parametrize("order,expected", [
    ("Ввести процедуру реализации имущества до 20.03.2027.", "explicit_date"),
    ("Продлить процедуру до «20» марта 2027 года.", "explicit_date"),
    ("Процедуру реструктуризации долгов продлить по 20.03.2027.", "explicit_date"),
    ("Ввести процедуру сроком на шесть месяцев.", "explicit_duration"),
    ("Открыть процедуру на 6 (шесть) месяцев.", "explicit_duration"),
    ("Ввести процедуру на шесть месяцев до 20.03.2027.", "explicit_date"),
    ("Ввести процедуру.", "not_specified"),
])
@pytest.mark.parametrize("heading", ["РЕШИЛ: ", "О П Р Е Д Е Л И Л:\n", "Р Е Ш И Л\n"])
def test_current_terms(order, expected, heading):
    text = heading + order
    result = extract(text)
    assert result.selected.value == expected
    for candidate in result.candidates:
        assert candidate.requires_confirmation is True
        assert candidate.quote == text[candidate.start:candidate.end]


@pytest.mark.parametrize("unrelated", [
    "Представить отчет до 20.03.2027.",
    "Назначить рассмотрение отчета на 20.03.2027.",
    "Финансовому управляющему на шесть месяцев представить документы.",
    "В случае отсутствия ходатайства продлить процедуру до 20.03.2027.",
    "При отсутствии ходатайства продлить процедуру на шесть месяцев.",
    "Ранее суд вводит процедуру до 20.03.2027.",
    "Определением от 01.01.2026 суд вводит процедуру на шесть месяцев.",
    "Согласно статье закона ввести процедуру на шесть месяцев.",
    "Процедура может быть введена на шесть месяцев.",
    "Установить вознаграждение управляющему на шесть месяцев.",
])
def test_absence_uses_the_complete_document(unrelated):
    text = "РЕШИЛ: Ввести процедуру. " + unrelated
    result = extract(text)
    assert result.selected.value == "not_specified"
    assert result.selected.absence is True
    assert result.selected.quote == text
    assert (result.selected.start, result.selected.end) == (0, len(text))


@pytest.mark.parametrize("orders", [
    "Ввести процедуру до 20.03.2027. Продлить процедуру до 21.03.2027.",
    "Ввести процедуру на шесть месяцев. Продлить процедуру на пять месяцев.",
    "Ввести процедуру на 6 (пять) месяцев.",
    "Ввести процедуру до 31.02.2027.",
])
def test_conflict_never_becomes_not_found(orders):
    result = extract("РЕШИЛ: " + orders)
    assert result.selected is None
    assert result.problem == "conflicting_procedure_terms"
    assert all(c.value != "not_specified" for c in result.candidates)


def test_current_reasoning_and_history():
    text = ("Ранее суд вводит процедуру до 01.01.2026. "
            "Суд вводит процедуру на шесть месяцев. "
            "РЕШИЛ: Ввести процедуру. Назначить рассмотрение отчета на 21.03.2027.")
    assert extract(text).selected.value == "explicit_duration"


def test_operative_date_wins_over_historical_duration():
    text = "Ранее суд вводит процедуру на шесть месяцев. РЕШИЛ: Ввести процедуру до 20.03.2027."
    assert extract(text).selected.value == "explicit_date"


def test_no_header_standalone_command():
    assert extract("Ввести процедуру на шесть месяцев.").selected.value == "explicit_duration"


def test_empty_document_is_unconfirmed_absence():
    result = extract("")
    assert result.selected.value == "not_specified"
    assert result.selected.quote == ""
    assert result.selected.requires_confirmation

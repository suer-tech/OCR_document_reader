"""Keep established date choices while fixing unrelated body-date anchors."""
import pytest

from ocr_platform.services.court_decision_dates import (
    extract_decision_date_candidates,
    extract_procedure_start_date_candidates,
)
from ocr_platform.services.court_decision_legacy_rules import extract_procedure_end_date_with_meta as extract

FULL = "Решение в полном объеме изготовлено 24 сентября 2026 года.\n"
RESOLUTIVE = "Резолютивная часть решения объявлена 15 сентября 2026 года.\n"
ORDER = "РЕШИЛ: Ввести процедуру реализации имущества на шесть месяцев."


@pytest.mark.parametrize("header", [FULL + RESOLUTIVE, RESOLUTIVE + FULL])
def test_candidate_priority_is_separate_from_the_preserved_application_policy(header):
    text = "РЕШЕНИЕ\n" + header + ORDER
    assert extract_decision_date_candidates(text).selected.value == "24.09.2026"
    candidate = extract_procedure_start_date_candidates(text).selected
    assert (candidate.value, candidate.role) == ("15.09.2026", "resolutive")
    assert candidate.quote in text
    expected = "24.03.2027" if header.startswith(FULL) else "15.03.2027"
    assert extract(text) == (expected, True)


@pytest.mark.parametrize("resolution", [
    "Резолютивная часть решения объявлена 15.09.2026.",
    "Резолютивная часть 15.09.2026",
    "Резолютивная часть решения оглашена 15 сентября 2026г.",
])
def test_full_text_first_keeps_existing_application_result(resolution):
    assert extract("РЕШЕНИЕ\n" + FULL + resolution + "\n" + ORDER) == ("24.03.2027", True)


@pytest.mark.parametrize("history", [
    "Должник 22 июля 2026 года подал заявление. ",
    "Назначить рассмотрение отчёта на 10 марта 2026 года. ",
])
def test_body_dates_cannot_replace_numeric_date_of_current_act(history):
    text = ("РЕШЕНИЕ\n13.10.2026\nАрбитражный суд в составе судьи. "
            "УСТАНОВИЛ: " + history + ORDER)
    assert extract(text) == ("13.04.2027", True)


@pytest.mark.parametrize("bad", [
    "Резолютивная часть решения объявлена 31 сентября 2026 года.",
    "Резолютивная часть решения объявлена неизвестно когда.",
    "Резолютивная часть решения объявлена 25 сентября 2026 года.",
    RESOLUTIVE + "Резолютивная часть решения объявлена 16 сентября 2026 года.",
])
def test_invalid_or_conflicting_resolutive_date_abstains(bad):
    # The stricter procedure-start candidate remains an experiment until agreed.
    assert extract_procedure_start_date_candidates("РЕШЕНИЕ\n" + FULL + bad + "\n" + ORDER).selected is None


def test_unlabelled_body_date_does_not_supply_a_missing_start():
    text = "РЕШЕНИЕ\nСуд в составе судьи. Должник обратился 22 июля 2026 года. " + ORDER
    assert extract(text) == (None, None)


def test_explicit_end_still_wins_over_any_calculated_end():
    assert extract("РЕШЕНИЕ\n" + FULL + RESOLUTIVE + ORDER +
                   " Продлить процедуру до 01.04.2027.") == ("01.04.2027", False)


REASONING = "На основании пункта 2 статьи 213.24 суд вводит процедуру реализации имущества на срок до 18 марта 2027 года. "
NO_TERM = "РЕШИЛ: Открыть процедуру реализации имущества. Назначить рассмотрение отчёта на 20 марта 2027 года."


def test_current_explicit_reasoning_conclusion_can_supply_omitted_end():
    assert extract("РЕШЕНИЕ\n16.10.2026\nУСТАНОВИЛ: " + REASONING + NO_TERM) == ("18.03.2027", False)


@pytest.mark.parametrize("prefix", [
    "Ранее ", "Если имеются основания, ", "В случае отсутствия отчета ",
    "В другом деле ", "Сторона цитирует: «", "Согласно решению от 01.01.2025 ",
])
def test_historical_quoted_or_conditional_reasoning_does_not_supply_current_end(prefix):
    statement = prefix + "суд вводит процедуру до 18 марта 2027 года. "
    assert extract("РЕШЕНИЕ\n16.10.2026\nУСТАНОВИЛ: " + statement + NO_TERM) == (None, None)


def test_operative_duration_has_priority_over_reasoning_fallback():
    assert extract("РЕШЕНИЕ\n16.10.2026\nУСТАНОВИЛ: " + REASONING + ORDER) == ("16.04.2027", True)


def test_conflicting_current_reasoning_conclusions_abstain():
    text = "РЕШЕНИЕ\n16.10.2026\nУСТАНОВИЛ: " + REASONING
    text += "Суд вводит процедуру реализации имущества до 19 марта 2027 года. " + NO_TERM
    assert extract(text) == (None, None)

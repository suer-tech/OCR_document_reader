"""Procedure orders and report instructions must not share an end-date field."""
import pytest

from ocr_platform.services.court_decision_legacy_rules import (
    extract_early_report_deadline,
    extract_procedure_end_date_with_meta as extract,
)

HEADER = "21 сентября 2026 года. РЕШИЛ: "


@pytest.mark.parametrize("date_text", ["22.03.2027", "22 марта 2027 года", "«22» марта 2027 года"])
def test_explicit_end_after_po_takes_priority_over_month_calculation(date_text):
    text = HEADER + f"Ввести процедуру реализации имущества сроком на шесть месяцев, по {date_text}."
    assert extract(text) == ("22.03.2027", False)


def test_po_in_report_clause_cannot_supply_procedure_end():
    text = HEADER + "Ввести процедуру реализации имущества на шесть месяцев. Представить отчёт по 22.03.2027."
    assert extract(text) == ("21.03.2027", True)


@pytest.mark.parametrize("report", [
    "Финансовому управляющему в срок до 22.03.2027 представить в суд отчёт.",
    "Финансовому управляющему представить отчёт до 22.03.2027.",
    "Назначить рассмотрение отчета финансового управляющего на 22 марта 2027 года на 10 час. 13 мин.",
    "Должнику до 22.03.2027 представить документы управляющему.",
])
@pytest.mark.parametrize("report_first", [False, True])
def test_procedure_date_is_independent_of_report_or_hearing_order(report, report_first):
    procedure = "Ввести процедуру реализации имущества до ««19» марта 2027 года. "
    clauses = report + " " + procedure if report_first else procedure + report
    assert extract(HEADER + clauses) == ("19.03.2027", False)


@pytest.mark.parametrize("term", ["шесть", "6", "6 (шесть)", "шесть (6)"])
def test_duration_is_used_instead_of_report_deadline(term):
    text = HEADER + f"Ввести процедуру реализации имущества сроком на {term} месяцев. "
    text += "Финансовому управляющему до 22.03.2027 представить в суд отчет."
    assert extract(text) == ("21.03.2027", True)


def test_same_date_keeps_calculated_source_when_only_report_is_explicit():
    text = HEADER + "Ввести процедуру реализации имущества на шесть месяцев. "
    text += "Представить отчет до 21.03.2027."
    assert extract(text) == ("21.03.2027", True)


@pytest.mark.parametrize("conditional", [
    "При отсутствии ходатайства срок процедуры считается продленным на шесть месяцев.",
    "При отсутствии ходатайства продлить процедуру на шесть месяцев.",
    "В случае отсутствия ходатайства продлить процедуру на шесть месяцев.",
])
def test_conditional_extension_and_hearing_do_not_supply_a_missing_end(conditional):
    text = HEADER + "Открыть процедуру реализации имущества. "
    text += "Назначить рассмотрение отчета на 22 марта 2027 года на 10 час. 13 мин. " + conditional
    assert extract(text) == (None, None)


@pytest.mark.parametrize("order", [
    "Продлить процедуру реализации имущества до 19.03.2027.",
    "Процедуру реализации имущества продлить до 19.03.2027.",
    "Признать гражданина банкротом с введением процедуры реализации имущества на шесть месяцев.",
])
def test_procedure_order_variants(order):
    expected = ("21.03.2027", True) if "месяцев" in order else ("19.03.2027", False)
    assert extract(HEADER + order) == expected


def test_unrelated_positive_order_does_not_inherit_procedure_context():
    text = HEADER + "Ввести процедуру реализации имущества до 19.03.2027; "
    text += "установить вознаграждение управляющему и выплатить до 22.03.2027."
    assert extract(text) == ("19.03.2027", False)


def test_opredelil_excludes_historical_orders_from_the_current_term():
    text = ("21 сентября 2026 года. Ранее ввести процедуру до 01.08.2026. "
            "О П Р Е Д Е Л И Л:\nВвести процедуру реструктуризации долгов до 19.03.2027.")
    assert extract(text) == ("19.03.2027", False)


def test_numeric_heading_date_can_anchor_duration():
    text = ("РЕШЕНИЕ\n21.09.2026\nСуд в составе судьи. РЕШИЛ: "
            "Ввести процедуру реализации имущества на 6 (шесть) месяцев. "
            "Финансовому управляющему до 22.03.2027 представить отчет.")
    assert extract(text) == ("21.03.2027", True)


@pytest.mark.parametrize("orders", [
    "Ввести процедуру до 19.03.2027. Продлить процедуру до 20.03.2027.",
    "Ввести процедуру на пять месяцев. Продлить процедуру на шесть месяцев.",
    "Ввести процедуру на 6 (пять) месяцев.",
])
def test_conflicting_terms_do_not_fall_back_to_a_report_date(orders):
    assert extract(HEADER + orders + " Представить отчёт до 22.03.2027.") == (None, None)


@pytest.mark.parametrize("days", ["три", "пять", "3", "5"])
def test_existing_ten_day_report_policy_is_unchanged(days):
    text = HEADER + "Ввести процедуру на шесть месяцев. "
    text += f"Представить отчёт не позднее чем за {days} дня до истечения срока."
    end, _ = extract(text)
    assert extract_early_report_deadline(text, end) == ("11.03.2027", "regex_legacy")

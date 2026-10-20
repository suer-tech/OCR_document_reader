"""An explicit end date must survive OCR quote duplication and typography."""
import pytest

from ocr_platform.services.court_decision_legacy_rules import (
    extract_early_report_deadline,
    extract_procedure_end_date_with_meta,
)


@pytest.mark.parametrize("day", [
    "19", "«19»", '"19"', "««19»", "««19»»", '""19""', "“19”", "„19”",
    "« 19 »", "«« 19 »»", "«\u00a019\u00a0»",
])
def test_explicit_date_wins_over_six_month_calculation(day):
    text = ("21 сентября 2026 года. РЕШИЛ: Ввести процедуру на шесть месяцев "
            f"до {day} марта 2027 года.")
    assert extract_procedure_end_date_with_meta(text) == ("19.03.2027", False)


@pytest.mark.parametrize("ending,expected", [
    ("до 19.03.2027", ("19.03.2027", False)),
    ("до 19 марта 2027 г.", ("19.03.2027", False)),
    ("на шесть месяцев", ("21.03.2027", True)),
    ("", (None, None)),
])
def test_existing_date_formats_and_calculation_are_preserved(ending, expected):
    assert extract_procedure_end_date_with_meta(
        "21 сентября 2026 года. РЕШИЛ: Ввести процедуру реализации имущества " + ending
    ) == expected


def test_quote_correction_does_not_change_report_subtraction_rule():
    text = ("21 сентября 2026 года. РЕШИЛ: Ввести процедуру до ««19» марта 2027 года. "
            "Финансовому управляющему заблаговременно представить отчет.")
    end, calculated = extract_procedure_end_date_with_meta(text)
    assert (end, calculated) == ("19.03.2027", False)
    assert extract_early_report_deadline(text, end) == ("09.03.2027", "regex_legacy")

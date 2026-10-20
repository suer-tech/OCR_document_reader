import pytest

from ocr_platform.services.court_regex_financial_manager_full_name import (
    extract_financial_manager_full_name,
)


@pytest.mark.parametrize(
    "clause,expected",
    [
        (
            "Утвердить финансовым управляющим Иванову Анну Сергеевну.",
            "Иванова Анна Сергеевна",
        ),
        (
            "Утвердить финансовым управляющим должника Петрова Ивана Сергеевича (ИНН 123) арбитражного управляющего Сидорова Сергея Петровича.",
            "Сидоров Сергей Петрович",
        ),
        (
            "Финансовым управляющим должника утвердить Иванову Анну Сергеевну.",
            "Иванова Анна Сергеевна",
        ),
        (
            "Утвердить Иванову Анну Сергеевну (ИНН 123) финансовым управляющим имуществом Петрова Ивана Сергеевича.",
            "Иванова Анна Сергеевна",
        ),
        (
            "Утвердить на должность финансового управляющего должника Иванову Анну Сергеевну.",
            "Иванова Анна Сергеевна",
        ),
        ("Утвердить финансовым управляющим Иванову А.С.", "Иванова А.С."),
        ("Утвердить финансовым управляющим Ивановой А.С.", "Иванова А.С."),
        (
            "Утвердить финансовым управляющим Иванову Алсу Сергеевну.",
            "Иванова Алсу Сергеевна",
        ),
        (
            "Утвердить финансовым управляющим Иванову Анну Сергеевну (ИНН 123, адрес: ул. Ленина.\nВ силу закона супруг должника Петров Иван Сергеевич приобретает статус участника.",
            "Иванова Анна Сергеевна",
        ),
        (
            "Утвердить финансовым управляющим Иванову Анну Сергеевну.\nИстребовать сведения в отношении Петрова Ивана Сергеевича.",
            "Иванова Анна Сергеевна",
        ),
    ],
)
def test_appointment_subject_and_exact_offsets(clause, expected):
    text = "Описательная часть.\nРЕШИЛ:\n" + clause
    result = extract_financial_manager_full_name(text)
    assert result.selected.value == expected
    assert result.problem is None
    assert result.requires_confirmation
    assert all(
        c.requires_confirmation and text[c.start : c.end] == c.quote
        for c in result.candidates
    )


@pytest.mark.parametrize(
    "marker", ["РЕШИЛ:", "Р Е Ш И Л\n", "**РЕШИЛ:**", "О П Р Е Д Е Л И Л:"]
)
def test_operative_marker_formats(marker):
    result = extract_financial_manager_full_name(
        "Шапка\n"
        + marker
        + "\nУтвердить финансовым управляющим Иванову Анну Сергеевну."
    )
    assert result.selected.value == "Иванова Анна Сергеевна"


def test_nomination_and_earlier_appointment_do_not_fill_current_omission():
    text = "Определением от 01.01.2020 постановлено: Утвердить финансовым управляющим Петрова Ивана Сергеевича.\nПредставлена кандидатура Сидорова Сергея Петровича.\nРЕШИЛ:\nПризнать должника банкротом.\nФинансовому управляющему представить отчет."
    result = extract_financial_manager_full_name(text)
    assert result.selected is None
    assert result.candidates == ()


def test_fee_award_does_not_appoint_manager():
    result = extract_financial_manager_full_name(
        "РЕШИЛ:\nУтвердить финансовому управляющему Петрову Ивану Сергеевичу вознаграждение."
    )
    assert result.selected is None


def test_historical_quoted_appointment_inside_operative_text_is_not_current():
    result = extract_financial_manager_full_name(
        "РЕШИЛ:\nОпределением от 01.01.2020 постановлено: утвердить финансовым управляющим Иванову Анну Сергеевну.\nПродлить процедуру."
    )
    assert result.selected is None


def test_conflicting_appointments_require_resolution():
    result = extract_financial_manager_full_name(
        "РЕШИЛ:\nУтвердить финансовым управляющим Иванову Анну Сергеевну.\nУтвердить финансовым управляющим Сидорова Сергея Петровича."
    )
    assert result.selected is None
    assert result.problem == "conflicting_appointments"
    assert len(result.candidates) == 2


def test_erroneous_source_appointing_debtor_is_a_conflict():
    result = extract_financial_manager_full_name(
        "РЕШИЛ:\nПризнать несостоятельной (банкротом) Иванову Анну Сергеевну.\nУтвердить финансовым управляющим Иванову Анну Сергеевну."
    )
    assert result.selected is None
    assert result.problem == "source_conflict_manager_is_debtor"
    assert result.candidates[0].value == "Иванова Анна Сергеевна"


def test_typo_is_not_repaired_from_another_name_or_external_knowledge():
    result = extract_financial_manager_full_name(
        "РЕШИЛ:\nУтвердить финансовым управляющим Иванову Викториу Сергеевну."
    )
    assert result.selected.value == "Иванова Викториу Сергеевна"


@pytest.mark.parametrize(
    "text,problem",
    [
        ("", "empty_text"),
        (
            "Утвердить финансовым управляющим Иванову Анну Сергеевну",
            "operative_part_not_found",
        ),
    ],
)
def test_insufficient_source(text, problem):
    result = extract_financial_manager_full_name(text)
    assert result.selected is None
    assert result.problem == problem

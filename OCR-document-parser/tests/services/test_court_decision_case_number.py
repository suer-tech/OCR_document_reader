"""Regression cases for court case identifiers; no OCR or LLM calls."""

import pytest

from ocr_platform.services.court_decision_legacy_rules import extract_case_number


@pytest.mark.parametrize("label", ["Дело", "Дела", "Делу", "Деле", "ДЕЛО"])
@pytest.mark.parametrize("number", ["А05-6/2025", "A41-12345/26", "А0512345/2026"])
def test_preserves_existing_labelled_numbers(label, number):
    assert extract_case_number(f"{label} № {number}") == number


@pytest.mark.parametrize("dash", list("-‐‑‒–—−"))
@pytest.mark.parametrize("spaces", ["", " ", "\u00a0", "\n"])
def test_spaces_and_typographic_dashes(dash, spaces):
    text = f"Дело №А31{spaces}{dash}{spaces}4501/2026"
    assert extract_case_number(text) == "А31-4501/2026"


@pytest.mark.parametrize("header", [
    "РЕШЕНИЕ\n№ А32-52638/2026",
    "Р Е Ш Е Н И Е\nгород Владикавказ № А61-4935/2026",
    "РЕШЕНИЕ\nг. Краснодар № А32-52638/2026",
    "РЕШЕНИЕ\nг. Нижний Новгород № А32-52638/2026",
])
def test_header_without_case_label_precedes_other_case_references(header):
    text = header + "\nСуд в составе судьи рассмотрел заявление. Дело № А99-999/2020."
    expected = "А61-4935/2026" if "Владикавказ" in header else "А32-52638/2026"
    assert extract_case_number(text) == expected


def test_current_label_precedes_later_numbered_header():
    text = "Дело № А05-6/26\n№ А99-999/2020\nСуд в составе судьи"
    assert extract_case_number(text) == "А05-6/26"


@pytest.mark.parametrize("text", [
    "Дело № А75",  # A partial number must not be completed from other data.
    "Дело № А75-123/20260",  # Do not silently truncate the year.
    "Дело № А75-123/2026abc",
    "Арбитражный суд\nДоговор № А12-3456/2026",
    "г. Москва договор № А12-3456/2026",
    "г. Москва. Договор № А12-3456/2026",
    "Суд в составе судьи рассмотрел заявление.\n№ А12-3456/2026",
    "Рассмотрев заявление, суд ссылается на\n№ А12-3456/2026",
    "Арбитражный суд\n" + "Текст\n" * 400 + "№ А12-3456/2026",
    "Ссылка на А12-3456/2026 без метки дела в тексте",
    "Переделу № А12-3456/2026",
    "",
])
def test_does_not_invent_or_take_unlabelled_body_references(text):
    assert extract_case_number(text) is None

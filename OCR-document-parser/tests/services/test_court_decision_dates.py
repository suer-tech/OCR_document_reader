"""Behavioral checks for date roles, evidence, calendar errors and abstention."""
from dataclasses import asdict
import json

import pytest

from ocr_platform.services.court_decision_dates import extract_decision_date_candidates as extract


@pytest.mark.parametrize("phrase", [
    "Полный текст решения изготовлен 05.10.2026",
    "Полный текст определения составлен 05.10.2026",
    "Решение в полном объеме изготовлено 05.10.2026",
    "Определение изготовлено в полном объёме 05.10.2026",
    "В полном объёме решение изготовлено 05.10.2026",
    "В окончательной форме решение изготовлено 05.10.2026",
    "Дата принятия решения в полном объеме 05.10.2026",
    "Решение принято и изготовлено в полном объеме 05.10.2026",
    "Решение изготовлено в полном объеме в совещательной комнате 05.10.2026",
])
def test_full_text_date_precedes_earlier_header_and_resolution_dates(phrase):
    text = ("РЕШЕНИЕ\n01 октября 2026 года\nРезолютивная часть решения объявлена 01.10.2026\n"
            + phrase + "\nСуд в составе судьи. Срок отчёта 30.10.2026")
    result = extract(text)
    assert result.selected.value == "05.10.2026"
    assert result.selected.role == "full_text"


@pytest.mark.parametrize("date", ["05.10.2026", "5 . 10 . 2026", "5 октября 2026г.",
                                 "« 05 » октября 2026 года", "“5” ОКТЯБРЯ 2026 года",
                                 "««05» октября 2026 года"])
def test_date_typography(date):
    assert extract(f"РЕШЕНИЕ\n{date}\nСуд в составе судьи").selected.value == "05.10.2026"


@pytest.mark.parametrize("annotation", [
    "Резолютивная часть 01.10.2026",
    "Резолютивная часть решения принята 01.10.2026",
    "Резолютивная часть составлена 01.10.2026",
    "Резолютивная часть решения оглашена\n01.10.2026",
    "Резолютивная часть определения вынесена в судебном заседании 01.10.2026",
    "Дата объявления резолютивной части — 01.10.2026",
])
def test_resolution_annotation_is_not_act_header(annotation):
    result = extract(f"РЕШЕНИЕ\n05 октября 2026 года\n{annotation}\nСуд в составе судьи")
    assert result.selected.value == "05.10.2026"
    assert {c.value for c in result.candidates if c.role == "resolutive"} == {"01.10.2026"}


def test_resolution_only_title_does_not_invent_full_text_date():
    result = extract("РЕЗОЛЮТИВНАЯ ЧАСТЬ РЕШЕНИЯ\n05 октября 2026 года\nРуководствуясь законом")
    assert result.selected.value == "05.10.2026"
    assert result.selected.role == "act_header"


@pytest.mark.parametrize("metadata", [
    "Дата публикации в КАД: 01.10.2026",
    "Дата поступления: 01.10.2026",
    "Дата регистрации:\n01.10.2026",
    "Дата рождения: 01.10.2026",
    "Дата заседания: 01.10.2026",
    "Срок отчёта 01.10.2026",
    "Срок представления отчета: 01.10.2026",
    "Судебное заседание назначено на 01.10.2026",
    "Сертификат действителен с 01.10.2025 по 01.10.2027",
    "Электронная подпись действительна.\nДанные ЭП: удостоверяющий центр\n"
    "Дата 01.10.2025 10:20:30\nКому выдана подпись",
    "Дата 01.10.2025 10:20:30",
])
def test_metadata_before_heading_does_not_hide_act_date(metadata):
    result = extract(metadata + "\nРЕШЕНИЕ\n05 октября 2026 года\nСуд в составе судьи")
    assert result.selected.value == "05.10.2026"


@pytest.mark.parametrize("text,problem", [
    ("РЕШЕНИЕ\nПолный текст решения изготовлен 31.02.2026\n05.03.2026", "invalid_full_text_date"),
    ("РЕШЕНИЕ\nПолный текст решения изготовлен в неизвестную дату\n05.03.2026", "invalid_full_text_date"),
    ("РЕШЕНИЕ\nПолный текст решения изготовлен 05.10.2026\n"
     "Полный текст решения изготовлен 06.10.2026", "conflicting_full_text_dates"),
    ("РЕШЕНИЕ\nПолный текст решения изготовлен 05.10.2026\n"
     "Резолютивная часть решения объявлена 06.10.2026", "conflicting_resolutive_date"),
    ("РЕШЕНИЕ\n05.10.2026\n06.10.2026\nСуд в составе судьи", "ambiguous_header"),
    ("РЕШЕНИЕ\n29.02.2025\nСуд в составе судьи", "invalid_header_date"),
    ("РЕШЕНИЕ\nСуд в составе судьи.\nЭлектронная подпись действительна.\n"
     "Дата 05.10.2026 10:20:30\nКому выдана подпись", "no_act_date"),
    ("РЕШЕНИЕ Дата 05.10.2026 10:20:30", "no_act_date"),
    ("РЕШЕНИЕ\nСуд в составе судьи. Определение изготовлено в полном объеме 05.10.2026", "no_act_date"),
    ("РЕШЕНИЕ\nР Е Ш И Л:\nСрок отчёта до 05.10.2026", "no_act_date"),
    ("РЕШЕНИЕ\n05.10.2026abc\nСуд в составе судьи", "invalid_header_date"),
    ("", "no_act_date"),
])
def test_abstains_instead_of_guessing(text, problem):
    result = extract(text)
    assert result.selected is None
    assert result.problem == problem


def test_duplicate_full_text_date_is_consistent_and_evidence_is_literal():
    text = ("РЕШЕНИЕ\nПолный текст решения изготовлен\n05 октября 2026 года\n"
            "Решение в полном объеме изготовлено 05.10.2026\nСуд в составе судьи")
    result = extract(text)
    assert result.selected.value == "05.10.2026"
    for candidate in result.candidates:
        assert candidate.quote == text[candidate.start:candidate.end]
    assert "confidence" not in json.dumps(asdict(result))


def test_date_before_court_title_is_supported():
    result = extract("г. Москва\n05 октября 2026г.\nАРБИТРАЖНЫЙ СУД\nРЕШЕНИЕ\nСуд в составе судьи")
    assert result.selected.value == "05.10.2026"


def test_leap_day_and_header_limit():
    assert extract("РЕШЕНИЕ\n29.02.2024\nСуд в составе судьи").selected.value == "29.02.2024"
    assert extract("РЕШЕНИЕ\n" + "Текст\n" * 800 + "05.10.2026").selected is None


def test_truncated_heading_is_not_completed_with_report_or_birth_date():
    text = "Арбитражный суд\nРЕШЕНИЕ\nг. Москва\nСрок отчёта 31.12.2099. Дата рождения 01.01.2001."
    assert extract(text).selected is None


def test_nbsp_inside_quotes_and_short_resolution_annotation():
    text = "РЕШЕНИЕ\n« 05 » октября 2026\nРезолютивная часть 01.10.2026\nСуд в составе судьи"
    assert extract(text.replace(" ", "\u00a0")).selected.value == "05.10.2026"

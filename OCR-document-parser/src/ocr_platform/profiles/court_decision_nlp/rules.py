"""Правила извлечения полей судебного решения (court_name, case_number, inn и т.д.)."""

from __future__ import annotations

import calendar
import re
from datetime import date, timedelta

from .postprocess import normalize_whitespace

CASE_NUMBER_RE = re.compile(r"Дело\s*[№N]?\s*([АA]\d{1,3}-\d+/\d{4})", re.IGNORECASE)
INN_RE = re.compile(r"ИНН\s*[:№]?\s*(\d{10,12})", re.IGNORECASE)
COURT_RE = re.compile(
    r"((?:Арбитражный|АРБИТРАЖНЫЙ)\s+суд\s+[А-ЯЁA-Z][А-ЯЁA-Zа-яёA-Za-z\s\-]+?"
    r"(?:области|края|республики|округа|автономного округа|города\s+Москвы|города\s+Санкт-Петербурга))",
    re.IGNORECASE,
)
DECISION_DATE_RE = re.compile(r"[«\"]?(\d{1,2})[»\"]?\s+([А-Яа-яЁё]+)\s+(\d{4})\s+года")
PROCEDURE_END_RE = re.compile(r"сроком\s+до\s+[«\"]?(\d{1,2})[»\"]?\s+([А-Яа-яЁё]+)\s+(\d{4})\s+года", re.IGNORECASE)
PROCEDURE_TERM_RE = re.compile(
    r"(?:на\s+срок|сроком\s+на)\s+(\d{1,2})\s+(месяц(?:ев|а)?|дн(?:я|ей)|год(?:а|ов)?)",
    re.IGNORECASE,
)
PROCEDURE_TYPE_PATTERNS = [
    re.compile(r"процедур[ауы]\s+реализации\s+имущества\s+гражданина", re.IGNORECASE),
    re.compile(r"реализаци[яи]\s+имущества\s+гражданина", re.IGNORECASE),
    re.compile(r"реструктуризаци[яи]\s+долгов\s+гражданина", re.IGNORECASE),
    re.compile(r"наблюдени[ея]", re.IGNORECASE),
    re.compile(r"конкурсн[а-я]+\s+производств[ао]", re.IGNORECASE),
]
MONTHS = {
    "января": "01",
    "февраля": "02",
    "марта": "03",
    "апреля": "04",
    "мая": "05",
    "июня": "06",
    "июля": "07",
    "августа": "08",
    "сентября": "09",
    "октября": "10",
    "ноября": "11",
    "декабря": "12",
}


def extract_court_name(text: str) -> str | None:
    match = COURT_RE.search(text)
    if not match:
        return None
    court_name = normalize_whitespace(match.group(1))
    return court_name.upper()


def extract_case_number(text: str) -> str | None:
    match = CASE_NUMBER_RE.search(text)
    return match.group(1) if match else None


def extract_inn(text: str) -> str | None:
    match = INN_RE.search(text)
    return match.group(1) if match else None


def _to_iso_date(day: str, month_name: str, year: str) -> str | None:
    month = MONTHS.get(month_name.lower())
    if not month:
        return None
    return f"{year}-{month}-{int(day):02d}"


def _add_months(base_date: date, months: int) -> date:
    total_months = (base_date.month - 1) + months
    year = base_date.year + total_months // 12
    month = total_months % 12 + 1
    day = min(base_date.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def extract_decision_date(text: str) -> str | None:
    match = DECISION_DATE_RE.search(text)
    if not match:
        return None
    return _to_iso_date(match.group(1), match.group(2), match.group(3))


def extract_procedure_end_date(text: str) -> str | None:
    explicit_date = _extract_explicit_procedure_end_date(text)
    if explicit_date:
        return explicit_date
    return _derive_procedure_end_date_from_term(text)


def _extract_explicit_procedure_end_date(text: str) -> str | None:
    match = PROCEDURE_END_RE.search(text)
    if not match:
        return None
    return _to_iso_date(match.group(1), match.group(2), match.group(3))


def _derive_procedure_end_date_from_term(text: str) -> str | None:
    match = PROCEDURE_TERM_RE.search(text)
    if not match:
        return None

    decision_date = extract_decision_date(text)
    if not decision_date:
        return None

    base_date = date.fromisoformat(decision_date)
    value = int(match.group(1))
    unit = match.group(2).lower()

    if unit.startswith("меся"):
        return _add_months(base_date, value).isoformat()
    if unit.startswith("год"):
        return _add_months(base_date, value * 12).isoformat()
    if unit.startswith("дн"):
        return (base_date + timedelta(days=value)).isoformat()
    return None


def extract_procedure_type(text: str) -> str | None:
    for pattern in PROCEDURE_TYPE_PATTERNS:
        match = pattern.search(text)
        if match:
            return normalize_whitespace(match.group(0))
    return None

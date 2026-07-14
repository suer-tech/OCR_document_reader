from __future__ import annotations

import calendar
import re
from datetime import date, timedelta


def normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


CASE_NUMBER_RE = re.compile(r"(?:Дело|Дела|Делу|Деле)\s*[№N]?\s*([АA]\d{1,3}-?\d+/\d{4})", re.IGNORECASE)
DECISION_DATE_RE = re.compile(r"[«\"]?(\d{1,2})[»\"]?\s+([А-Яа-яЁё]+)\s+(\d{4})\s*(?:года|г\.?)", re.IGNORECASE)

REPORT_TIME_RE = re.compile(
    r"рассмотрени[ея]\s+отчета.{1,100}?на\s+([«\"]?\d{1,2}[»\"]?\s+[А-Яа-яЁё]+\s+\d{4}\s*года\s+на\s+\d{1,2}\s*час\.?\s*\d{1,2}\s*мин\.?)",
    re.IGNORECASE | re.DOTALL
)
EXPLICIT_END_DATE_RE = re.compile(
    r"до\s+([«\"]?\d{1,2}[»\"]?\s+[А-Яа-яЁё]+\s+\d{4}\s*(?:года|г\.?)|\d{2}\.\d{2}\.\d{4})",
    re.IGNORECASE
)
TERM_MONTHS_RE = re.compile(
    r"(?:на\s+срок|сроком\s+на|на)\s+([А-Яа-яЁё]+|\d+)\s+месяц(?:а|ев)?",
    re.IGNORECASE
)

WORD_TO_NUM = {
    "один": "1", "одного": "1", "два": "2", "двух": "2", "три": "3", "трех": "3", "трёх": "3",
    "четыре": "4", "четырех": "4", "четырёх": "4", "пять": "5", "пяти": "5",
    "шесть": "6", "шести": "6", "семь": "7", "семи": "7", "восемь": "8", "восьми": "8",
    "девять": "9", "девяти": "9", "десять": "10", "десяти": "10",
    "одиннадцать": "11", "одиннадцати": "11", "двенадцать": "12", "двенадцати": "12"
}

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

def extract_case_number(text: str) -> str | None:
    match = CASE_NUMBER_RE.search(text)
    return match.group(1) if match else None


def _to_ru_date(day: str, month_name: str, year: str) -> str | None:
    month = MONTHS.get(month_name.lower())
    if not month:
        return None
    return f"{int(day):02d}.{month}.{year}"


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
    return _to_ru_date(match.group(1), match.group(2), match.group(3))


def extract_procedure_end_date_with_meta(text: str) -> tuple[str | None, bool | None]:
    match_reshil = re.search(r"Р\s*Е\s*Ш\s*И\s*Л", text, re.IGNORECASE)
    search_text = text[match_reshil.start():] if match_reshil else text

    match3 = REPORT_TIME_RE.search(search_text)
    if match3:
        return normalize_whitespace(match3.group(1)), False

    match2 = EXPLICIT_END_DATE_RE.search(search_text)
    if match2:
        val = match2.group(1)
        if re.match(r"\d{2}\.\d{2}\.\d{4}", val):
            return val, False
        m = re.match(r"[«\"]?(\d{1,2})[»\"]?\s+([А-Яа-яЁё]+)\s+(\d{4})\s*(?:года|г\.?)", val, re.IGNORECASE)
        if m:
            day, month_name, year = m.groups()
            month_num = MONTHS.get(month_name.lower())
            if month_num:
                return f"{int(day):02d}.{month_num}.{year}", False

    match1 = TERM_MONTHS_RE.search(search_text)
    if match1:
        term = match1.group(1).lower()
        if term in WORD_TO_NUM:
            term = WORD_TO_NUM[term]
        if term.isdigit():
            decision_date = extract_decision_date(text)
            if decision_date:
                base_date = _parse_date_str(decision_date)
                if base_date:
                    end_date = _add_months(base_date, int(term))
                    return end_date.strftime("%d.%m.%Y"), True
            return term, True

    return None, None


# ---------- Заблаговременное предоставление отчёта ФУ ----------

EARLY_REPORT_EXPLICIT_DATE_RE = re.compile(
    r"(?:обязать\s+)?финансов\w+\s+управляющ\w+\s+"
    r"до\s+([«\"]?\d{1,2}[»\"]?\s+[А-Яа-яЁё]+\s+\d{4}\s*года|\d{2}\.\d{2}\.\d{4})"
    r"\s+(?:представить|направить)",
    re.IGNORECASE | re.DOTALL,
)

EARLY_REPORT_DAYS_BEFORE_RE = re.compile(
    r"не\s+позднее\s+(?:чем\s+)?за\s+(\w+)\s+(?:рабочих\s+)?(?:дней|дня)\s+"
    r"до\s+истечения\s+срока",
    re.IGNORECASE,
)

EARLY_REPORT_ADVANCE_RE = re.compile(
    r"(?:обязать\s+)?финансов\w+\s+управляющ\w+\s+заблаговременно.{1,200}?представить",
    re.IGNORECASE | re.DOTALL,
)

EARLY_REPORT_SUBMIT_DEADLINE_RE = re.compile(
    r"(?:отчет|документ)\w*.{1,300}?представить.{1,100}?"
    r"(?:в\s+срок\s+)?до\s+"
    r"(?:[«\"]?\d{1,2}[»\"]?\s+[А-Яа-яЁё]+\s+\d{4}\s*года|\d{2}\.\d{2}\.\d{4})",
    re.IGNORECASE | re.DOTALL,
)

DEFAULT_ADVANCE_DAYS = 10


def _parse_date_str(date_str: str) -> date | None:
    import re as _re
    if _re.match(r"\d{2}\.\d{2}\.\d{4}$", date_str):
        parts = date_str.split(".")
        try:
            return date(int(parts[2]), int(parts[1]), int(parts[0]))
        except ValueError:
            return None
    try:
        return date.fromisoformat(date_str)
    except ValueError:
        return None


def _subtract_days_from_date(date_str: str, days: int) -> str | None:
    base = _parse_date_str(date_str)
    if base is None:
        return None
    result = base - timedelta(days=days)
    return result.strftime("%d.%m.%Y")


def extract_early_report_deadline(text: str, procedure_end_date: str | None) -> tuple[str | None, str | None]:
    if not procedure_end_date:
        return None, None

    match_reshil = re.search(r"Р\s*Е\s*Ш\s*И\s*Л", text, re.IGNORECASE)
    search_text = text[match_reshil.start():] if match_reshil else text

    has_requirement = (
        EARLY_REPORT_EXPLICIT_DATE_RE.search(search_text) is not None
        or EARLY_REPORT_DAYS_BEFORE_RE.search(search_text) is not None
        or EARLY_REPORT_ADVANCE_RE.search(search_text) is not None
        or EARLY_REPORT_SUBMIT_DEADLINE_RE.search(search_text) is not None
    )

    if not has_requirement:
        return None, None

    return _subtract_days_from_date(procedure_end_date, DEFAULT_ADVANCE_DAYS), "regex_legacy"

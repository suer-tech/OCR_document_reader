from __future__ import annotations

import calendar
import re
from datetime import date, timedelta

from .postprocess import normalize_whitespace

CASE_NUMBER_RE = re.compile(r"(?:Дело|Дела|Делу|Деле)\s*[№N]?\s*([АA]\d{1,3}-?\d+/\d{2,4})", re.IGNORECASE)
INN_RE = re.compile(r"ИНН\s*[:№]?\s*(\d{10,12})", re.IGNORECASE)
COURT_RE = re.compile(
    r"((?:Арбитражный|АРБИТРАЖНЫЙ)\s+суд\s+[А-ЯЁA-Z][А-ЯЁA-Zа-яёA-Za-z\s\-]+?"
    r"(?:области|края|республики|округа|автономного округа|города\s+Москвы|города\s+Санкт-Петербурга))",
    re.IGNORECASE,
)
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


def extract_procedure_end_date(text: str) -> str | None:
    return extract_procedure_end_date_with_meta(text)[0]


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


def extract_procedure_type(text: str) -> str | None:
    for pattern in PROCEDURE_TYPE_PATTERNS:
        match = pattern.search(text)
        if match:
            return normalize_whitespace(match.group(0))
    return None


# ---------- Заблаговременное предоставление отчёта ФУ ----------

# Паттерн 1: явная дата предоставления документов ФУ
# «обязать финансового управляющего до 09 ноября 2026 года представить суду документы»
EARLY_REPORT_EXPLICIT_DATE_RE = re.compile(
    r"(?:обязать\s+)?финансов\w+\s+управляющ\w+\s+"
    r"до\s+([«\"]?\d{1,2}[»\"]?\s+[А-Яа-яЁё]+\s+\d{4}\s*года|\d{2}\.\d{2}\.\d{4})"
    r"\s+(?:представить|направить)",
    re.IGNORECASE | re.DOTALL,
)

# Паттерн 2: «за N дней до истечения срока»
EARLY_REPORT_DAYS_BEFORE_RE = re.compile(
    r"не\s+позднее\s+(?:чем\s+)?за\s+(\w+)\s+(?:рабочих\s+)?(?:дней|дня)\s+"
    r"до\s+истечения\s+срока",
    re.IGNORECASE,
)

# Паттерн 3: «заблаговременно представить»
EARLY_REPORT_ADVANCE_RE = re.compile(
    r"(?:обязать\s+)?финансов\w+\s+управляющ\w+\s+заблаговременно\s+представить",
    re.IGNORECASE | re.DOTALL,
)

# Паттерн 4: «отчет ... представить ... в срок до DATE»
# «отчет о результатах реализации имущества ... представить в арбитражный суд в срок до 30 сентября 2026 года»
EARLY_REPORT_SUBMIT_DEADLINE_RE = re.compile(
    r"(?:отчет|документ)\w*.{1,300}?представить.{1,100}?"
    r"(?:в\s+срок\s+)?до\s+"
    r"(?:[«\"]?\d{1,2}[»\"]?\s+[А-Яа-яЁё]+\s+\d{4}\s*года|\d{2}\.\d{2}\.\d{4})",
    re.IGNORECASE | re.DOTALL,
)

WORD_TO_NUM_DAYS: dict[str, int] = {
    "один": 1, "одного": 1, "два": 2, "двух": 2, "три": 3, "трёх": 3, "трех": 3,
    "четыре": 4, "четырёх": 4, "четырех": 4, "пять": 5, "пяти": 5,
    "шесть": 6, "шести": 6, "семь": 7, "семи": 7, "восемь": 8, "восьми": 8,
    "девять": 9, "девяти": 9, "десять": 10, "десяти": 10,
    "пятнадцать": 15, "пятнадцати": 15, "двадцать": 20, "двадцати": 20,
    "тридцать": 30, "тридцати": 30,
}

DEFAULT_ADVANCE_DAYS = 10


def _parse_date_str(date_str: str) -> date | None:
    """Распарсить дату из строки ДД.ММ.ГГГГ или ГГГГ-ММ-ДД."""
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
    """Вычесть N дней из даты. Возвращает строку ДД.ММ.ГГГГ."""
    base = _parse_date_str(date_str)
    if base is None:
        return None
    result = base - timedelta(days=days)
    return result.strftime("%d.%m.%Y")


def extract_early_report_deadline(text: str, procedure_end_date: str | None) -> str | None:
    """Извлечь дату заблаговременного предоставления отчёта ФУ.

    Ищет только в блоке после «РЕШИЛ:».
    Если найден любой из паттернов, требующих заблаговременного предоставления,
    возвращает procedure_end_date минус 10 дней (формат ДД.ММ.ГГГГ).
    Если требования нет или нет даты завершения — возвращает None.
    """
    if not procedure_end_date:
        return None

    match_reshil = re.search(r"Р\s*Е\s*Ш\s*И\s*Л", text, re.IGNORECASE)
    search_text = text[match_reshil.start():] if match_reshil else text

    has_requirement = (
        EARLY_REPORT_EXPLICIT_DATE_RE.search(search_text) is not None
        or EARLY_REPORT_DAYS_BEFORE_RE.search(search_text) is not None
        or EARLY_REPORT_ADVANCE_RE.search(search_text) is not None
        or EARLY_REPORT_SUBMIT_DEADLINE_RE.search(search_text) is not None
    )

    if not has_requirement:
        return None

    return _subtract_days_from_date(procedure_end_date, DEFAULT_ADVANCE_DAYS)


def extract_motivating_part(text: str) -> str | None:
    """Извлечь мотивирующую часть судебного решения.

    Ищет текст между маркерами 'УСТАНОВИЛ' и 'РЕШИЛ'.
    """
    match_ustanovil = re.search(r"У\s*С\s*Т\s*А\s*Н\s*О\s*В\s*И\s*Л", text, re.IGNORECASE)
    if not match_ustanovil:
        return None

    start_pos = match_ustanovil.end()
    match_reshil = re.search(r"Р\s*Е\s*Ш\s*И\s*Л", text[start_pos:], re.IGNORECASE)
    if not match_reshil:
        return None

    end_pos = start_pos + match_reshil.start()
    motivating_text = text[start_pos:end_pos]

    # Очистка начальных спецсимволов и концевых пробелов
    motivating_text = re.sub(r"^[\s:,\.\-–—\(\)]+", "", motivating_text)
    return motivating_text.strip() or None


def extract_resolutive_part(text: str) -> str | None:
    """Извлечь резолютивную часть судебного решения.

    Ищет текст после маркера 'РЕШИЛ' и до 'Электронная подпись действительна'.
    """
    match_reshil = re.search(r"Р\s*Е\s*Ш\s*И\s*Л", text, re.IGNORECASE)
    if not match_reshil:
        return None

    start_pos = match_reshil.end()
    match_sig = re.search(r"электронная\s+подпись\s+действительна", text[start_pos:], re.IGNORECASE)
    if match_sig:
        end_pos = start_pos + match_sig.start()
        resolutive_text = text[start_pos:end_pos]
    else:
        resolutive_text = text[start_pos:]

    # Очистка начальных спецсимволов и концевых пробелов
    resolutive_text = re.sub(r"^[\s:,\.\-–—\(\)]+", "", resolutive_text)
    return resolutive_text.strip() or None



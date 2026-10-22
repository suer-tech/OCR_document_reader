from __future__ import annotations

import calendar
import re
from datetime import date, timedelta


def normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


_CASE_DASHES = "-‐‑‒–—−"
_CASE_SUFFIX = (
    rf"(?:[{_CASE_DASHES}]\d+[А-Яа-яЁёA-Za-z]*)*"
    rf"(?:[^\S\r\n]*(?:«[^\S\r\n]*[А-ЯЁA-Z]{{1,3}}[^\S\r\n]*»|"
    rf'"[^\S\r\n]*[А-ЯЁA-Z]{{1,3}}[^\S\r\n]*"|'
    rf"\([^\S\r\n]*\d+(?:[{_CASE_DASHES}]\d+)*[^\S\r\n]*\)))?"
)
_CASE_NUMBER = rf"[АA]\d{{1,3}}(?:\s*[{_CASE_DASHES}]\s*\d+|\d+)(?:\s*[{_CASE_DASHES}]\s*\d+)*/\d{{2,4}}(?!\w){_CASE_SUFFIX}"
CASE_NUMBER_RE = re.compile(
    rf"\b(?:Дело|Дела|Делу|Деле)\s*[№N]?\s*({_CASE_NUMBER})",
    re.IGNORECASE,
)
# Without a case label, accept only a number line in the document header.
_HEADER_CASE_NUMBER_RE = re.compile(
    rf"^[ \t]*(?:(?:г\.|город)[ \t]+[А-Яа-яЁё‐-]+"
    rf"(?:[ \t]+(?-i:[А-ЯЁ])[А-Яа-яЁё‐-]+){{0,2}}[ \t]+)?[№N][ \t]*"
    rf"([АA]\d{{1,3}}\s*[{_CASE_DASHES}]\s*\d+(?:\s*[{_CASE_DASHES}]\s*\d+)*/\d{{2,4}}(?!\w){_CASE_SUFFIX})",
    re.IGNORECASE | re.MULTILINE,
)
_CASE_HEADER_END_RE = re.compile(
    r"\b(?:в\s+составе|рассмотрев|рассмотрел|установил[аи]?|руководствуясь)\b",
    re.IGNORECASE,
)
DECISION_DATE_RE = re.compile(r"[«\"]?(\d{1,2})[»\"]?\s+([А-Яа-яЁё]+)\s+(\d{4})\s*(?:года|г\.?)", re.IGNORECASE)

_WRITTEN_END_DATE = (
    r'[«"“„]*\s*(\d{1,2})\s*[»"”]*\s+([А-Яа-яЁё]+)\s+(\d{4})\s*(?:года|г\.?)'
)
EXPLICIT_END_DATE_RE = re.compile(
    rf"\b(?:до|по)\s+({_WRITTEN_END_DATE}|\d{{2}}\.\d{{2}}\.\d{{4}})",
    re.IGNORECASE
)
TERM_MONTHS_RE = re.compile(
    r"(?:на\s+срок|сроком(?:\s+на)?|продолжительностью|длительностью|на)\s+([А-Яа-яЁё]+|\d+)"
    r"(?:\s*\(\s*([А-Яа-яЁё]+|\d+)\s*\))?\s+месяц(?:а|ев)?",
    re.IGNORECASE
)
_PROCEDURE = re.compile(
    r"\b(?:процедур[а-яё]*|реализаци[а-яё]*\s+имущества|реструктуризаци[а-яё]*\s+долгов)\b", re.I,
)
_PROCEDURE_ORDERS = {"ввести", "введением", "открыть", "продлить", "установить", "определить", "вводится", "вводит"}
_ORDER = re.compile(
    r"\b(?:ввести|введением|открыть|продлить|установить|определить|вводится|вводит|"
    r"назначить|назначено|рассмотрени[ея]|обязать|утвердить|представить|предоставить|"
    r"направить|опубликовать|взыскать|выплатить|отч[её]т[а-яё]*|заседани[а-яё]*)\b", re.I,
)
_CONDITIONAL_EXTENSION = re.compile(
    r"\b(?:при\s+отсутствии|в\s+случае|считается\s+продл[её]н|"
    r"будет\s+считаться|автоматическ[а-яё]*\s+продл[её]н)", re.I,
)
_OTHER_TERM_SUBJECT = re.compile(r"\b(?:финансовому|арбитражному)\s+управляющему\b|\bдолжнику\b", re.I)
_OPERATIVE_HEADING = r"(?:р\s*е\s*ш\s*и\s*л|о\s*п\s*р\s*е\s*д\s*е\s*л\s*и\s*л)"

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
    """Read the current case label or a numbered header; keep the source year."""
    match = CASE_NUMBER_RE.search(text)
    header = text[:2000]
    header_end = _CASE_HEADER_END_RE.search(header)
    if header_end:
        header = header[:header_end.start()]
    header_match = _HEADER_CASE_NUMBER_RE.search(header)
    if header_match and (match is None or header_match.start(1) < match.start(1)):
        match = header_match
    if match is None:
        return None
    value = re.sub(r"\s+", "", match.group(1))
    return re.sub(rf"[{_CASE_DASHES}]", "-", value)


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


def _procedure_term_context(text: str, position: int) -> bool:
    """Tie a term to a procedure order, not to a subsequent report instruction."""
    prefix = text[max(0, position-1000):position]
    orders = list(_ORDER.finditer(prefix))
    if not orders or orders[-1].group().casefold() not in _PROCEDURE_ORDERS:
        return False
    order = orders[-1]
    before = prefix[max(0, order.start()-250):order.start()]
    before = re.split(r"[.;!?]", before)[-1]
    after = prefix[order.start():]
    context = before + after
    return (bool(_PROCEDURE.search(context))
            and not _CONDITIONAL_EXTENSION.search(context)
            and not _OTHER_TERM_SUBJECT.search(after))


def _explicit_procedure_dates(text: str, *, reasoning_only: bool = False) -> list[str]:
    values = []
    for match in EXPLICIT_END_DATE_RE.finditer(text):
        if not _procedure_term_context(text, match.start()):
            continue
        if reasoning_only:
            prefix = text[:match.start()]
            order = re.search(r"\bсуд\s+(?:вводит|продлевает)\s+процедур[а-яё]*\b[^.!?;:]{0,220}$", prefix, re.I)
            if not order:
                continue
            context = prefix[max(0, order.start()-250):order.start()]
            context = re.split(r"[.!?]\s+|\n\s*\n", context)[-1]
            if (re.search(r"\b(?:ранее|другой|если|например|цит[а-яё]*|в\s+случае|при\s+отсутствии|"
                          r"другом\s+деле|решени[а-яё]*\s+от|определени[а-яё]*\s+от)\b", context, re.I)
                    or re.search(r'[«“"][^»”"]*$', context)):
                continue
        val = match.group(1)
        if not re.fullmatch(r"\d{2}\.\d{2}\.\d{4}", val):
            written = re.fullmatch(_WRITTEN_END_DATE, val, re.I)
            if not written:
                continue
            val = _to_ru_date(*written.groups())
        if val and _parse_date_str(val):
            values.append(val)
    return values


def extract_procedure_end_date_with_meta(text: str) -> tuple[str | None, bool | None]:
    headings = [match for pattern in (
        rf"\b{_OPERATIVE_HEADING}\s*:",
        rf"(?m)^[^\S\r\n]*{_OPERATIVE_HEADING}[^\S\r\n]*:?[^\S\r\n]*$",
    ) if (match := re.search(pattern, text, re.I))]
    search_text = text[min(match.end() for match in headings):] if headings else text

    explicit = _explicit_procedure_dates(search_text)
    if explicit:
        return (explicit[0], False) if len(set(explicit)) == 1 else (None, None)

    terms = []
    for match1 in TERM_MONTHS_RE.finditer(search_text):
        if not _procedure_term_context(search_text, match1.start()):
            continue
        term = match1.group(1).casefold()
        if term in WORD_TO_NUM:
            term = WORD_TO_NUM[term]
        if match1.group(2):
            duplicate = match1.group(2).casefold()
            duplicate = WORD_TO_NUM.get(duplicate, duplicate)
            if duplicate != term:
                continue
        if term.isdigit() and int(term) > 0:
            terms.append(term)
    if terms and len(set(terms)) == 1:
        term = terms[0]
        from ocr_platform.services.court_decision_dates import extract_decision_date_candidates

        dates = extract_decision_date_candidates(text, require_act_title=False)
        candidate = dates.selected
        decision_date = None
        if candidate:
            if candidate.role == "act_header":
                # A numeric current-act date must precede unrelated body dates.
                decision_date = candidate.value
            else:
                # Keep the existing full/resolutive convention pending operator agreement.
                decision_date = extract_decision_date(text) or candidate.value
        if decision_date:
            base_date = _parse_date_str(decision_date)
            if base_date:
                end_date = _add_months(base_date, int(term))
                return end_date.strftime("%d.%m.%Y"), True

    if not terms and headings:
        reasoning = text[:min(match.start() for match in headings)]
        explicit = _explicit_procedure_dates(reasoning, reasoning_only=True)
        if len(set(explicit)) == 1:
            return explicit[0], False

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

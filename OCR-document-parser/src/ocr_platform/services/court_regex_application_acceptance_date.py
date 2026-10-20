"""Literal candidates for acceptance of the bankruptcy application.

Pure text extraction. Every candidate requires downstream verification (JEV).
No operator values, document identifiers, OCR, model calls, or inferred dates.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

_MONTHS = {"января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5,
           "июня": 6, "июля": 7, "августа": 8, "сентября": 9, "октября": 10,
           "ноября": 11, "декабря": 12}
_DATE = re.compile(
    r"(?<![\w.])(?P<d>\d{1,2})\s*[.\-/]\s*(?P<m>\d{1,2})\s*[.\-/]\s*(?P<y>\d{4})(?!\d)|"
    r'(?<!\w)(?:[«"“”„]\s*)?(?P<wd>\d{1,2})\s*[»"“”]*\s*'
    r"(?P<wm>" + "|".join(_MONTHS) + r")\s+(?P<wy>\d{4})(?!\d)", re.IGNORECASE)
_DECREE = re.compile(r"\bопределением\b", re.IGNORECASE)
_ADMIT = re.compile(
    r"\bпринят[оа]\s+(?:судом\s+)?(?:к\s+)?(?:производству|рассмотрению)\b|"
    r"\bк\s+производству\s+(?:суда\s+)?(?:было\s+)?принято\b|"
    r"\bсуд\s+принял\s+(?:указанное\s+)?заявление\s+к\s+производству\b|"
    r"\bзаявление\s+принято(?=\s*(?:[,;]|и)\s*возбужд[её]но)", re.IGNORECASE)
_OPEN = re.compile(r"\b(?:возбужд[её]но|суд\s+возбудил)\s+(?:производство\s+по\s+(?:настоящему\s+)?делу|дело)\b", re.IGNORECASE)
_BAD = re.compile(r"оставлен[оа]\s+без\s+движения|возвращен[оа]|возвращён[оа]|отказано|"
                  r"прекращено|отменен[оа]|отменён[оа]|приостановлено", re.IGNORECASE)
_OTHER_APPLICATION = re.compile(r"о\s+включении|в\s+реестр|об\s+оспаривании|о\s+признании\s+сделк|"
                                r"о\s+процессуальном|о\s+замене|о\s+возмещении", re.IGNORECASE)
_CREDITOR_APPLICATION = re.compile(
    r"заявлени[еяю]\s+(?:ПАО\b|АО\b|ООО\b|банка\b|(?:межрайонной\s+)?ИФНС\b|"
    r"ФНС\b|кредитора\b|конкурсного\s+кредитора\b|уполномоченного\s+органа\b|"
    r"другого\b|иного\b)", re.IGNORECASE)
_CASE = re.compile(r"[АA]\s*(\d{1,3})\s*[-–]\s*(\d+)\s*/\s*(\d{2,4})", re.IGNORECASE)
_INN = re.compile(r"\bинн\s*[:№]?\s*(\d{12})(?!\d)", re.IGNORECASE)
_SETUP = re.compile(r"у\s*с\s*т\s*а\s*н\s*о\s*в\s*и\s*л\s*[а:]?", re.IGNORECASE)


@dataclass(frozen=True)
class ApplicationAcceptanceDateCandidate:
    value: str
    quote: str
    start: int
    end: int
    date_start: int
    date_end: int
    role: str
    requires_verification: bool = True


@dataclass(frozen=True)
class ApplicationAcceptanceDateResult:
    selected: ApplicationAcceptanceDateCandidate | None
    candidates: tuple[ApplicationAcceptanceDateCandidate, ...]
    problem: str = ""
    rejections: tuple[str, ...] = ()


def _value(found: re.Match) -> str | None:
    try:
        if found["d"]:
            value = date(int(found["y"]), int(found["m"]), int(found["d"]))
        else:
            value = date(int(found["wy"]), _MONTHS[found["wm"].casefold()], int(found["wd"]))
        return value.strftime("%d.%m.%Y")
    except ValueError:
        return None


def _cases(text: str) -> set[tuple[str, str, str]]:
    return {(m[1], m[2], m[3][-2:]) for m in _CASE.finditer(text)}


def _clause_end(text: str, start: int, limit: int) -> int:
    # Initials, city abbreviations, dates and paragraph numbers are not sentences.
    depth = 0
    abbreviations = {"г", "гг", "р", "гор", "обл", "ул", "д", "к", "кв", "помещ",
                     "пер", "пр", "просп", "тер", "стр", "ст", "п", "руб"}
    cursor = start
    for dot in re.finditer(r"[.;]", text[start:limit]):
        position = start + dot.start()
        for char in text[cursor:position]:
            depth += (char == "(") - (char == ")")
        cursor = position + 1
        if depth > 0 or text[position] == ";":
            continue
        before = re.search(r"([\w]+)$", text[max(0, start-30):position])
        after = text[position + 1:limit]
        if (before and len(before[1]) > 1 and before[1].casefold() not in abbreviations
                and not after[:1].isdigit()):
            return position + 1
    return limit


def extract_application_acceptance_date_candidates(text: str) -> ApplicationAcceptanceDateResult:
    """Return one explicit date, or abstain on missing/competing source evidence.

    A date must directly belong to a dated order reference or a dated affirmative
    acceptance/opening clause. Different accepted applications are deliberately
    not resolved by first/last date.
    """
    if not text:
        return ApplicationAcceptanceDateResult(None, (), "no_explicit_acceptance")
    setup = _SETUP.search(text)
    header = text[:setup.start()] if setup else text[:2500]
    header_inns = set(_INN.findall(header))
    header_cases = _cases(header)
    decrees = list(_DECREE.finditer(text))
    candidates = []
    rejected = []
    for index, decree in enumerate(decrees):
        limit = min(len(text), decree.end() + 1200,
                    decrees[index + 1].start() if index + 1 < len(decrees) else len(text))
        date = _DATE.search(text, decree.end(), min(limit, decree.end() + 220))
        prefix = text[decree.end():date.start()] if date else ""
        if re.search(r"закон|кодекс|стать|согласно|соответствии", prefix, re.IGNORECASE):
            rejected.append("date_in_legal_reference")
            continue
        quote_start = decree.start()
        tail_start = date.end() if date else decree.end()
        event_in_prefix = re.search(r"заявлен|приня|назнач|поступ|возбужд|оставл|рассмотр|представ", prefix, re.IGNORECASE)
        if not date or event_in_prefix or not re.fullmatch(r'[\s\w«»".,()–—-]{0,190}(?:\bот\s*)?', prefix, re.IGNORECASE):
            # The decree's date can precede its noun: "DATE определением суда".
            preceding = [m for m in _DATE.finditer(text, max(0, decree.start()-55), decree.start())
                         if re.fullmatch(r"\s*(?:г\.)?\s*", text[m.end():decree.start()], re.IGNORECASE)]
            if not preceding:
                continue
            date = preceding[-1]
            quote_start = date.start()
            tail_start = decree.end()
        # Skip only the year suffix; this prevents joining a later sentence.
        suffix = re.match(r"\s*(?:года\b|г\.)?[\s,]*", text[tail_start:limit], re.IGNORECASE)
        content_start = tail_start + (suffix.end() if suffix else 0)
        first_end = _clause_end(text, content_start, limit)
        # An action can follow a long debtor description containing birth dates,
        # abbreviated addresses and initials. The date is still the decree date.
        segment = text[content_start:limit]
        action = _ADMIT.search(segment) or _OPEN.search(segment)
        if not action or action.start() > 850:
            continue
        before_action = segment[:action.start()]
        if _BAD.search(before_action):
            rejected.append("negative_or_other_procedural_order")
            continue
        # Do not cross an unrelated full sentence before an acceptance action.
        if first_end < content_start + action.start():
            rejected.append("acceptance_not_in_dated_clause")
            continue
        action_end = content_start + action.end()
        end = _clause_end(text, action_end, limit)
        context = text[content_start:end]
        if _OTHER_APPLICATION.search(context):
            rejected.append("different_application_kind")
            continue
        if not re.search(r"заявлени[еяю]|банкрот|несостоятель|по\s+настоящему\s+делу", context, re.IGNORECASE):
            continue
        if action.re is _OPEN and not re.search(r"банкрот|несостоятель|по\s+настоящему\s+делу|"
                                             r"обоснованности\s+заявления\s+должника", context, re.IGNORECASE):
            continue
        context_inns = set(_INN.findall(context))
        if header_inns and context_inns and context_inns.isdisjoint(header_inns):
            rejected.append("different_debtor_inn")
            continue
        context_cases = _cases(context)
        if header_cases and context_cases and context_cases.isdisjoint(header_cases):
            rejected.append("different_case_number")
            continue
        value = _value(date)
        if value is None:
            rejected.append("invalid_calendar_date")
            continue
        # A creditor's application and the debtor's application are independent;
        # do not transfer an opening date through a historical creditor narrative.
        role = "application_accepted" if action.re is _ADMIT else "bankruptcy_case_opened"
        candidates.append(ApplicationAcceptanceDateCandidate(
            value, text[quote_start:end], quote_start, end,
            date.start(), date.end(), role))

    # A trailing parenthesis can name the order for the immediately preceding
    # opening clause. It must not borrow a law date or a date of filing.
    trailing = re.compile(r"\bкоторое\s+судом\s+было\s+принято\s*,\s*"
                          r"возбужд[её]но\s+производство\s+по\s+делу[^!?;]{0,350}?"
                          r"\(\s*определение\s+от\s*", re.IGNORECASE)
    for reference in trailing.finditer(text):
        date = _DATE.match(text, reference.end())
        if not date or not re.match(r"\s*(?:г\.)?\s*\)", text[date.end():]):
            continue
        context = text[reference.start():date.end()]
        if not re.search(r"банкрот|несостоятель", context, re.IGNORECASE) or _OTHER_APPLICATION.search(context):
            continue
        cases = _cases(context)
        if header_cases and cases and header_cases.isdisjoint(cases):
            rejected.append("different_case_number")
            continue
        value = _value(date)
        if value:
            end = date.end() + re.match(r"\s*(?:г\.)?\s*\)", text[date.end():]).end()
            candidates.append(ApplicationAcceptanceDateCandidate(
                value, text[reference.start():end], reference.start(), end,
                date.start(), date.end(), "bankruptcy_case_opened"))

    # The source can state the acceptance date without naming its order:
    # "DATE заявление принято судом к производству, возбуждено дело ...".
    for date in _DATE.finditer(text):
        if any(c.date_start == date.start() for c in candidates):
            continue
        suffix = re.match(r"\s*(?:года\b|г\.)?[\s,]*", text[date.end():], re.IGNORECASE)
        start = date.end() + suffix.end()
        if not re.match(r"заявление\b", text[start:], re.IGNORECASE):
            continue
        end = _clause_end(text, start, min(len(text), start+850))
        context = text[start:end]
        action = _ADMIT.search(context)
        if not action or re.search(r"подано|поступило|оставлено|возвращено", context[:action.start()], re.IGNORECASE):
            continue
        if not _OPEN.search(context) or _OTHER_APPLICATION.search(context):
            continue
        if not re.search(r"банкрот|несостоятель", context, re.IGNORECASE):
            continue
        inns, cases = set(_INN.findall(context)), _cases(context)
        if (header_inns and inns and header_inns.isdisjoint(inns)) or (header_cases and cases and header_cases.isdisjoint(cases)):
            continue
        value = _value(date)
        if value:
            candidates.append(ApplicationAcceptanceDateCandidate(
                value, text[date.start():end], date.start(), end,
                date.start(), date.end(), "dated_application_accepted"))
    unique = tuple(dict.fromkeys(candidates))
    if len({item.value for item in unique}) > 1:
        return ApplicationAcceptanceDateResult(None, unique, "competing_acceptance_dates", tuple(rejected))
    if any(_CREDITOR_APPLICATION.search(item.quote) for item in unique):
        return ApplicationAcceptanceDateResult(None, unique, "creditor_application_requires_resolution", tuple(rejected))
    if unique:
        return ApplicationAcceptanceDateResult(unique[0], unique, rejections=tuple(rejected))
    return ApplicationAcceptanceDateResult(None, (), rejected[0] if rejected else "no_explicit_acceptance", tuple(rejected))

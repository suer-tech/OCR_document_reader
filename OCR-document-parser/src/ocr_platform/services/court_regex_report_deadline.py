"""Source-bound report deadline candidates; preserve the confirmed ten-day policy.

Explicit report dates are exposed separately for review. They do not override
the existing calculation until that field policy is agreed with the operator.
No returned candidate represents classifier confirmation.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta

from .court_decision_dates import MONTHS
from .court_decision_legacy_rules import extract_early_report_deadline

_OPERATIVE = re.compile(
    r"\b(?:р[ \t]*е[ \t]*ш[ \t]*и[ \t]*л|"
    r"о[ \t]*п[ \t]*р[ \t]*е[ \t]*д[ \t]*е[ \t]*л[ \t]*и[ \t]*л)[ \t]*(?::|$)", re.I | re.M,
)
_ADVANCE = re.compile(
    r"\bзаблаговременно\b|\bза\s+(?:\d{1,2}|один|одного|два|двух|три|тр[её]х|"
    r"четыре|четыр[её]х|пять|пяти|шесть|шести|семь|семи|восемь|восьми|"
    r"девять|девяти|десять|десяти)\s+(?:рабочих\s+|календарных\s+)?"
    r"(?:дней|дня|день)\s+до\b", re.I,
)
_SUBMIT = re.compile(r"\b(?:представить|представление|направить|направление)\b", re.I)
_REPORT = re.compile(r"\bотч[её]т(?:а|у|ом|ы|ов|ности)?\b", re.I)
_SUBJECT = re.compile(r"\b(?:финансов\w+\s+управляющ\w+|арбитражн\w+\s+управляющ\w+)\b", re.I)
_DATE = re.compile(
    r"(?<![\d.])(?P<day>\d{1,2})\s*\.\s*(?P<month>\d{1,2})\s*\.\s*(?P<year>\d{4})(?!\d)|"
    r"(?<!\d)[«\"“”„]*(?P<wd>\d{1,2})\s*[»\"“”]*\s+(?P<wm>" + "|".join(MONTHS) +
    r")\s+(?P<wy>\d{4})(?!\d)", re.I,
)
_DUE_LABEL = re.compile(r"(?:\bдо|\bне\s+позднее(?:\s+чем)?)\s*$", re.I)


@dataclass(frozen=True)
class ReportDeadlineCandidate:
    value: str
    basis: str
    quote: str
    start: int
    end: int


@dataclass(frozen=True)
class ReportDeadlineCandidates:
    selected: ReportDeadlineCandidate | None
    explicit_dates: tuple[ReportDeadlineCandidate, ...]
    problem: str | None


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        if re.fullmatch(r"\d{2}\.\d{2}\.\d{4}", value):
            day, month, year = map(int, value.split("."))
            return date(year, month, day)
        return date.fromisoformat(value)
    except ValueError:
        return None


def _is_report_instruction(fragment: str) -> bool:
    return bool(_REPORT.search(fragment) and _SUBMIT.search(fragment))


def _report_after_submission(fragment: str) -> bool:
    submit = _SUBMIT.search(fragment)
    if not submit:
        return False
    if re.search(r"\b(?:обязать|разъяснить|разрешить|назначить)\b", fragment[:submit.start()], re.I):
        return False
    tail = fragment[submit.end():]
    if re.match(r"\s+лицам\b", tail, re.I):
        return False
    boundary = re.search(r"\b(?:обязать|разъяснить|разрешить|назначить)\b|(?<=[а-яё])\.\s+[А-ЯЁ]", tail)
    if boundary:
        tail = tail[:boundary.start()]
    return bool(_REPORT.search(tail))


def extract_report_deadline_candidates(
    text: str, next_session_date: str | None,
) -> ReportDeadlineCandidates:
    """Return candidates without changing explicit-date priority or day count.

    Existing recognized requirements retain their original result. Additional
    requirements need a report submission instruction and an advance marker in
    the operative part. The base is the next consideration date, selected by
    the shared appointment → procedure-end fallback policy.
    """
    heading = list(_OPERATIVE.finditer(text))
    legacy, _ = extract_early_report_deadline(text, next_session_date)
    if not heading:
        selected = ReportDeadlineCandidate(legacy, 'next_session_minus_ten_calendar_days', text, 0, len(text)) if legacy else None
        return ReportDeadlineCandidates(selected, (), None if selected else 'missing_operative_heading')
    offset = heading[-1].end() if heading else 0
    operative = text[offset:]
    explicit: list[ReportDeadlineCandidate] = []
    for match in _DATE.finditer(operative):
        if not _DUE_LABEL.search(operative[max(0, match.start() - 40):match.start()]):
            continue
        start, end = max(0, match.start() - 250), min(len(operative), match.end() + 250)
        # A semicolon separates independent assignments (e.g. the report and
        # a financial analysis with a different due date).
        start = max(start, operative.rfind(';', start, match.start()) + 1)
        semicolon = operative.find(';', match.end(), end)
        if semicolon >= 0:
            end = semicolon
        fragment = operative[start:end]
        if _ADVANCE.search(operative[max(start, match.start() - 60):match.start()]):
            continue  # "за пять дней до DATE" names a base, not the exact due date.
        # Bind the date to a nearby submission verb, rather than a hearing date.
        submit = list(_SUBMIT.finditer(fragment))
        if not _is_report_instruction(fragment) or not any(
            abs((start + verb.start()) - match.start()) < 120 for verb in submit
        ):
            continue
        try:
            parsed = date(int(match['year'] or match['wy']),
                          int(match['month']) if match['month'] else MONTHS[match['wm'].lower()],
                          int(match['day'] or match['wd']))
        except ValueError:
            continue
        explicit.append(ReportDeadlineCandidate(
            parsed.strftime('%d.%m.%Y'), 'explicit_report_date_pending_policy',
            fragment, offset + start, offset + end,
        ))

    if legacy:
        selected = ReportDeadlineCandidate(legacy, 'next_session_minus_ten_calendar_days',
                                           operative, offset, len(text))
        return ReportDeadlineCandidates(selected, tuple(explicit), 'explicit_date_priority_pending' if explicit else None)
    base = _parse_date(next_session_date)
    for marker in _ADVANCE.finditer(operative):
        start = max(0, marker.start() - 250)
        end = min(len(operative), marker.end() + 650)
        fragment = operative[start:end]
        # The report instruction must follow the marker in the same local context.
        following = operative[marker.start():end]
        first_submit = _SUBMIT.search(following)
        # A report mentioned as the subject of a hearing is not the object of
        # "представить позицию". Require the report after the submission verb.
        if not first_submit or not _report_after_submission(following):
            continue
        if not (_SUBJECT.search(fragment) or re.search(r"\bв\s+(?:арбитражный\s+)?суд\b", following, re.I)):
            continue
        if base is None:
            return ReportDeadlineCandidates(None, tuple(explicit), 'missing_next_session_date')
        selected = ReportDeadlineCandidate(
            (base - timedelta(days=10)).strftime('%d.%m.%Y'), 'next_session_minus_ten_calendar_days',
            fragment, offset + start, offset + end,
        )
        return ReportDeadlineCandidates(selected, tuple(explicit), 'explicit_date_priority_pending' if explicit else None)
    return ReportDeadlineCandidates(None, tuple(explicit), 'explicit_date_priority_pending' if explicit else None)

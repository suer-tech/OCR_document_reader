"""Use a printed report deadline first; otherwise preserve the ten-day policy.

Every candidate remains unconfirmed until downstream verification. A date
after «за N дней до» is a calculation base, never a printed report deadline.
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
_SUBMIT = re.compile(r"\b(?:представить|предоставить|представление|направить|направление)\b", re.I)
_REPORT = re.compile(r"\bотч[её]т(?:а|у|ом|ы|ов|ности)?\b", re.I)
_SUBJECT = re.compile(r"\b(?:финансов\w+\s+управляющ\w+|арбитражн\w+\s+управляющ\w+)\b", re.I)
_DATE = re.compile(
    r"(?<![\d.])(?P<day>\d{1,2})\s*\.\s*(?P<month>\d{1,2})\s*\.\s*(?P<year>\d{4})(?!\d)|"
    r"(?<!\d)[«\"“”„]*(?P<wd>\d{1,2})\s*[»\"“”]*\s+(?P<wm>" + "|".join(MONTHS) +
    r")\s+(?P<wy>\d{4})(?!\d)", re.I,
)
_DUE_LABEL = re.compile(r"(?:\bдо|\bне\s+позднее(?:\s+чем)?)\s*$", re.I)
_OTHER_OBJECT = re.compile(r"\b(?:заявлени\w*|позици\w*|публикаци\w*|анализ\w*|доказательств\w*|сведени\w*)\b", re.I)
_ASSIGNMENT = re.compile(r"\b(?:финансовому|арбитражному)\s+управляющему\b|"
                         r"\bобязать\s+(?:финансового|арбитражного)\s+управляющего\b", re.I)
_REPORT_ATTACHMENT = re.compile(r"\b(?:заявлени\w*|ходатайств\w*)\s+о\s+завершении"
                                r"[\s\S]{0,200}?\bс\s+(?:представлением|приложением)\s+отч[её]т", re.I)
_DATE_REFERENCE = re.compile(r"\bк\s+указанной\s+(?:выше\s+)?дате\b", re.I)
_BULLET = re.compile(r"\n[ \t]*[-–—][ \t]+")


def _instruction_end(operative: str, start: int, limit: int) -> int:
    boundary = re.search(r";|(?<=[а-яё])\.\s+(?=[А-ЯЁ])|\n[ \t]*[-–—][ \t]+", operative[start:limit])
    return start + boundary.start() if boundary else limit


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


def _direct_report_submission(fragment: str, verb: re.Match) -> bool:
    """The dated submission must concern the report, not a position about it."""
    tail = fragment[verb.end():]
    if _REPORT_ATTACHMENT.search(tail):
        return True
    after = _REPORT.search(tail)
    if after and not _OTHER_OBJECT.search(tail[:after.start()]):
        return True
    before = list(_REPORT.finditer(fragment[:verb.start()]))
    return bool(before and verb.start() - before[-1].end() < 150
                and not _OTHER_OBJECT.search(tail[:after.start() if after else len(tail)]))


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
    return _direct_report_submission(fragment[:submit.end()] + tail, submit)


def extract_report_deadline_candidates(
    text: str, next_session_date: str | None,
) -> ReportDeadlineCandidates:
    """Prefer one unambiguous literal deadline; otherwise calculate minus ten.

    Calculations need a source requirement and the shared next-consideration
    date. An explicit report deadline does not depend on a hearing/end date.
    """
    heading = list(_OPERATIVE.finditer(text))
    legacy, _ = extract_early_report_deadline(text, next_session_date)
    if not heading:
        selected = ReportDeadlineCandidate(legacy, 'next_session_minus_ten_calendar_days', text, 0, len(text)) if legacy else None
        return ReportDeadlineCandidates(selected, (), None if selected else 'missing_operative_heading')
    offset = heading[-1].end() if heading else 0
    operative = text[offset:]
    from .court_regex_early_report_required import extract_early_report_required

    assignments = list(_ASSIGNMENT.finditer(operative))
    explicit: list[ReportDeadlineCandidate] = []
    for match in _DATE.finditer(operative):
        if not _DUE_LABEL.search(operative[max(0, match.start() - 40):match.start()]):
            continue
        start, end = max(0, match.start() - 250), min(len(operative), match.end() + 250)
        end = _instruction_end(operative, match.end(), end)
        bullets = list(_BULLET.finditer(operative, start, match.start()))
        if bullets:
            start = bullets[-1].end()
        previous = [a.start() for a in assignments if a.start() < match.start()]
        following = [a.start() for a in assignments if a.start() > match.end()]
        if previous:
            start = max(start, previous[-1])
        if following:
            end = min(end, following[0])
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
            abs((start + verb.start()) - match.start()) < 120
            and _direct_report_submission(fragment, verb) for verb in submit
        ):
            continue
        try:
            parsed = date(int(match['year'] or match['wy']),
                          int(match['month']) if match['month'] else MONTHS[match['wm'].lower()],
                          int(match['day'] or match['wd']))
        except ValueError:
            continue
        explicit.append(ReportDeadlineCandidate(
            parsed.strftime('%d.%m.%Y'), 'explicit_report_date',
            fragment, offset + start, offset + end,
        ))

    if len({item.value for item in explicit}) > 1:
        return ReportDeadlineCandidates(None, tuple(explicit), 'conflicting_explicit_report_dates')
    if explicit:
        return ReportDeadlineCandidates(explicit[0], tuple(explicit), None)
    if extract_early_report_required(text).value is False:
        return ReportDeadlineCandidates(None, (), None)
    # A reference is an explicit due date only if its date is unambiguous in
    # the current order. Statutory and historical dates are not antecedents.
    for marker in _DATE_REFERENCE.finditer(operative):
        end = _instruction_end(operative, marker.end(), min(len(operative), marker.end() + 600))
        fragment = operative[max(0, marker.start() - 120):end]
        if not _is_report_instruction(fragment) or not _report_after_submission(operative[marker.start():end]):
            continue
        from .court_decision_legacy_rules import extract_procedure_end_date_with_meta
        from .court_regex_next_session_date import extract_next_session_date_candidates

        end_date, _ = extract_procedure_end_date_with_meta(text)
        appointment = extract_next_session_date_candidates(text).selected
        dates = {v for v in (end_date, appointment.value if appointment else None) if v}
        if len(dates) != 1 or (next_session_date and next_session_date not in dates):
            return ReportDeadlineCandidates(None, (), 'unresolved_report_date_reference')
        selected = ReportDeadlineCandidate(dates.pop(), 'explicit_report_date', text[:offset + end], 0, offset + end)
        return ReportDeadlineCandidates(selected, (selected,), None)
    base = _parse_date(next_session_date)
    for marker in _ADVANCE.finditer(operative):
        start = max(0, marker.start() - 250)
        end = _instruction_end(operative, marker.end(), min(len(operative), marker.end() + 650))
        bullets = list(_BULLET.finditer(operative, start, marker.start()))
        if bullets:
            start = bullets[-1].end()
        fragment = operative[start:end]
        if re.search(r"\bв\s+случае\s+назначения\b", fragment, re.I):
            continue
        # The report instruction must follow the marker in the same local context.
        before = list(_SUBMIT.finditer(operative, start, marker.start()))
        # Both «за N дней представить отчёт» and «представить за N дней отчёт»
        # are literal orders. Keep the verb so the same object checks apply.
        instruction_starts = [marker.start()]
        if before and marker.start() - before[-1].end() < 150:
            instruction_starts.append(before[-1].start())
        following = next((operative[a:end] for a in instruction_starts
                          if _SUBMIT.search(operative[a:end]) and _report_after_submission(operative[a:end])), None)
        # A report mentioned as the subject of a hearing is not the object of
        # "представить позицию". Require the report after the submission verb.
        if following is None:
            continue
        actor_context = operative[max(0, start - 2500):end]
        if not (_SUBJECT.search(actor_context) or re.search(r"\bв\s+(?:арбитражный\s+)?суд\b", following, re.I)):
            continue
        if base is None:
            return ReportDeadlineCandidates(None, tuple(explicit), 'missing_next_session_date')
        selected = ReportDeadlineCandidate(
            (base - timedelta(days=10)).strftime('%d.%m.%Y'), 'next_session_minus_ten_calendar_days',
            fragment, offset + start, offset + end,
        )
        return ReportDeadlineCandidates(selected, (), None)
    return ReportDeadlineCandidates(None, (), None)

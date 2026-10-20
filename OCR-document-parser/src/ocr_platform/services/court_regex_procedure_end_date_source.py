"""Unconfirmed, literal evidence for the way the current procedure is timed."""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Literal

from ocr_platform.services.court_decision_dates import _DATE, _value
from ocr_platform.services.court_decision_legacy_rules import (
    _OPERATIVE_HEADING,
    _ORDER,
    _procedure_term_context,
)
from ocr_platform.services.court_procedure_duration import _TERM, _number

SourceValue = Literal["explicit_date", "explicit_duration", "not_found"]


@dataclass(frozen=True)
class ProcedureEndDateSourceCandidate:
    value: SourceValue
    quote: str
    start: int
    end: int
    requires_confirmation: bool = True
    absence: bool = False


@dataclass(frozen=True)
class ProcedureEndDateSourceResult:
    selected: ProcedureEndDateSourceCandidate | None
    candidates: tuple[ProcedureEndDateSourceCandidate, ...]
    problem: str = ""


_HEADING = re.compile(
    rf"\b{_OPERATIVE_HEADING}\s*:|"
    rf"(?m:^[^\S\r\n]*{_OPERATIVE_HEADING}[^\S\r\n]*:?[^\S\r\n]*$)|"
    r"\b(?-i:Р\s*Е\s*Ш\s*И\s*Л|О\s*П\s*Р\s*Е\s*Д\s*Е\s*Л\s*И\s*Л)"
    r"\s+(?=(?:признать|ввести|открыть|продлить|утвердить)\b)", re.I,
)
_EXCLUDED = re.compile(
    r"\b(?:ранее|если|просит|просил[аи]?|предлагает|следует|например|"
    r"в\s+случае|при\s+отсутствии|другом\s+деле|согласно|"
    r"решени[а-яё]*\s+от|определени[а-яё]*\s+от|"
    r"в\s+соответствии\s+с|может\s+быть|не\s+более)\b", re.I,
)
_OTHER_DURATION = re.compile(
    r"\b(?:обжалован[а-яё]*|апелляци[а-яё]*|кассаци[а-яё]*|публикаци[а-яё]*|"
    r"вознаграждени[а-яё]*|требован[а-яё]*\s+кредитор[а-яё]*)\b", re.I,
)
_CURRENT_COURT = re.compile(
    r"\bсуд\b[^.!?;:]{0,200}\b(?:вводит|продлевает)\b[^.!?;:]{0,300}$", re.I,
)


def _context(part: str, position: int, reasoning: bool) -> tuple[int, bool]:
    if not _procedure_term_context(part, position):
        return 0, False
    prefix = part[max(0, position-1000):position]
    orders = list(_ORDER.finditer(prefix))
    order_start = max(0, position-1000) + orders[-1].start()
    left = max(0, order_start-250)
    # A preceding date's dots must not split the actual command context.
    before = part[left:order_start]
    boundary = list(re.finditer(r"[.!?;](?=\s)|\n\s*\n", before))
    if boundary:
        left += boundary[-1].end()
    context = part[left:position]
    if _EXCLUDED.search(context) or _OTHER_DURATION.search(part[order_start:position]):
        return left, False
    if reasoning and not _CURRENT_COURT.search(context):
        return left, False
    if re.search(r'[«“\"][^»”\"]*$', part[left:order_start]):
        return left, False
    return left, True


def _collect(text: str, offset: int, stop: int, *, reasoning: bool = False):
    part = text[offset:stop]
    dates, durations, invalid = [], [], False
    for match in _DATE.finditer(part):
        relation = re.search(r"\b(?:до|по)\s*[«\"“„]*\s*$", part[max(0, match.start()-25):match.start()], re.I)
        if not relation:
            continue
        position = max(0, match.start()-25) + relation.start()
        left, accepted = _context(part, position, reasoning)
        if not accepted:
            continue
        value = _value(match)
        if value is None:
            invalid = True
            continue
        start, end = offset+left, offset+match.end()
        dates.append((value, ProcedureEndDateSourceCandidate("explicit_date", text[start:end], start, end)))
    for match in _TERM.finditer(part):
        left, accepted = _context(part, match.start(), reasoning)
        if not accepted:
            continue
        value = _number(match[1])
        duplicate = _number(match[2]) if match[2] else value
        if value is None or value != duplicate:
            invalid = True
            continue
        start, end = offset+left, offset+match.end()
        durations.append((value, ProcedureEndDateSourceCandidate("explicit_duration", text[start:end], start, end)))
    return dates, durations, invalid


def extract_procedure_end_date_source_candidates(text: str) -> ProcedureEndDateSourceResult:
    """Classify explicit current orders; every result still needs confirmation.

    The full document is evidence for absence. Missing or conflicting terms are
    deliberately different states; conflict never becomes a not_found value.
    """
    heading = _HEADING.search(text)
    if heading:
        dates, durations, invalid = _collect(text, heading.end(), len(text))
        if not dates and not durations and not invalid:
            dates, durations, invalid = _collect(text, 0, heading.start(), reasoning=True)
    else:
        standalone = re.match(r"\s*(?:\d+[.)]\s*)?(?:ввести|открыть|продлить|процедуру)\b", text, re.I)
        dates, durations, invalid = _collect(text, 0, len(text), reasoning=not bool(standalone))
    candidates = tuple(item for _, item in dates + durations)
    if invalid or len({value for value, _ in dates}) > 1 or len({value for value, _ in durations}) > 1:
        return ProcedureEndDateSourceResult(None, candidates, "conflicting_procedure_terms")
    if dates:
        return ProcedureEndDateSourceResult(dates[0][1], candidates)
    if durations:
        return ProcedureEndDateSourceResult(durations[0][1], candidates)
    candidate = ProcedureEndDateSourceCandidate("not_found", text, 0, len(text), absence=True)
    return ProcedureEndDateSourceResult(candidate, (candidate,), "no_explicit_current_procedure_term")

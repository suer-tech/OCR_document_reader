"""Offline time candidates bound to the same explicit next-appointment date.

These are proposals for downstream confirmation by JEV, never final fields.
Only source text is an extraction input; no operator values or current date.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from ocr_platform.services.court_regex_next_session_date import (
    extract_next_session_date_candidates,
)

_TIME = (
    r"(?<![\w\d])(?P<hour>\d{1,2})\s*"
    r"(?:(?:[:.]\s*(?P<minute>\d{2})(?!\d|[.:]\d)(?:\s*мин(?:ут[аы]?)?\.?)?)|"
    r"(?:час(?:а|ов)?\.?\s*(?P<word_minute>\d{1,2})(?!\d)\s*мин(?:ут[аы]?)?\.?))"
)
_AFTER = re.compile(r"\s*(?:год[ау]?|г\.?)?\s*[,;]?\s*(?:в|на|с)\s+" + _TIME, re.IGNORECASE)
_BEFORE = re.compile(r"\b(?:в|на|с)\s+" + _TIME + r"\s*$", re.IGNORECASE)
_MORE = re.compile(r"\s*(?:,?\s*(?:и|или|либо)|[-–—])\s*(?:(?:в|на)\s+)?" + _TIME, re.IGNORECASE)


@dataclass(frozen=True)
class NextSessionTimeCandidate:
    value: str
    session_date: str
    quote: str
    start: int
    end: int
    time_start: int
    time_end: int
    date_start: int
    date_end: int
    role: str
    requires_confirmation: bool = True


@dataclass(frozen=True)
class NextSessionTimeResult:
    selected: NextSessionTimeCandidate | None
    candidates: tuple[NextSessionTimeCandidate, ...]
    problem: str = ""


def _value(match: re.Match) -> str | None:
    hour = int(match["hour"])
    minute = int(match["minute"] or match["word_minute"])
    if hour > 23 or minute > 59:
        return None
    return f"{hour:02d}:{minute:02d}"


def extract_next_session_time_candidates(text: str) -> NextSessionTimeResult:
    """Return explicit HH:MM with literal evidence or an abstention reason.

    Time must be immediately before/after a date accepted by the independent
    date extractor. Multiple distinct times abstain. An absent time is never
    converted to midnight, and invalid, past or conflicting dates block time.
    """
    dates = extract_next_session_date_candidates(text)
    if dates.selected is None:
        return NextSessionTimeResult(None, (), "session_date:" + dates.problem)
    candidates = []
    invalid = False
    for date in dates.candidates:
        if date.value != dates.selected.value:
            continue
        matches = []
        after = text[date.date_end:date.date_end + 180]
        found = _AFTER.match(after)
        if found:
            matches.append((found, date.date_end))
            position = found.end()
            while more := _MORE.match(after, position):
                matches.append((more, date.date_end))
                position = more.end()
        before = text[date.start:date.date_start]
        if found := _BEFORE.search(before):
            matches.append((found, date.start))
        for match, offset in matches:
            value = _value(match)
            if value is None:
                invalid = True
                continue
            time_start = offset + match.start("hour")
            time_end = offset + match.end()
            # Whitespace before a date is context, not part of the time token.
            while time_end > time_start and text[time_end - 1].isspace():
                time_end -= 1
            end = max(date.date_end, time_end)
            candidates.append(NextSessionTimeCandidate(
                value, date.value, text[date.start:end], date.start, end,
                time_start, time_end, date.date_start, date.date_end, date.role,
            ))
    unique = tuple(dict.fromkeys(candidates))
    if invalid:
        return NextSessionTimeResult(None, unique, "invalid_appointment_time")
    if len({item.value for item in unique}) > 1:
        return NextSessionTimeResult(None, unique, "conflicting_appointment_times")
    if unique:
        return NextSessionTimeResult(unique[0], unique)
    return NextSessionTimeResult(None, (), "no_explicit_time_for_appointment")

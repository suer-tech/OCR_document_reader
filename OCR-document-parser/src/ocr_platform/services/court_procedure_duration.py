"""Extract explicit current-procedure durations with literal supporting spans."""
from __future__ import annotations

from dataclasses import dataclass
import re

from ocr_platform.services.court_decision_legacy_rules import (
    WORD_TO_NUM, _OPERATIVE_HEADING, _ORDER, _procedure_term_context,
)

_TERM = re.compile(
    r"\b(?:на\s+срок|сроком(?:\s+(?:на|до))?|продолжительностью|длительностью|на)\s+"
    r"([А-Яа-яЁё]+|\d+)(?:\s*\(\s*([А-Яа-яЁё]+|\d+)\s*\))?\s+месяц(?:а|ев)?\b", re.I,
)
_CURRENT_COURT = re.compile(
    r"\bсуд\b[^.!?;:]{0,200}\b(?:вводит|продлевает)\b[^.!?;:]{0,300}$", re.I,
)
_HISTORICAL_OR_CONDITIONAL = re.compile(
    r"\b(?:ранее|если|просит|просил[аи]?|предлагает|следует|например|"
    r"в\s+случае|при\s+отсутствии|другом\s+деле|решени[а-яё]*\s+от|"
    r"определени[а-яё]*\s+от)\b", re.I,
)
_OTHER_DURATION = re.compile(
    r"\b(?:обжалован[а-яё]*|апелляци[а-яё]*|кассаци[а-яё]*|публикаци[а-яё]*|"
    r"вознаграждени[а-яё]*|требован[а-яё]*\s+кредитор[а-яё]*)\b", re.I,
)


@dataclass(frozen=True)
class DurationCandidate:
    value: int
    quote: str
    start: int
    end: int


@dataclass(frozen=True)
class DurationExtraction:
    selected: DurationCandidate | None
    candidates: tuple[DurationCandidate, ...]
    problem: str = ""


def _number(token: str) -> int | None:
    value = WORD_TO_NUM.get(token.casefold(), token)
    return int(value) if value.isdigit() and int(value) > 0 else None


def _candidates(text: str, start: int, end: int, *, reasoning: bool = False) -> tuple[list[DurationCandidate], bool]:
    part = text[start:end]
    candidates, invalid = [], False
    for match in _TERM.finditer(part):
        if not _procedure_term_context(part, match.start()):
            continue
        prefix = part[max(0, match.start()-1000):match.start()]
        orders = list(_ORDER.finditer(prefix))
        if orders and _OTHER_DURATION.search(prefix[orders[-1].start():]):
            continue
        if reasoning:
            prefix = part[max(0, match.start()-600):match.start()]
            current = _CURRENT_COURT.search(prefix)
            if not current:
                continue
            context = prefix[max(0, current.start()-100):]
            if (_HISTORICAL_OR_CONDITIONAL.search(context)
                    or re.search(r'[«“"][^»”"]*$', context)):
                continue
        value = _number(match[1])
        duplicate = _number(match[2]) if match[2] else value
        if value is None or value != duplicate:
            invalid = True
            continue
        left, right = start + max(0, match.start()-350), start + match.end()
        candidates.append(DurationCandidate(value, text[left:right], left, right))
    return candidates, invalid


def extract_procedure_duration(text: str) -> DurationExtraction:
    """Do not infer months from end dates, statutory maxima, or future renewals."""
    headings = [match for pattern in (
        rf"\b{_OPERATIVE_HEADING}\s*:",
        rf"(?m)^[^\S\r\n]*{_OPERATIVE_HEADING}[^\S\r\n]*:?[^\S\r\n]*$",
        r"\b(?-i:Р\s*Е\s*Ш\s*И\s*Л|О\s*П\s*Р\s*Е\s*Д\s*Е\s*Л\s*И\s*Л)"
        r"\s+(?=(?:признать|ввести|открыть|продлить|утвердить)\b)",
    ) if (match := re.search(pattern, text, re.I))]
    if headings:
        heading = min(headings, key=lambda item: item.start())
        candidates, invalid = _candidates(text, heading.end(), len(text))
        if not candidates and not invalid:
            candidates, invalid = _candidates(text, 0, heading.start(), reasoning=True)
    else:
        standalone = re.match(r"\s*(?:\d+[.)]\s*)?(?:ввести|открыть|продлить)\b", text, re.I)
        candidates, invalid = _candidates(text, 0, len(text), reasoning=not bool(standalone))
    values = {item.value for item in candidates}
    if invalid or len(values) > 1:
        return DurationExtraction(None, tuple(candidates), "conflicting_duration")
    return DurationExtraction(candidates[0] if candidates else None, tuple(candidates),
                              "" if candidates else "not_found")


def procedure_duration_field(text: str) -> dict:
    """Return a regex candidate without claiming classifier confirmation."""
    extracted = extract_procedure_duration(text)
    result = dict(value=None, confidence=0.0, source="regex_court_duration",
                  reasoning="Явно назначенная длительность процедуры в месяцах не найдена")
    if extracted.selected:
        item = extracted.selected
        reason = "Явно назначенный срок найден регуляркой; требуется подтверждение: " + item.quote
        result.update(value=item.value, reasoning=reason, evidence=item.quote,
                      evidence_start=item.start, evidence_end=item.end,
                      validation_issue="Срок найден регуляркой; подтверждающий классификатор пока не подключён")
    elif extracted.problem != "not_found":
        result.update(reasoning="Противоречивые значения длительности процедуры",
                      validation_issue="Нельзя однозначно определить длительность процедуры")
    return result

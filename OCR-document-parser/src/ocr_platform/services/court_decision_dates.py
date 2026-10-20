"""Prepare source-bound act dates and choose the start used in procedure calculations."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Literal

DateRole = Literal["full_text", "resolutive", "act_header"]
MONTHS = {"января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
          "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12}
_DATE = re.compile(
    r"(?<![\w.])(?P<day>\d{1,2})\s*\.\s*(?P<month>\d{1,2})\s*\.\s*(?P<year>\d{4})(?!\d)|"
    r"(?<!\w)[«\"“”„]*[^\S\r\n]*(?P<word_day>\d{1,2})[^\S\r\n]*[»\"“”]*\s+"
    r"(?P<word_month>" + "|".join(MONTHS) + r")\s+(?P<word_year>\d{4})(?!\d)", re.I,
)
_BODY = re.compile(
    r"\b(?:в\s+составе|судья|при\s+ведении|рассмотрев|рассмотрел|ознакомившись|"
    r"руководствуясь|у\s*с\s*т\s*а\s*н\s*о\s*в\s*и\s*л|"
    r"о\s*п\s*р\s*е\s*д\s*е\s*л\s*и\s*л|р\s*е\s*ш\s*и\s*л)\b", re.I,
)
_FULL = re.compile(
    r"полный\s+текст\s+(?:решения|определения)(?:\s+суда)?\s+(?:изготовлен|составлен)|"
    r"(?:решение|определение)\s+(?:в\s+полном\s+объ[её]ме\s+)?"
    r"(?:принято\s+и\s+)?(?:изготовлено|составлено)(?:\s+в\s+полном\s+объ[её]ме)?|"
    r"(?:в\s+полном\s+объ[её]ме|в\s+окончательной\s+форме)\s+"
    r"(?:решение|определение)\s+(?:изготовлено|составлено)|"
    r"дата\s+принятия\s+(?:решения|определения)\s+в\s+полном\s+объ[её]ме", re.I,
)
_RESOLUTIVE = re.compile(
    r"резолютивная\s+часть(?:\s+(?:решения|определения)(?:\s+суда)?)?\s+"
    r"(?:объявлена|оглашена|вынесена|принята|составлена)|"
    r"дата\s+объявления\s+резолютивной\s+части", re.I,
)
_SHORT_RESOLUTIVE = re.compile(
    r"резолютивная[^\S\r\n]+часть(?:[^\S\r\n]+(?:решения|определения)(?:[^\S\r\n]+суда)?)?", re.I,
)
_ACT = re.compile(r"\b(?:р\s*е\s*ш\s*е\s*н\s*и\s*[ея]|"
                  r"о\s*п\s*р\s*е\s*д\s*е\s*л\s*е\s*н\s*и\s*[ея])\b", re.I)
_SIGNATURE = re.compile(
    r"(?:электронная\s+подпись|данные\s+ЭП)[\s\S]{0,1200}?"
    r"кому\s+выдан[ао][^\n]*", re.I,
)
_TECHNICAL_LABEL = re.compile(
    r"(?:сертификат[^\n\d]{0,60}|"
    r"дата\s+(?:публикации|размещения|поступления|регистрации|отправления|рождения|заседания)|"
    r"срок\s+(?:(?:представления|подачи)\s+)?отч[её]та|"
    r"(?:судебное\s+)?заседание\s+(?:назначено|назначить)\s+на|"
    r"опубликован[оа]?|дата\s+принятия\s+заявления)", re.I,
)
_CERTIFICATE_DATE = re.compile(
    r"\bДата[ \t:]+\d{1,2}\.\d{1,2}\.\d{4}[ \t]+\d{1,2}:\d{2}(?::\d{2})?", re.I,
)


@dataclass(frozen=True)
class DateCandidate:
    value: str
    role: DateRole
    quote: str
    start: int
    end: int


@dataclass(frozen=True)
class DecisionDateCandidates:
    selected: DateCandidate | None
    candidates: tuple[DateCandidate, ...]
    problem: str = ""


def _mask_technical(text: str) -> str:
    chars = list(text)
    spans = []
    for pattern in (_SIGNATURE, _CERTIFICATE_DATE):
        for match in pattern.finditer(text):
            spans.append((match.start(), match.end()))
    for label in _TECHNICAL_LABEL.finditer(text):
        tail = text[label.end():label.end()+100]
        found = _DATE.search(tail)
        if found and re.fullmatch(r"[\s:.,—–-]*(?:(?:в\s+)?КАД[\s:.,—–-]*)?", tail[:found.start()], re.I):
            end = label.end()+found.end()
            if "сертификат" in label.group().casefold():
                remaining = text[end:end+100]
                following = _DATE.search(remaining)
                if following and re.fullmatch(r"\s*(?:года|г\.?)?\s*(?:по|до|[-—–])\s*",
                                               remaining[:following.start()], re.I):
                    end += following.end()
            spans.append((label.start(), end))
    for start, end in spans:
        for index in range(start, end):
            if chars[index] not in "\r\n":
                chars[index] = " "
    return "".join(chars)


def _value(match: re.Match) -> str | None:
    if match["day"]:
        parts = int(match["year"]), int(match["month"]), int(match["day"])
    else:
        parts = int(match["word_year"]), MONTHS[match["word_month"].casefold()], int(match["word_day"])
    try:
        value = date(*parts)
    except ValueError:
        return None
    # Ignore an unrelated word immediately attached to the year, preserving г/года.
    suffix = match.string[match.end():]
    if re.match(r"[^\W\d_]", suffix) and not re.match(r"г(?:ода)?\b", suffix, re.I):
        return None
    return value.strftime("%d.%m.%Y")


def _candidate(text: str, start: int, end: int, value: str, role: DateRole) -> DateCandidate:
    # Include literal line context for a future verifier; never fabricate a quote.
    left = max(text.rfind("\n", 0, start) + 1, start-120)
    right = text.find("\n", end)
    if right == -1:
        right = len(text)
    right = min(right, end+120)
    return DateCandidate(value, role, text[left:right], left, right)


def _labelled(text: str, header: str, pattern: re.Pattern, role: DateRole,
              same_line: bool = False) -> tuple[list[DateCandidate], list[tuple[int, int]], bool]:
    candidates, spans = [], []
    invalid = False
    for label in pattern.finditer(header):
        tail = header[label.end():label.end()+150]
        found = _DATE.search(tail)
        if found is None:
            invalid |= not same_line
            continue
        prefix = tail[:found.start()]
        gap = (r"(?:[^\S\r\n]|[:.,—–-])*" if same_line else
               r"[\s:.,—–-]*(?:в\s+(?:совещательной\s+комнате|судебном\s+заседании)\s*)?")
        if not re.fullmatch(gap, prefix, re.I):
            invalid |= not same_line
            continue
        end = label.end() + found.end()
        spans.append((label.start(), end))
        value = _value(found)
        if value is None:
            invalid = True
        else:
            candidates.append(_candidate(text, label.start(), end, value, role))
    return candidates, spans, invalid


def extract_decision_date_candidates(
    text: str, *, header_limit: int = 4000, require_act_title: bool = True,
) -> DecisionDateCandidates:
    """Return an unconfirmed candidate or abstain, without invoking any model."""
    header = _mask_technical(text[:header_limit])
    if stop := _BODY.search(header):
        header = header[:stop.start()]
    full, full_spans, invalid_full = _labelled(text, header, _FULL, "full_text")
    resolution, resolution_spans, invalid_resolution = _labelled(text, header, _RESOLUTIVE, "resolutive")
    short, short_spans, _ = _labelled(text, header, _SHORT_RESOLUTIVE, "resolutive", same_line=True)
    resolution.extend(item for item in short if item not in resolution)
    labelled_spans = full_spans + resolution_spans + short_spans
    unlabelled = []
    invalid_header = False
    for match in _DATE.finditer(header):
        if any(start <= match.start() < end for start, end in labelled_spans):
            continue
        value = _value(match)
        if value is None:
            invalid_header = True
        elif not require_act_title or _ACT.search(header):
            unlabelled.append(_candidate(text, match.start(), match.end(), value, "act_header"))
    candidates = tuple(dict.fromkeys(full + resolution + unlabelled))
    if invalid_full:
        return DecisionDateCandidates(None, candidates, "invalid_full_text_date")
    values = {item.value for item in full}
    if len(values) > 1:
        return DecisionDateCandidates(None, candidates, "conflicting_full_text_dates")
    if full:
        full_date = date.fromisoformat("-".join(reversed(full[0].value.split("."))))
        if invalid_resolution or any(
            date.fromisoformat("-".join(reversed(item.value.split(".")))) > full_date for item in resolution
        ):
            return DecisionDateCandidates(None, candidates, "conflicting_resolutive_date")
        return DecisionDateCandidates(full[0], candidates)
    if invalid_header:
        return DecisionDateCandidates(None, candidates, "invalid_header_date")
    values = {item.value for item in unlabelled}
    if len(values) == 1:
        return DecisionDateCandidates(unlabelled[0], candidates)
    return DecisionDateCandidates(None, candidates, "ambiguous_header" if values else "no_act_date")


def extract_procedure_start_date_candidates(text: str) -> DecisionDateCandidates:
    """Choose the current procedure's start, independently of the full-act date."""
    result = extract_decision_date_candidates(text, require_act_title=False)
    if result.problem.startswith(("invalid_", "conflicting_")):
        return result
    # A malformed resolutive label can exist without a valid candidate.
    header = _mask_technical(text[:4000])
    if stop := _BODY.search(header):
        header = header[:stop.start()]
    if _labelled(text, header, _RESOLUTIVE, "resolutive")[2]:
        return DecisionDateCandidates(None, result.candidates, "invalid_resolutive_date")
    resolution = [item for item in result.candidates if item.role == "resolutive"]
    if len({item.value for item in resolution}) > 1:
        return DecisionDateCandidates(None, result.candidates, "conflicting_resolutive_dates")
    if resolution:
        return DecisionDateCandidates(resolution[0], result.candidates)
    return result

"""Source-bound candidates for the court issuing the current judicial act.

This module only proposes a value. Every candidate needs external confirmation.
There is no court catalogue or inference from case numbers or addresses.
"""
from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class CourtNameCandidate:
    value: str
    quote: str
    start: int
    end: int
    requires_confirmation: bool = True


@dataclass(frozen=True)
class CourtNameResult:
    selected: CourtNameCandidate | None
    candidates: tuple[CourtNameCandidate, ...]
    problem: str | None


_WORD = r"[а-яё]+(?:[-‐‑–—][а-яё]+)*"
_SPACE = r"\s+"
_REGION = (
    rf"(?:города{_SPACE}{_WORD}(?:{_SPACE}и{_SPACE}{_WORD}{_SPACE}области)?"
    rf"|республики{_SPACE}(?:{_WORD}(?:ая|яя){_SPACE})?{_WORD}(?:\s*\({_WORD}\))?"
    rf"|(?:{_WORD}{_SPACE}){{1,5}}?(?:области|края|республики|автономного{_SPACE}округа)"
    rf"(?:\s*[-‐‑–—]\s*{_WORD})?)"
)
_COURT = re.compile(rf"\bарбитражный\s+суд\s+{_REGION}(?![а-яё])", re.I)
_BOUNDARY = re.compile(
    r"(?im)^\s*(?:у\s*с\s*т\s*а\s*н\s*о\s*в\s*и\s*л[аи]?"
    r"|р\s*е\s*ш\s*и\s*л[аи]?|о\s*п\s*р\s*е\s*д\s*е\s*л\s*и\s*л[аи]?)\s*:?(?=\s|$)"
)
_LOWER_WORDS = {"суд", "города", "области", "края", "и", "автономного", "округа"}
_SPACE_TITLE = re.compile(
    r"(?im)^[ \t]*(?P<name>а\s+р\s+б\s+и\s+т\s+р\s+а\s+ж\s+н\s+ы\s+й"
    r"\s+с\s+у\s+д(?:\s+[а-яё‐‑–—-](?![а-яё])){5,180})[ \t]*$"
)


def _key(value: str) -> str:
    return re.sub(r"[\s‐‑–—-]+", "", value).casefold().replace("ё", "е")


def _value(quote: str) -> str:
    # Only whitespace/case are changed; each word is present in the quote.
    words = re.sub(r"\s+", " ", quote).strip().split(" ")
    return " ".join(word.lower() if word.lower() in _LOWER_WORDS else word.title() for word in words)


def _is_current(text: str, start: int, end: int) -> bool:
    line_start = text.rfind("\n", 0, start) + 1
    before = text[line_start:start].strip(" \t*#_>")
    after = text[end:end + 120]
    # Standalone letterhead, composition of this court, or the court ruling
    # immediately before РЕШИЛ. Historical/appeal mentions lack these contexts.
    if not before and re.match(r"[ \t*]*(?:\r?\n|$)", after):
        return True
    if not before and re.match(r"\s+в\s+составе\b", after, re.I):
        return True
    return bool(
        re.match(r"\s+р\s*е\s*ш\s*и\s*л\s*:", after, re.I)
        and re.search(r"руководствуясь\b", text[max(0, start - 700):start], re.I)
    )


def extract_court_name_candidates(text: str) -> CourtNameResult:
    """Find the issuing court in the letterhead or this act's introduction."""
    boundary = _BOUNDARY.search(text)
    end = min(boundary.start() if boundary else len(text), 3500)
    header = text[:end]
    found = []
    for match in _COURT.finditer(header):
        if _is_current(text, match.start(), match.end()):
            found.append(CourtNameCandidate(_value(match[0]), match[0], match.start(), match.end()))

    for match in _SPACE_TITLE.finditer(header):
        quote = match["name"].rstrip()
        compact = re.sub(r"\s+", "", quote)
        territory = re.fullmatch(r"арбитражныйсуд([а-яё-]+)(области|края)", compact, re.I)
        if territory:
            value = f"Арбитражный суд {territory[1].title()} {territory[2].lower()}"
        else:
            territory = re.fullmatch(r"арбитражныйсуд(республики|города)([а-яё-]+)", compact, re.I)
            if not territory:
                continue
            kind = territory[1].title() if territory[1].casefold() == "республики" else "города"
            value = f"Арбитражный суд {kind} {territory[2].title()}"
        if territory:
            start = match.start("name")
            found.append(CourtNameCandidate(value, quote, start, start + len(quote)))

    found.sort(key=lambda candidate: candidate.start)
    candidates = tuple(found)
    keys = {_key(candidate.value) for candidate in candidates}
    if len(keys) > 1:
        return CourtNameResult(None, candidates, "conflicting_header_courts")
    if not candidates:
        return CourtNameResult(None, candidates, "court_name_not_found_in_header")
    # Prefer the ordinary spelling repeated in the introduction over an all-cap
    # or letter-spaced letterhead. Both remain available with literal evidence.
    selected = next((candidate for candidate in candidates
                     if any(char.islower() for char in candidate.quote)
                     and not _SPACE_TITLE.fullmatch(candidate.quote)), candidates[0])
    return CourtNameResult(selected, candidates, None)

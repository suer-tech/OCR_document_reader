"""Propose the current act's basis from literal title/disposition evidence.

This field extractor is not a document router. All proposals need confirmation.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Literal


@dataclass(frozen=True)
class DocumentBasisCandidate:
    value: Literal["Решение", "Определение"]
    quote: str
    start: int
    end: int
    requires_confirmation: bool = True


@dataclass(frozen=True)
class DocumentBasisResult:
    selected: DocumentBasisCandidate | None
    candidates: tuple[DocumentBasisCandidate, ...]
    problem: str | None


def _letters(word: str) -> str:
    return r"[\s*_]*".join(word)


_TITLE = re.compile(
    rf"(?im)^[ \t#*_]*(?P<word>{_letters('решение')}|{_letters('определение')})"
    r"[ \t*_]*[:.]?[ \t*_]*(?=\r?$)",
)
_BODY = re.compile(
    rf"(?im)\b{_letters('установил')}[аи]?(?:\s*:|(?=[ \t*_]*\r?$))",
)
_ORDER = re.compile(
    rf"(?im)^[ \t#*_]*(?P<word>{_letters('решил')}|{_letters('определил')})"
    r"[ \t*_]*(?::|(?=\r?$))",
)
_INLINE_ORDER = re.compile(
    rf"(?i)\bсуд\s+(?P<word>{_letters('решил')}|{_letters('определил')})"
    r"[ \t*_]*:",
)


def _candidate(text: str, match: re.Match[str]) -> DocumentBasisCandidate:
    start, end = match.span("word")
    compact = re.sub(r"[\s*_]", "", text[start:end]).casefold()
    value = "Решение" if compact in {"решение", "решил"} else "Определение"
    return DocumentBasisCandidate(value, text[start:end], start, end)


def extract_document_basis_candidates(text: str) -> DocumentBasisResult:
    """Use standalone header titles and operative markers, never narrative mentions.

    Letter spacing, line breaks and Markdown emphasis preserve source offsets.
    A title is accepted only before the first motivating section and within the
    first 3500 characters. Opposing current markers cause abstention.
    """
    body = _BODY.search(text)
    header_end = min(body.start() if body else len(text), 3500)
    titles = [_candidate(text, match) for match in _TITLE.finditer(text, 0, header_end)]
    orders = [_candidate(text, match) for match in _ORDER.finditer(text)]
    for match in _INLINE_ORDER.finditer(text):
        if re.search(r"руководствуясь\b", text[max(0, match.start() - 1500):match.start()], re.I):
            orders.append(_candidate(text, match))
    by_span = {(candidate.start, candidate.end): candidate for candidate in titles + orders}
    candidates = tuple(sorted(by_span.values(), key=lambda candidate: candidate.start))
    if len({candidate.value for candidate in candidates}) > 1:
        return DocumentBasisResult(None, candidates, "conflicting_current_act_markers")
    if not candidates:
        return DocumentBasisResult(None, (), "current_act_title_or_disposition_not_found")
    return DocumentBasisResult(titles[0] if titles else candidates[0], candidates, None)

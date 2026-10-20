"""Literal debtor SNILS candidates for subsequent evidence confirmation.

Only separators are normalized. No checksum correction, identity lookup, or
document-specific information participates in extraction.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ocr_platform.services.court_regex_debtor_full_name import ignored_historical_debtor_identity_blocks


@dataclass(frozen=True)
class DebtorSnilsCandidate:
    value: str
    quote: str
    start: int
    end: int
    requires_confirmation: bool = True


@dataclass(frozen=True)
class DebtorSnilsResult:
    selected: DebtorSnilsCandidate | None
    candidates: tuple[DebtorSnilsCandidate, ...]
    problem: str | None = None


_LABEL = re.compile(
    r"\b(?:СНИЛС|(?:страхов\w+\s+)?номер\w*\s+индивидуального\s+"
    r"лицевого\s+сч[её]та(?:\s+в\s+системе\s+обязательного\s+"
    r"пенсионного\s+страхования)?)",
    re.IGNORECASE,
)
_SEPARATOR = r"[\s\-‐‑‒–—−]*"
_NUMBER = re.compile(
    rf"\)?[\s:№N.,\-–—]*(?P<number>(?<![0-9])[0-9]{{3}}{_SEPARATOR}"
    rf"[0-9]{{3}}{_SEPARATOR}[0-9]{{3}}{_SEPARATOR}[0-9]{{2}})"
    r"(?![0-9]|[ \t]*[\-‐‑‒–—−]\s*[0-9])"
)
_OTHER_OWNER = re.compile(
    r"(?:финансов\w*|арбитражн\w*|конкурсн\w*|временн\w*)\s+управляющ\w*"
    r"|\bкредитор(?:а|ом|у)?\b|\bпредставител\w*|\bсупруг\w*|\bпоручител\w*",
    re.IGNORECASE,
)
_RELATIVE_OWNER = re.compile(
    r"\b(?:сын(?:а|у|ом|е|овья|овей)?|доч(?:ь|ери|ерью|ерей)|"
    r"реб[её]н(?:ок|ка|ку|ком|ке)|дет(?:и|ей|ям|ьми))\b",
    re.IGNORECASE,
)
_EXPLICIT_NEW_DEBTOR = re.compile(
    r"\b(?:признать|заявлени[еяю]|в\s+отношении)\s+"
    r"(?:гражданина[\s–—-]+)?должника\b",
    re.IGNORECASE,
)
_RESOLUTION_HEADING = re.compile(
    r"(?:^|\n)\s*(?:РЕШИЛ|ОПРЕДЕЛИЛ|ПОСТАНОВИЛ)А?\s*:?[ \t]*(?:\n|$)",
    re.IGNORECASE,
)
_DEBTOR_FRAME = re.compile(
    r"\bпризнать\b|\bзаявлени[еяю]\b|\bв\s+отношении\b|"
    r"\bдолжник\w*|\bграждан(?:ин|ина|ки|ку)\b|\bбанкротств\w*",
    re.IGNORECASE,
)
_DEBTOR_AFTER = re.compile(
    r"(?:о\s+признании|несостоятельн\w*|банкрот\w*|"
    r"далее\s*[–—\-:]?\s*(?:заявитель[,\s]*)?должник)",
    re.IGNORECASE,
)


def _owner(text: str, label_start: int, number_end: int) -> str:
    """Use nearby procedural roles; never assign an isolated number by default."""
    before = text[max(0, label_start - 1200):label_start]
    after = text[number_end:number_end + 450]
    # A complete preceding sentence or paragraph closes an unrelated role.
    boundary = max(before.rfind("\n\n"), before.rfind(".\n"), before.rfind(". "))
    # Address abbreviations also contain periods, so use them only to close an
    # already identified other owner, not to erase the bankruptcy context.
    others = list(_OTHER_OWNER.finditer(before))
    frames = list(_DEBTOR_FRAME.finditer(before))
    relatives = list(_RELATIVE_OWNER.finditer(before))
    if relatives:
        relative = relatives[-1]
        # "сын должника" contains a debtor word, but the following identity
        # belongs to the son. Generic "заявление"/"в отношении" is not a new
        # debtor identity either. Require a separate explicit debtor clause.
        tail = before[relative.end():]
        explicit_reset = _EXPLICIT_NEW_DEBTOR.search(tail)
        separate_debtor = relative.end() < boundary and re.search(
            r"\bдолжник\b", before[boundary + 1:], re.IGNORECASE
        )
        # References to children in the reasoning/law cannot govern a fresh
        # debtor identity in the operative part. Require both the heading and
        # an actual bankruptcy declaration around the current identity.
        operative_reset = _RESOLUTION_HEADING.search(tail) and (
            re.search(r"\bпризнать\b", tail, re.IGNORECASE) and _DEBTOR_AFTER.search(after)
        )
        if not explicit_reset and not separate_debtor and not operative_reset:
            return "other"
    if others:
        other = others[-1]
        later_frames = [m for m in frames if m.start() > other.end()]
        # "Утвердить финансовым управляющим должника Петрова" describes the
        # manager appointment and cannot establish ownership by itself.
        later_strong = [m for m in later_frames if re.match(
            r"признать|заявлени|в\s+отношении", m.group(), re.IGNORECASE
        )]
        later_debtor_sentence = any(
            m.start() > boundary and m.group().lower().startswith("должник")
            for m in later_frames
        ) and other.end() < boundary
        if not later_strong and not later_debtor_sentence:
            return "other"
    if frames or _DEBTOR_AFTER.search(after):
        return "debtor"
    return "unknown"


def extract_debtor_snils_candidates(text: str) -> DebtorSnilsResult:
    """Return candidates and an unconfirmed selection for ``court_decision_ru``.

    Offsets are Python character offsets of the *quote* in the original text,
    with an exclusive end. Every emitted number has exactly eleven literal
    ASCII digits after a SNILS label. Repeated occurrences are retained; distinct
    numbers are ambiguous, including contradictory repetitions for one debtor.
    Unknown owners remain candidates for review, but cannot be selected alone.
    """
    candidates: list[DebtorSnilsCandidate] = []
    ignored = ignored_historical_debtor_identity_blocks(text)
    debtor_candidates: list[DebtorSnilsCandidate] = []
    for label in _LABEL.finditer(text):
        match = _NUMBER.match(text, label.end())
        if match is None:
            continue
        if any(block.start <= label.start() and match.end("number") <= block.end for block in ignored):
            continue
        owner = _owner(text, label.start(), match.end("number"))
        if owner == "other":
            continue
        digits = re.sub(r"[^0-9]", "", match.group("number"))
        value = f"{digits[:3]}-{digits[3:6]}-{digits[6:9]} {digits[9:]}"
        start = max(0, label.start() - 700)
        end = min(len(text), match.end("number") + 350)
        candidate = DebtorSnilsCandidate(value, text[start:end], start, end)
        candidates.append(candidate)
        if owner == "debtor":
            debtor_candidates.append(candidate)
    values = {candidate.value for candidate in candidates}
    if not candidates:
        return DebtorSnilsResult(None, (), "not_found")
    if len(values) > 1:
        return DebtorSnilsResult(None, tuple(candidates), "ambiguous")
    if not debtor_candidates:
        return DebtorSnilsResult(None, tuple(candidates), "owner_not_established")
    return DebtorSnilsResult(debtor_candidates[0], tuple(candidates))

"""Literal debtor INN proposals for court_decision_ru; semantic review is mandatory."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from ocr_platform.services.court_regex_debtor_full_name import ignored_historical_debtor_identity_blocks


@dataclass(frozen=True)
class DebtorInnCandidate:
    value: str
    quote: str
    start: int
    end: int
    requires_confirmation: Literal[True] = True


@dataclass(frozen=True)
class DebtorInnCandidates:
    selected: DebtorInnCandidate | None
    candidates: list[DebtorInnCandidate]
    problem: str | None


_INN = re.compile(
    r"(?<!\w)(?:И[ \t]*Н[ \t]*Н|идентификационный\s+номер\s+налогоплательщика)"
    r"(?!\w)\s*\)?\s*[:№–—-]?\s*(?P<digits>[0-9]+(?:\s+[0-9]+)*)(?![0-9])", re.I,
)
_ROLE = re.compile(
    r"(?P<other>утвердить|управляющ\w*(?:\s+(?:(?:имуществ\w*|гражданин\w*)\s*[-–—]?\s*|"
    r"в\s+деле\s+о\s+банкротстве\s+)?должник\w*)?"
    r"|кредитор\w*|саморегулируем\w*|\bСРО\b|\bСАУ\b|"
    r"(?:супруг\w*|представител\w*)(?:\s+должник\w*)?|"
    r"\b(?:сын(?:а|у|ом|овья|овей|овьям|овьями)?|дочь|дочер(?:и|ью|ей|ям|ями)|"
    r"реб[её]н(?:ок|ка|ку|ком)|дет(?:и|ей|ям|ьми))\b(?:\s+должник\w*)?)"
    r"|(?P<debtor>должник\w*|в\s+отношении|о\s+банкротстве|признать|заявлени[еяю])", re.I,
)
_ENTITY = re.compile(
    r"\b(?:ООО|ОАО|ЗАО|ПАО|МУП|ГУП)\b|\bАО\s*[«\"]|обществ\w*\s+с\s+ограниченн\w*"
    r"\s+ответственност\w*|акционерн\w*\s+обществ\w*", re.I,
)


def extract_debtor_inn_candidates(text: str) -> DebtorInnCandidates:
    """Keep source digits and offsets; never repair, checksum or consult annotations.

    ``start``/``end`` delimit the complete literal ``quote`` in the original text.
    Whitespace inside an explicitly labelled number is the only normalization.
    Ten digits require an explicitly named legal entity in the debtor context.
    Repeated occurrences of one value agree; different values remain ambiguous.
    """
    candidates: list[DebtorInnCandidate] = []
    ignored = ignored_historical_debtor_identity_blocks(text)
    malformed = False
    previous_end = 0
    for match in _INN.finditer(text):
        start = max(previous_end, match.start() - 900)
        previous_end = match.end()
        if any(block.start <= match.start() and match.end() <= block.end for block in ignored):
            continue
        prefix = text[start:match.start()]
        roles = list(_ROLE.finditer(prefix))
        if not roles or roles[-1].lastgroup != "debtor":
            continue
        role = roles[-1]
        context = prefix[role.start():]
        if role["debtor"].lower().startswith("о") and not re.match(
            r"\s+[А-ЯЁ]", prefix[role.end():],
        ):
            continue
        # A statement by a creditor is insufficient to identify its INN as debtor INN.
        if role["debtor"].lower().startswith("заявлени"):
            tail = text[match.end():match.end() + 650]
            if not re.search(
                r"о\s+признании\s+(?:себя|его|е[её]|несостоятельн|гражданина\s+несостоятельн)|"
                r"\)\s*(?:несостоятельн|банкрот|о\s+несостоятельности)", tail, re.I,
            ):
                continue
        suffix = text[match.end():match.end() + 2]
        if suffix and (suffix[0].isalnum() or re.match(r"[-–—][0-9]", suffix)):
            malformed = True
            continue
        value = re.sub(r"\s", "", match["digits"])
        if len(value) not in (10, 12):
            malformed = True
            continue
        if len(value) == 10 and not _ENTITY.search(context):
            malformed = True
            continue
        if len(value) == 12 and _ENTITY.search(context):
            malformed = True
            continue
        start += role.start()
        end = min(len(text), match.end() + 250)
        candidates.append(DebtorInnCandidate(value, text[start:end], start, end))
    values = {candidate.value for candidate in candidates}
    if len(values) > 1:
        return DebtorInnCandidates(None, candidates, "ambiguous_debtor_inn")
    if not candidates:
        problem = "invalid_debtor_inn_length" if malformed else "debtor_inn_not_found"
        return DebtorInnCandidates(None, [], problem)
    return DebtorInnCandidates(candidates[0], candidates, None)

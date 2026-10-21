"""Unconfirmed procedure candidates supported by a current operative order.

Values use the two procedure names in the court decision profile. Presentation
variants in human labels are deliberately not used to choose output spelling.
"""
from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class ProcedureTypeCandidate:
    value: str
    quote: str
    start: int
    end: int
    requires_confirmation: bool = True


@dataclass(frozen=True)
class ProcedureTypeResult:
    selected: ProcedureTypeCandidate | None
    candidates: tuple[ProcedureTypeCandidate, ...]
    problem: str | None


_HEADING = re.compile(
    r"(?im)(?:^|\n)[ \t*#]*(?:р\s*е\s*ш\s*и\s*л[аи]?|"
    r"о\s*п\s*р\s*е\s*д\s*е\s*л\s*и\s*л[аи]?)\s*:?[ \t*]*(?=\n|$)|"
    r"\b(?:решил|определил)\s*:", re.I,
)
_PROCEDURE = re.compile(
    r"\b(?P<realization>реализаци[яиюей]+\s+(?:(?:его|е[её])\s+)?имуществ[ао])"
    r"(?:\s+(?:гражданина|гражданки|должника|должницы))?|"
    r"\b(?P<restructuring>реструктуризаци[яиюей]+\s+долг(?:ов|и))"
    r"(?:\s+(?:гражданина|гражданки|должника|должницы))?", re.I,
)
_ACTION = re.compile(r"\b(?:ввести|открыть|продлить|признать)\b", re.I)
_EXCLUDED = re.compile(
    r"\b(?:просит|просил\w*|ходатайству\w*|предлага\w*|предлож\w*|"
    r"вправе|может|могут|следует|необходимо|если|при|согласно|разъяснить|"
    r"отменить|отказать|отказано|не\s+вводить|не\s+продлевать)\b|"
    r"\bв\s+(?:случае|соответствии)\b|\bсупруг\w*|\bбывш\w*", re.I,
)
_SENTENCE = re.compile(r"[.!?](?=\s+[А-ЯЁ])|\n\s*\d+[.)]\s+")
_CANCEL = re.compile(r"\b(?:прекратить|завершить|отменить)\b", re.I)
_POSTPONED_EXTENSION = re.compile(r"\s+продлить\b", re.I)


def _context_before(text: str, position: int) -> str:
    """Keep the sentence prefix, including conditions before the imperative."""
    boundaries = list(_SENTENCE.finditer(text, max(0, position - 2000), position))
    start = boundaries[-1].end() if boundaries else max(0, position - 2000)
    return text[start:position]


def extract_procedure_type_candidates(text: str) -> ProcedureTypeResult:
    """Select a procedure introduced/extended by this act, or explain refusal.

    Quotes preserve the complete action-to-procedure interval in the original
    text. No confidence or confirmation is produced by this regex stage.
    """
    headings = list(_HEADING.finditer(text))
    if not headings:
        return ProcedureTypeResult(None, (), "operative_part_not_found")
    offset = headings[-1].end()
    operative = text[offset:]
    mentions = list(_PROCEDURE.finditer(operative))
    found = []
    cancelled = set()
    previous_end = 0
    for mention in mentions:
        value = ("Реализация имущества" if mention["realization"]
                 else "Реструктуризация долгов")
        # A directive cannot inherit an action belonging to another procedure.
        left = max(previous_end, mention.start() - 1800)
        previous_end = mention.end()
        before = operative[left:mention.start()]
        actions = list(_ACTION.finditer(before))
        cancellations = list(_CANCEL.finditer(before))
        if cancellations and (not actions or cancellations[-1].start() > actions[-1].start()):
            cancelled.add(value)
        # Russian orders also place the verb after its object:
        # «Срок процедуры реализации имущества гражданина продлить ...».
        extension = _POSTPONED_EXTENSION.match(operative, mention.end())
        object_start = re.search(r"(?:срок\s+)?процедур[уы]\s*$", before, re.I)
        if extension and object_start:
            start_local = left + object_start.start()
            prefix = _context_before(operative, start_local)
            clause = operative[start_local:extension.end()]
            if not (_EXCLUDED.search(prefix) or _EXCLUDED.search(clause)
                    or re.search(r'[«"“]\s*$', prefix)):
                start, end = offset + start_local, offset + extension.end()
                found.append(ProcedureTypeCandidate(value, text[start:end], start, end))
            continue
        if not actions:
            continue
        action_start = left + actions[-1].start()
        prefix = _context_before(operative, action_start)
        action_clause = operative[action_start:mention.end()]
        if (actions[-1][0].casefold() == "признать"
                and not re.search(r"\bбанкротом\b[\s\S]*\bс\s+введением\s+процедуры\s+", action_clause, re.I)):
            continue
        # Conditions, quotations, statutory permissions, and orders about a
        # spouse cannot serve as evidence of this debtor's current procedure.
        if (_EXCLUDED.search(prefix) or _EXCLUDED.search(action_clause)
                or re.search(r'[«"“]\s*$|\bне\s*$', prefix, re.I)
                or _CANCEL.search(action_clause)):
            continue
        start, end = offset + action_start, offset + mention.end()
        found.append(ProcedureTypeCandidate(value, text[start:end], start, end))
    candidates = tuple(found)
    values = {candidate.value for candidate in candidates}
    if len(values) > 1 or values.intersection(cancelled):
        return ProcedureTypeResult(None, candidates, "conflicting_procedure_orders")
    if not candidates:
        return ProcedureTypeResult(None, (), "current_procedure_order_not_found")
    return ProcedureTypeResult(candidates[0], candidates, None)

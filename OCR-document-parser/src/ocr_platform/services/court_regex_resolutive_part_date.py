"""Extract explicit resolutive dates without substituting a full-act date.

Pure text extraction. Candidates remain unconfirmed for the downstream verifier;
the general decision/procedure date selection policy is not changed here.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from ocr_platform.services import court_decision_dates as dates
from ocr_platform.services.court_decision_sections import RESOLUTIVE_ONLY_TITLE

_LABEL = re.compile(
    r"(?:^|(?<=[;.]))[^\S\r\n]*(?:"
    r"резолютивная\s+часть(?:\s+(?:решения|определения)(?:\s+суда)?)?\s+"
    r"(?:объявлена|оглашена|вынесена|принята|составлена)|"
    r"дата\s+(?:объявления|оглашения)\s+резолютивной\s+части"
    r"(?:\s+(?:решения|определения)(?:\s+суда)?)?)",
    re.I | re.M,
)
_SHORT = re.compile(
    r"(?:^|(?<=[;.]))[^\S\r\n]*резолютивная[^\S\r\n]+часть"
    r"(?:[^\S\r\n]+(?:решения|определения)(?:[^\S\r\n]+суда)?)?",
    re.I | re.M,
)
_HISTORY = re.compile(
    r"\b(?:ранее|согласно\s+(?:решению|определению)|"
    r"(?:решением|определением|постановлением)\s+(?:суда\s+)?от)\b", re.I,
)
_STANDALONE_TITLE = RESOLUTIVE_ONLY_TITLE
_ACT_LINE = re.compile(
    r"^[^\S\r\n]*[*_]*(?:р\s*е\s*ш\s*е\s*н\s*и\s*[ея]|"
    r"о\s*п\s*р\s*е\s*д\s*е\s*л\s*е\s*н\s*и\s*[ея])[*_]*"
    r"(?:[^\S\r\n]+по\s+делу[^\r\n]*)?[^\S\r\n]*\r?$", re.I | re.M,
)
_QUOTED_TITLE = re.compile(
    r"\b(?:цитата|копия|приложение|выписка|согласно|ранее|предыдущ\w*|"
    r"обжалуем\w*|цитиру\w*)\b|\bиз\s+(?:решения|определения|материалов)\b|"
    r"[«“]\s*$", re.I,
)
_SIGNING_DATE = re.compile(r"\b(?:подписан\w*|сертификат\w*|"
                           r"электронн\w*\s+подпис\w*)\b", re.I)
_MOTIVATION = re.compile(r"\bу\s*с\s*т\s*а\s*н\s*о\s*в\s*и\s*л\b", re.I)
_OPERATIVE = re.compile(r"\b(?:р\s*е\s*ш\s*и\s*л|"
                        r"о\s*п\s*р\s*е\s*д\s*е\s*л\s*и\s*л)\b", re.I)
@dataclass(frozen=True)
class ResolutivePartDateCandidate:
    value: str
    quote: str
    start: int
    end: int
    requires_confirmation: bool = True


@dataclass(frozen=True)
class ResolutivePartDateResult:
    selected: ResolutivePartDateCandidate | None
    candidates: tuple[ResolutivePartDateCandidate, ...]
    problem: str = ""


def _standalone_title_date(text: str, header: str) -> ResolutivePartDateResult:
    """standalone_title_header: an act title, not an unlabelled full-act date.

    The title must occupy its own line in the initial current-act header. A
    nearby date is accepted only when all unmasked header dates agree. No date
    from a different act, full-text annotation, motivation or signature is used.
    """
    titles = list(_STANDALONE_TITLE.finditer(header))
    if not titles:
        return ResolutivePartDateResult(None, (), "no_explicit_resolutive_date")
    if len(titles) != 1:
        return ResolutivePartDateResult(None, (), "ambiguous_standalone_resolutive_title")
    title = titles[0]
    if title.start() > 1600 or _QUOTED_TITLE.search(header[:title.start()]):
        return ResolutivePartDateResult(None, (), "no_explicit_resolutive_date")
    if dates._FULL.search(header):
        return ResolutivePartDateResult(None, (), "standalone_title_with_full_text_date")
    # A bare subtitle must also have an adjacent, explicit act title. A narrative
    # reference to "резолютивная часть" alone is never sufficient.
    if not re.search(r"решения|определения", title.group(), re.I):
        adjacent = header[max(0, title.start()-180):min(len(header), title.end()+120)]
        if not _ACT_LINE.search(adjacent):
            return ResolutivePartDateResult(None, (), "no_explicit_resolutive_date")
    # A document containing a motivation section is a full act, even if a title
    # was copied into its header. Only the initial act section is inspected.
    current_act = dates._mask_technical(text[:10000])
    operative = _OPERATIVE.search(current_act)
    before_operative = current_act[:operative.start()] if operative else current_act
    if _MOTIVATION.search(before_operative):
        return ResolutivePartDateResult(None, (), "standalone_title_with_motivation")
    matches = list(dates._DATE.finditer(header))
    near = [item for item in matches if title.end() <= item.start() <= title.end()+350]
    if not near or any(item.start() < title.start() for item in matches):
        return ResolutivePartDateResult(None, (), "no_explicit_resolutive_date")
    if _SIGNING_DATE.search(header[title.end():near[0].start()]):
        return ResolutivePartDateResult(None, (), "no_explicit_resolutive_date")
    values = [dates._value(item) for item in matches]
    if any(value is None for value in values):
        return ResolutivePartDateResult(None, (), "invalid_resolutive_date")
    if len(set(values)) != 1:
        return ResolutivePartDateResult(None, (), "conflicting_resolutive_dates")
    match = near[0]
    item = dates._candidate(text, title.start(), match.end(), values[0], "resolutive")
    candidate = ResolutivePartDateCandidate(item.value, item.quote, item.start, item.end)
    return ResolutivePartDateResult(candidate, (candidate,))


def extract_resolutive_part_date_candidates(text: str) -> ResolutivePartDateResult:
    """Use an explicit date or a standalone resolutive act's own header date.

    Reuse the existing date parser and its calendar validation. Limit additional
    label variants to the same current-act header, preserving original offsets.
    A standalone resolutive act may have only its own title and header date.
    This field-specific rule never changes the shared decision/start-date policy.
    """
    base = dates.extract_decision_date_candidates(text)
    header = dates._mask_technical(text[:4000])
    stops = [match.start() for pattern in (dates._BODY, _HISTORY)
             if (match := pattern.search(header))]
    if stops:
        header = header[:min(stops)]
    labelled, spans, invalid = dates._labelled(text, header, _LABEL, "resolutive")
    short, short_spans, invalid_short = dates._labelled(
        text, header, _SHORT, "resolutive", same_line=True,
    )
    for label_start, date_end in spans + short_spans:
        tail = header[date_end:date_end + 100]
        following = dates._DATE.search(tail)
        if following and re.fullmatch(
            r"[^\S\r\n]*(?:года|г\.?)?[^\S\r\n]*(?:и|или|/|,)[^\S\r\n]*",
            tail[:following.start()], re.I,
        ):
            value = dates._value(following)
            if value is None:
                invalid = True
            else:
                labelled.append(dates._candidate(
                    text, label_start, date_end + following.end(), value, "resolutive",
                ))
    allowed = set(labelled + short)
    # Preserve the shared service's role classification, then add only the
    # additional explicit label variants supported by this field.
    resolution = [item for item in base.candidates
                  if item.role == "resolutive" and item in allowed]
    resolution.extend(item for item in labelled + short if item not in resolution)
    candidates = tuple(ResolutivePartDateCandidate(
        item.value, item.quote, item.start, item.end,
    ) for item in resolution)
    if invalid or invalid_short:
        return ResolutivePartDateResult(None, candidates, "invalid_resolutive_date")
    if len({item.value for item in candidates}) > 1:
        return ResolutivePartDateResult(None, candidates, "conflicting_resolutive_dates")
    if not candidates:
        standalone = _standalone_title_date(text, header)
        if standalone.selected is not None:
            return standalone
        return standalone
    if base.problem in {"invalid_full_text_date", "conflicting_full_text_dates"}:
        return ResolutivePartDateResult(None, candidates, base.problem)
    full = [item for item in base.candidates if item.role == "full_text"]
    selected_date = datetime.strptime(candidates[0].value, "%d.%m.%Y").date()
    if any(selected_date > datetime.strptime(item.value, "%d.%m.%Y").date()
           for item in full):
        return ResolutivePartDateResult(None, candidates, "conflicting_resolutive_date")
    return ResolutivePartDateResult(candidates[0], candidates)

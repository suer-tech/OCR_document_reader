"""Source-bound judge candidates for the current Russian court act.

Pure text extraction: candidates still require independent confirmation. No corpus,
previous extraction, certificate owner or person dictionary supplies missing names.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_WORD = r"[А-ЯЁ][а-яё]+(?:-[А-ЯЁ][а-яё]+)*|[А-ЯЁ]{2,}(?:-[А-ЯЁ]{2,})*"
_SURNAME = rf"(?:{_WORD})"
_INITIALS = r"[А-ЯЁ]\s*\.\s*(?:[А-ЯЁ]\s*\.)?"
_FULL = rf"{_SURNAME}\s+(?:{_WORD})\s+[А-ЯЁ][а-яё]*(?:ич(?:а)?|н[аы])"
_NAME = re.compile(
    rf"(?<![А-Яа-яЁё])(?:{_FULL}|{_SURNAME}\s+{_INITIALS}|"
    rf"{_INITIALS}\s*{_SURNAME})(?![А-Яа-яЁё])"
)
_ROLE = re.compile(
    r"(?im)^[ \t]*(?:председательствующий[ \t]*[-—–]?[ \t]*)?судья\b|"
    r"(?i:в\s+составе\s*:?\s*(?:председательствующего\s*[-—–]?\s*)?судьи)\b"
)
_BODY = re.compile(
    r"\b(?:рассмотрев|рассмотрел|руководствуясь|у\s*с\s*т\s*а\s*н\s*о\s*в\s*и\s*л|"
    r"р\s*е\s*ш\s*и\s*л|о\s*п\s*р\s*е\s*д\s*е\s*л\s*и\s*л)\b", re.I,
)
_TECHNICAL = re.compile(
    r"данные\s+ЭП|Д\s+а\s+н\s+н\s+ы\s+е\s+Э\s+П|"
    r"(?m:^\s*\*?Дата\s+\d{2}\.\d{2}\.\d{4}\s+\d{1,2}:\d{2})|кому\s+выдан[ао]", re.I,
)
_PAST_ACT = re.compile(r"\b(?:решением|определением|постановлением|апелляционн\w*|кассационн\w*)\b", re.I)
_COURT_PREFIX = re.compile(r"Арбитражного\s+суда\b", re.I)


@dataclass(frozen=True)
class JudgeFullNameCandidate:
    value: str
    quote: str
    start: int
    end: int
    requires_confirmation: bool = True


@dataclass(frozen=True)
class JudgeFullNameCandidates:
    selected: JudgeFullNameCandidate | None
    candidates: tuple[JudgeFullNameCandidate, ...]
    problem: str = ""


def _parts(value: str) -> tuple[str, str]:
    """Return surname and preserved initials/full given names in canonical order."""
    value = " ".join(value.split())
    if value[1:2] == "." or re.match(r"^[А-ЯЁ]\s+\.", value):
        last_dot = value.rfind(".")
        surname, names = value[last_dot + 1:].strip(), value[:last_dot + 1]
    elif "." in value:
        surname, names = value.split(" ", 1)
    else:
        surname, names = value.split(" ", 1)
    surname = "-".join(part.capitalize() for part in surname.split("-"))
    return surname, re.sub(r"\s+", "", names) if "." in names else names


def _key(value: str) -> tuple[str, str]:
    surname, names = _parts(value)
    initials = "".join(re.findall(r"[А-ЯЁ]", names)) if "." in names else "".join(
        part[0] for part in names.split()
    )
    return surname.casefold(), initials.casefold()


def _genitive_forms(surname: str) -> set[str]:
    """Only regular surname inflections, used to compare explicit textual forms."""
    forms = {surname.casefold()}
    lower = surname.casefold()
    if lower.endswith(("ов", "ев", "ёв", "ин", "ын")):
        forms.add(lower + "а")
    elif lower.endswith(("ова", "ева", "ёва", "ина", "ына")):
        forms.add(lower[:-1] + "ой")
    elif lower.endswith("ский"):
        forms.add(lower[:-2] + "ого")
    elif lower.endswith("цкий"):
        forms.add(lower[:-2] + "ого")
    elif lower.endswith("ая"):
        forms.add(lower[:-2] + "ой")
        forms.add(lower[:-2] + "ей")
    elif lower.endswith("а"):
        forms.add(lower[:-1] + ("и" if lower[-2:-1] in "гкхжчшщ" else "ы"))
    elif lower.endswith("я"):
        forms.add(lower[:-1] + "и")
    elif lower[-1:] in "бвгджзклмнпрстфхцчшщ":
        forms.add(lower + "а")
    return forms


def _nominative_from_genitive(surname: str) -> str | None:
    # -ой is ambiguous (-а/-ая), so a matching explicit nominative is required.
    if surname.casefold().endswith(("ова", "ева", "ёва", "ина", "ына")):
        return surname[:-1]
    if surname.casefold().endswith(("ского", "цкого")):
        return surname[:-3] + "ий"
    if surname.casefold().endswith(("ко", "енко", "швили", "дзе", "ян", "янц")):
        return surname
    # A consonant-final form already printed after "судьи" cannot be a
    # regular masculine genitive (which ends in a vowel). Keep the source
    # nominative/indeclinable form; do not consult an electronic certificate.
    if surname[-1:].casefold() in "бвгджзклмнпрстфхцчшщ":
        return surname
    return None


def _given_genitive_matches(genitive: str, nominative: str) -> bool:
    if "." in genitive or "." in nominative:
        return True  # Initials are checked separately; no expansion is performed.
    old, new = genitive.casefold().split(), nominative.casefold().split()
    if len(old) != len(new):
        return False
    for inflected, base in zip(old, new, strict=True):
        if base.endswith("а"):
            forms = {base, base[:-1] + ("и" if base[-2:-1] in "гкхжчшщ" else "ы")}
        elif base.endswith("я"):
            forms = {base, base[:-1] + "и"}
        elif base.endswith(("й", "ь")):
            forms = {base, base[:-1] + "я"}
        else:
            forms = {base, base + "а"}
        if inflected not in forms:
            return False
    return True


def extract_judge_full_name_candidates(text: str) -> JudgeFullNameCandidates:
    """Extract a compatible judge from the act header and final signature block.

    Quotes are literal name spans (Python character offsets). Different judges or
    incompatible initials cause abstention; no majority voting or OCR repair.
    """
    if not text or not text.strip():
        return JudgeFullNameCandidates(None, (), "not_found")
    technical = _TECHNICAL.search(text)
    visible_end = technical.start() if technical else len(text)
    body = _BODY.search(text, 0, min(visible_end, 5000))
    header_end = body.start() if body else min(visible_end, 5000)
    found: list[tuple[JudgeFullNameCandidate, bool]] = []
    for role in _ROLE.finditer(text, 0, visible_end):
        genitive = role.group().strip().casefold().endswith("судьи")
        is_header = role.start() < header_end
        if genitive and not is_header:
            continue
        prefix = text[max(0, role.start() - 180):role.start()]
        if is_header and genitive and _PAST_ACT.search(prefix):
            continue
        tail = text[role.end():min(visible_end, role.end() + 220)]
        padding = re.match(r"[\s/_—–-]*", tail)
        assert padding is not None
        offset = padding.end()
        court = _COURT_PREFIX.match(tail, offset)
        if court:
            # A court qualifier is allowed only in a current header/signature.
            match = _NAME.search(tail, court.end())
            if match and _PAST_ACT.search(tail[court.end():match.start()]):
                continue
        else:
            match = _NAME.match(tail, offset)
        if not match:
            continue
        start, end = role.end() + match.start(), role.end() + match.end()
        if not is_header:
            # A signature line contains only the judge, not narrative text.
            line_end = text.find("\n", end)
            line_end = visible_end if line_end < 0 else min(line_end, visible_end)
            if text[end:line_end].strip(" \t\r.,/_—–-"):
                continue
            # Earlier quoted signatures are followed by normal prose.
            remainder = text[line_end:visible_end]
            remainder = re.sub(r"(?im)^\s*(?:председательствующий\s*[-—–]?\s*)?судья[^\n]*", "", remainder)
            remainder = re.sub(r"электронная\s+подпись[\s\S]*", "", remainder, flags=re.I)
            if re.search(r"[А-Яа-яЁё]{2,}", remainder):
                continue
        surname, names = _parts(match.group())
        candidate = JudgeFullNameCandidate(f"{surname} {names}", text[start:end], start, end)
        found.append((candidate, genitive))
    if not found:
        return JudgeFullNameCandidates(None, (), "not_found")
    if re.search(r"\bсудей\b", text[:header_end], re.I):
        return JudgeFullNameCandidates(None, tuple(candidate for candidate, _ in found), "multiple_current_judges")

    # OCR can interleave the issuer of an electronic signature into the same
    # physical line as the judge's printed signature. Recover only a literal
    # initials/surname suffix corroborating an existing current-act header.
    # A certificate owner (or a name appearing only in technical data) is never
    # sufficient to identify or expand the judge.
    for role in _ROLE.finditer(text):
        if role.group().strip().casefold().endswith("судьи") or role.start() < header_end:
            continue
        line_end = text.find("\n", role.end())
        line_end = len(text) if line_end < 0 else line_end
        tail = text[role.end():line_end]
        padding = len(tail) - len(tail.lstrip(" \t"))
        technical_label = _TECHNICAL.match(tail, padding)
        if technical_label is None or re.search(r"кому\s+выдан|владелец|сертификат", tail, re.I):
            continue
        matches = [match for match in _NAME.finditer(tail, technical_label.end())
                   if not tail[match.end():].strip(" \t\r.,/_—–-")
                   and re.match(r"^[А-ЯЁ]\s*\.", match.group())]
        if len(matches) != 1:
            continue
        match = matches[0]
        surname, names = _parts(match.group())
        value = f"{surname} {names}"
        if not any(genitive and _parts(header.value)[0].casefold() in _genitive_forms(surname)
                   and _key(header.value)[1] == _key(value)[1]
                   for header, genitive in found):
            continue
        start, end = role.end() + match.start(), role.end() + match.end()
        found.append((JudgeFullNameCandidate(value, text[start:end], start, end), False))
    nominatives = [candidate for candidate, genitive in found if not genitive]
    normalized: list[JudgeFullNameCandidate] = []
    unresolved = False
    for candidate, genitive in found:
        surname, names = _parts(candidate.value)
        if genitive:
            matches = [other for other in nominatives if surname.casefold() in _genitive_forms(
                _parts(other.value)[0]
            ) and _key(candidate.value)[1] == _key(other.value)[1]
                and _given_genitive_matches(names, _parts(other.value)[1])]
            if matches:
                surname = _parts(matches[0].value)[0]
                if "." not in names and "." not in _parts(matches[0].value)[1]:
                    names = _parts(matches[0].value)[1]
            elif "." in names and (converted := _nominative_from_genitive(surname)):
                surname = converted
            else:
                unresolved = True
                normalized.append(candidate)
                continue
            # Full inflected given names require explicit nominative evidence.
            if "." not in names and not matches:
                unresolved = True
        normalized.append(JudgeFullNameCandidate(
            f"{surname} {names}", candidate.quote, candidate.start, candidate.end
        ))
    candidates = tuple(normalized)
    keys = {_key(candidate.value) for candidate in candidates}
    # Missing second initials cannot prove that two mentions identify one judge.
    full_names = {candidate.value.casefold() for candidate in candidates if "." not in candidate.value}
    if len(keys) > 1 or len(full_names) > 1:
        return JudgeFullNameCandidates(None, candidates, "conflicting_judges")
    if unresolved:
        return JudgeFullNameCandidates(None, candidates, "ambiguous_nominative")
    # Prefer the explicit nominative signature, preserving its information level.
    selected = next((candidate for candidate, (_, genitive) in zip(candidates, found, strict=True)
                     if not genitive), candidates[0])
    return JudgeFullNameCandidates(selected, candidates)

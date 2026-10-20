"""Literal, debtor-bound birth date proposals; every proposal needs confirmation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from ocr_platform.services.court_regex_debtor_full_name import ignored_historical_debtor_identity_blocks


@dataclass(frozen=True)
class DebtorBirthDateCandidate:
    value: str
    quote: str
    start: int
    end: int
    requires_confirmation: bool = True


@dataclass(frozen=True)
class DebtorBirthDateResult:
    selected: DebtorBirthDateCandidate | None
    candidates: list[DebtorBirthDateCandidate]
    problem: str | None


_MONTHS = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4,
    "мая": 5, "июня": 6, "июля": 7, "августа": 8,
    "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}
_DATE = re.compile(
    r"(?<![\d.])(?P<day>\d{1,2})\s*[.]\s*(?P<month>\d{1,2})\s*[.]\s*"
    r"(?P<year>\d{1,5})(?!\d)|"
    r"(?<!\d)[«\"]?(?P<word_day>\d{1,2})[»\"]?\s+"
    r"(?P<word_month>" + "|".join(_MONTHS) + r")\s+(?P<word_year>\d{1,5})(?!\d)",
    re.I,
)
_WORD = r"[А-ЯЁ][а-яё]+(?:[-‐‑][А-ЯЁа-яё][а-яё]+)?"
_NAME = re.compile(
    rf"(?=(?<![А-Яа-яЁё])(?P<name>{_WORD}\s+"
    rf"(?:\({_WORD}(?:\s*,\s*{_WORD})*\)\s+)?"
    rf"{_WORD}\s+(?P<patronymic>{_WORD}(?:\s+(?:[Оо]глы|[Кк]ызы))?))(?![А-Яа-яЁё]))"
)
_OTHER_ROLE = re.compile(
    r"(?:управляющ\w*|супруг\w*|муж\w*|жен[аыуе]|доч\w*|сын\w*|дет[ьи]|"
    r"реб[её]нок\w*|реб[её]нка|кредитор\w*|представител\w*|судь[яиею])"
    r"(?:\s+\w+){0,4}\s*[:,–—-]?\s*$|состоит\s+в\s+браке\s+с\s*$", re.I,
)
_DEBTOR_ROLE = re.compile(
    r"(?:должник\w*(?:\s*[-–—]?\s*граждан\w*)?|граждан\w*|заявлени[еяю]\s*"
    r"(?:(?:граждан\w*|о\s+банкротстве|о\s+признании\s+банкротом)\s*)?|"
    r"признать\s*(?:(?:несостоятельн\w*\s*)?\(?банкрот\w*\)?\s*)?|"
    r"о\s+банкротстве|несостоятельности\s*\(банкротстве\))"
    r"\s*[:,–—-]?\s*(?:(?:ИП|гр\.)\s*)?$", re.I,
)
_BIRTH_BEFORE = re.compile(
    r"(?:дат[аы]\s+(?:и\s+место\s+)?рождени[яе]|дат[аы]\s+рожд\.|"
    r"родил(?:ся|ась)|рожден\w*|рожд\.|род\.|г\.?\s*р\.)\s*[:–—-]?\s*$", re.I,
)
_BIRTH_AFTER = re.compile(
    r"^\s*(?:года?\s+рождени[яе]|г\.?\s*р\.(?!\s*з)|рожд\.)", re.I,
)


def _excluded_name(text: str, match: re.Match[str]) -> bool:
    before = text[max(0, match.start("name") - 180):match.start("name")]
    return bool(_OTHER_ROLE.search(before) or re.search(
        r"лицо,\s*участвующее\s+в\s+деле\s+о\s+банкротстве\s*:\s*$", before, re.I
    ))


def _debtor_name(text: str, match: re.Match[str]) -> bool:
    before = text[max(0, match.start("name") - 180):match.start("name")]
    if _excluded_name(text, match):
        return False
    if _DEBTOR_ROLE.search(before):
        return True
    after = text[match.end("name"):match.end("name") + 650]
    return bool(re.match(r"\s*(?:\([^)]{0,500}\))?\s*[,–—-]?\s*"
                         r"(?:далее\s*[-–—]?\s*)?должник\b", after, re.I))


def _name_key(match: re.Match[str]) -> str:
    return re.sub(r"\s+", " ", match.group("name")).casefold().replace("ё", "е")


def _normalize(match: re.Match[str]) -> str | None:
    year = match.group("year") or match.group("word_year")
    if len(year) != 4:
        return None
    day = int(match.group("day") or match.group("word_day"))
    month = (int(match.group("month")) if match.group("month") else
             _MONTHS[match.group("word_month").lower()])
    try:
        value = date(int(year), month, day)
    except ValueError:
        return None
    return f"{value.day:02}.{value.month:02}.{value.year:04}"


def extract_debtor_birth_date_candidates(text: str) -> DebtorBirthDateResult:
    """Use source text only; offsets address the exact original ``quote`` slice.

    Birth markers must belong to a debtor identity block. A bare date is accepted
    only immediately after a debtor name in a parenthesized identity block.
    Invalid/incomplete dates and conflicting debtor dates prevent selection.
    """
    ignored = ignored_historical_debtor_identity_blocks(text)
    names = [m for m in _NAME.finditer(text)
             if m.group("patronymic").casefold() not in {"оглы", "кызы"} and re.search(
        r"(?:вич(?:а|у|ем|е)?|ьич(?:а|у|ем|е)?|вн(?:а|ы|е|у|ой)|"
        r"ичн(?:а|ы|е|у|ой)|оглы|кызы)$",
        m.group("patronymic"), re.I)]
    identified = {_name_key(n) for n in names if _debtor_name(text, n)}
    candidates = []
    malformed = False
    for match in _DATE.finditer(text):
        if any(block.start <= match.start() and match.end() <= block.end for block in ignored):
            continue
        before = text[max(0, match.start() - 100):match.start()]
        after = text[match.end():match.end() + 45]
        marked = bool(_BIRTH_BEFORE.search(before) or _BIRTH_AFTER.search(after))
        preceding = [n for n in names if n.end("name") <= match.start() and
                     match.start() - n.end("name") <= 450]
        name = preceding[-1] if preceding else None
        direct = re.search(r"(?:должник\w*|должниц\w*)\s*[,(:]?\s*"
                           r"(?:дат[аы]\s+рождения\s*[:–—-]?\s*|родил(?:ся|ась)\s*)$",
                           before, re.I)
        if direct and _OTHER_ROLE.search(before[:direct.start()]):
            # In "ребёнок должника родился" the debtor is the parent, not
            # the subject of the birth date. A role word alone is insufficient.
            direct = None
        if name:
            if _excluded_name(text, name) or _name_key(name) not in identified:
                continue
            between = text[name.end("name"):match.start()]
            # A closed identity block or a sentence cannot lend its role to a
            # later event or family member. Abbreviated metadata stays intact.
            if re.search(r"\)\s*[.;]\s*\S|[!?]|\b(?:супруг\w*|дет[ьи]|сын\w*|доч\w*|"
                         r"реб[её]н(?:ок|к\w*)|"
                         r"управляющ\w*|представител\w*|кредитор\w*)\b",
                         between, re.I):
                continue
            if not marked and not (re.fullmatch(r"\s*\(\s*", between) or
                                   (re.fullmatch(r"\s*[,;]\s*", between) and
                                    re.match(r"\s*[,;]\s*(?:СНИЛС|ИНН)\b", after, re.I))):
                continue
            start = max(0, name.start("name") - 100)
        elif marked and direct:
            start = max(0, match.start() - 100)
        else:
            continue
        value = _normalize(match)
        if value is None:
            malformed = True
            continue
        end = min(len(text), match.end() + 75)
        candidates.append(DebtorBirthDateCandidate(value, text[start:end], start, end))
    values = {candidate.value for candidate in candidates}
    problem = ("invalid_or_incomplete_debtor_birth_date" if malformed else
               "conflicting_debtor_birth_dates" if len(values) > 1 else
               None if candidates else "not_found")
    return DebtorBirthDateResult(
        selected=candidates[0] if candidates and problem is None else None,
        candidates=candidates,
        problem=problem,
    )

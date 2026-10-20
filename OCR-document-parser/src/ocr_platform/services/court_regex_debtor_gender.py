"""Debtor-bound gender proposals from literal grammatical evidence only."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from .court_regex_debtor_full_name import extract_debtor_full_name_candidates

Gender = Literal["Мужской", "Женский"]


@dataclass(frozen=True)
class DebtorGenderCandidate:
    value: Gender
    quote: str
    start: int
    end: int
    requires_confirmation: bool = True


@dataclass(frozen=True)
class DebtorGenderResult:
    selected: DebtorGenderCandidate | None
    candidates: list[DebtorGenderCandidate]
    problem: str | None


_MALE_PATRONYMIC = re.compile(r"(?:вич(?:а|у|ем|е)?|ьич(?:а|у|ем|е)?|оглы)$", re.I)
_FEMALE_PATRONYMIC = re.compile(r"(?:вн(?:а|ы|е|у|ой)|ичн(?:а|ы|е|у|ой)|кызы)$", re.I)
_CITIZEN = re.compile(r"\b(?P<m>граждан(?:ин|ина|ину|ином|ине))\b|"
                      r"\b(?P<f>гражданк(?:а|и|е|у|ой))\b", re.I)
_DIRECT = re.compile(
    r"\bдолжни(?:к[ауе]?|ком|ца|цы|це|цу|цей)\s*[:(,–—-]?\s*"
    r"(?:(?:пол\s*[:–—-]?\s*)?(?P<sex>мужской|женский)|"
    r"(?P<birth>родился|родилась)|(?P<citizen>граждан(?:ин|ина|ину|ином|ине|ка|ки|ке|ку|кой)))\b|"
    r"\bпол\s+должника\s*[:–—-]?\s*(?P<label>мужской|женский)\b", re.I,
)
_OTHER_PERSON = re.compile(
    r"\b(?:управляющ\w*|супруг\w*|муж|жена|жены|женой|сын\w*|доч\w*|"
    r"реб[её]нок|реб[её]нка|дет[ьи]|представител\w*|кредитор\w*)\b", re.I,
)


def _marker_gender(marker: str) -> Gender:
    return "Женский" if re.fullmatch(
        r"женский|родилась|гражданк(?:а|и|е|у|ой)", marker, re.I,
    ) else "Мужской"


def extract_debtor_gender_candidates(text: str) -> DebtorGenderResult:
    """Return proposals requiring confirmation, with original code-point offsets.

    Patronymics come exclusively from the sibling's source-bound debtor names.
    Given names, surnames, pronouns, generic legal prose and other people's
    patronymics never determine gender. Identity conflicts prevent selection even
    when all candidate patronymics have the same grammatical gender.
    """
    names = extract_debtor_full_name_candidates(text)
    identities = names.candidates
    if names.selected is not None:
        selected_identity = names.selected.value.casefold().replace("ё", "е")
        identities = [name for name in identities
                      if name.value.casefold().replace("ё", "е") == selected_identity]
    has_patronymic = any(_MALE_PATRONYMIC.search(name.source_value)
                         or _FEMALE_PATRONYMIC.search(name.source_value)
                         for name in identities)
    candidates: list[DebtorGenderCandidate] = []
    seen: set[tuple[str, int, int]] = set()

    def add(value: Gender, start: int, end: int) -> None:
        key = value, start, end
        if key not in seen:
            seen.add(key)
            candidates.append(DebtorGenderCandidate(value, text[start:end], start, end))

    for name in identities:
        # The source form is preserved by the sibling, including oblique endings.
        source = name.source_value
        position = name.quote.find(source)
        if position < 0:
            continue
        start = name.start + position
        end = start + len(source)
        context = text[max(0, start - 180):start]
        if re.search(r"(?:управляющ\w*|супруг\w*|муж|жен[аыуе]|сын\w*|доч\w*|"
                     r"реб[её]нок|реб[её]нка|дет[ьи]|представител\w*|кредитор\w*)\s+"
                     r"должник\w*\s*[:–—-]?\s*(?:граждан\w*\s*)?$", context, re.I):
            continue
        value: Gender | None = None
        if _MALE_PATRONYMIC.search(source):
            value = "Мужской"
        elif _FEMALE_PATRONYMIC.search(source):
            value = "Женский"
        if value:
            add(value, name.start, name.end)
        before = text[max(0, start - 40):start]
        for marker in _CITIZEN.finditer(before):
            if not has_patronymic and re.fullmatch(r"\s*[:–—-]?\s*", before[marker.end():]):
                add(_marker_gender(marker.group()), max(0, start - 100), end)
        # Only the same identity block may supply a birth/sex marker. Stop before
        # another person's role or a closed parenthesis followed by a sentence.
        full_after = text[end:end + 400]
        after = full_after
        boundary = re.search(r"\)\s*[.;]|[!?]|" + _OTHER_PERSON.pattern, after, re.I)
        if boundary:
            after = after[:boundary.start()]
        for marker in re.finditer(r"\bродил(?:ся|ась)\b|\bпол\s*[:–—-]?\s*(?:мужской|женский)\b",
                                  after, re.I):
            # In "Родилась дочь ..." the subject follows the verb. The role
            # boundary above deliberately truncates before "дочь", so inspect
            # the original tail as well before inheriting a birth marker.
            if re.match(r"\s+(?:(?:его|е[её])\s+)?" + _OTHER_PERSON.pattern,
                        full_after[marker.end():], re.I):
                continue
            # A new sentence or named person cannot inherit the preceding role.
            between = after[:marker.start()]
            if re.search(r"[.;]\s+[А-ЯЁ][а-яё]+\s+[А-ЯЁ]|\b[А-ЯЁ][а-яё]+\s+[А-ЯЁ][а-яё]+", between):
                continue
            word = marker.group().split()[-1]
            add(_marker_gender(word), max(0, start - 100), end + marker.end())

    for marker in _DIRECT.finditer(text):
        # Reject "управляющий должника: ..." and similar other-person clauses.
        before = text[max(0, marker.start() - 90):marker.start()]
        if re.search(r"(?:управляющ\w*|супруг\w*|муж|жен[аыуе]|сын\w*|доч\w*|"
                     r"реб[её]нок|реб[её]нка|дет[ьи]|представител\w*|кредитор\w*)\s*$", before, re.I):
            continue
        word = next(value for value in marker.groups() if value)
        if marker.group("citizen"):
            # Legal templates use masculine "должник-гражданин" for women too.
            # A patronymic supplies the more specific grammatical evidence.
            if has_patronymic or not re.match(r"\s+[А-ЯЁ][А-Яа-яЁё-]+\s+[А-ЯЁ]",
                                             text[marker.end():]):
                continue
        add(_marker_gender(word), max(0, marker.start() - 60), min(len(text), marker.end() + 100))

    # Explicit citizen inflection can identify a debtor without a patronymic,
    # but only when attached to a named bankruptcy subject/action.
    for marker in _CITIZEN.finditer(text):
        if has_patronymic:
            continue
        before = text[max(0, marker.start() - 120):marker.start()]
        after = text[marker.end():marker.end() + 450]
        if _OTHER_PERSON.search(before) or re.search(r"\bпривлечь\b", before, re.I):
            continue
        if not re.search(r"(?:признать|заявлени[еяю]|в\s+отношении)\s*$", before, re.I):
            continue
        if not re.match(r"\s+[А-ЯЁ][А-Яа-яЁё-]+\s+[А-ЯЁ]", after):
            continue
        if not re.search(r"банкрот|несостоятельн", after, re.I):
            continue
        add(_marker_gender(marker.group()), max(0, marker.start() - 100),
            min(len(text), marker.end() + 450))

    values = {candidate.value for candidate in candidates}
    problem = ("conflicting_debtor_names" if names.problem == "conflicting_debtor_names" else
               "conflicting_debtor_gender" if len(values) > 1 else
               None if candidates else "not_found")
    return DebtorGenderResult(candidates[0] if candidates and problem is None else None,
                             candidates, problem)

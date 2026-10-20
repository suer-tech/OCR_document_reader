"""Source-bound debtor name proposals for ``court_decision_ru``.

This is a pure candidate generator. Every proposal requires semantic confirmation;
it does not read previous extraction results, document identifiers or annotations.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, replace
from itertools import product


@dataclass(frozen=True)
class DebtorNameCandidate:
    value: str
    quote: str
    start: int
    end: int
    requires_confirmation: bool
    source_value: str
    normalization: str


@dataclass(frozen=True)
class DebtorNameCandidates:
    selected: DebtorNameCandidate | None
    candidates: list[DebtorNameCandidate]
    problem: str | None


@dataclass(frozen=True)
class HistoricalDebtorIdentityBlock:
    """Exact span of another person's identity in a previous admission order.

    The span starts at the name, so it never masks the admission date before
    it. An immediately following balanced personal-data parenthesis is part of
    the block; following narrative and dates are not.
    """

    value: str
    name_start: int
    name_end: int
    start: int
    end: int
    quote: str


_WORD = r"[А-ЯЁ][а-яё]+(?:[-‐‑][А-ЯЁа-яё][а-яё]+)?"
_FULL_NAME = re.compile(
    rf"(?=(?<![А-Яа-яЁё])(?P<name>(?P<last>{_WORD})\s+"
    rf"(?:\({_WORD}(?:\s*,\s*{_WORD})*\)\s+)?(?P<first>{_WORD})\s+"
    rf"(?P<patronymic>{_WORD}(?:\s+(?:[Оо]глы|[Кк]ызы))?))(?![А-Яа-яЁё]))"
)
_INITIAL_NAME = re.compile(rf"(?<![А-Яа-яЁё]){_WORD}\s+[А-ЯЁ]\.\s*[А-ЯЁ]\.")
_PATRONYMIC = re.compile(
    r"(?:вич(?:а|у|ем|ом|е)?|ьич(?:а|у|ем|ом|е)?|вн(?:а|ы|е|у|ой)|"
    r"ичн(?:а|ы|е|у|ой)|оглы|кызы)$", re.I
)
_OTHER_ROLE = re.compile(
    r"(?:судь[яиею]|управляющ\w*|кредитор\w*|представител\w*|секретар\w*|"
    r"помощник\w*|супруг\w*|сын(?:а|у|ом)?|доч(?:ь|ери|ерью)|"
    r"дет(?:и|ей)|реб[её]н(?:ок|ка|ку|ком))\s*"
    r"(?:(?:(?:имуществ\w*|в\s+деле\s+о\s+банкротстве)\s+)?должник\w*\s*)?"
    r"(?:[:–—-]\s*)?$", re.I
)
_DIRECT_DEBTOR = re.compile(
    r"(?:должник\w*|граждан(?:ин|к)[а-яё]*|заявлени[еяю]\s*(?:(?:граждан(?:ин|к)[а-яё]*|ИП)\s*)?|"
    r"в\s+отношении|о\s+банкротстве|признать)\s*(?:[:–—-]\s*)?(?:гр\.\s*)?$", re.I
)
_REASONING_HEADING = re.compile(r"\bу\s*с\s*т\s*а\s*н\s*о\s*в\s*и\s*л\s*:", re.I)
_OPERATIVE_HEADING = re.compile(
    r"\b(?:р\s*е\s*ш\s*и\s*л|о\s*п\s*р\s*е\s*д\s*е\s*л\s*и\s*л)\s*:", re.I,
)
_HISTORICAL_ADMISSION = re.compile(
    r"\bОпределением\b[^;]{0,450}\bот\s+\d[^;]{0,200}"
    r"\bпринято\s+к\s+производству\s+заявление\s*"
    r"(?:(?:должник|граждан)[а-яё]*(?:\s*[-–—]\s*(?:должник|граждан)[а-яё]*)?\s*)?$", re.I,
)
_CURRENT_RECOGNITION = re.compile(
    r"\bпризнать\s+(?:(?:граждан[а-яё]*|должник[а-яё]*|"
    r"несостоятельн[а-яё]*|банкрот[а-яё]*|[()–—-])\s*)*$", re.I,
)
_MALE_FIRST = {
    "Льва": "Лев", "Петра": "Петр", "Петру": "Петр", "Петром": "Петр",
    "Пётра": "Пётр", "Пётру": "Пётр", "Пётром": "Пётр",
    "Павла": "Павел", "Павлу": "Павел", "Павлом": "Павел",
    "Ильи": "Илья", "Илье": "Илья", "Илью": "Илья", "Ильей": "Илья",
    "Никиты": "Никита", "Никите": "Никита", "Никиту": "Никита",
    "Кузьмы": "Кузьма", "Кузьме": "Кузьма", "Кузьму": "Кузьма",
    "Игоря": "Игорь", "Игорю": "Игорь", "Игорем": "Игорь",
    "Рафаэля": "Рафаэль", "Рафаэлю": "Рафаэль", "Рафаэлем": "Рафаэль",
    "Мусы": "Муса", "Мусу": "Муса", "Мустафы": "Мустафа", "Мустафу": "Мустафа",
    "Исы": "Иса", "Ису": "Иса",
}


# Lexical exceptions describe ordinary Russian names, not document identities.
# Productive first-declension names cannot be distinguished from consonant names
# by -у alone (Савву / Ивану); this small lexicon supplies that grammatical class.
_MALE_A_NAMES = {"Илья", "Никита", "Кузьма", "Муса", "Мустафа", "Иса", "Савва",
                 "Фома", "Лука", "Данила", "Косьма", "Ерёма", "Фока", "Мина"}
_FEMALE_SOFT_NAMES = {"Любовь", "Нинель", "Рахиль", "Адель", "Эсфирь", "Руфь",
                      "Ассоль", "Жизель", "Мишель"}
_FEMALE_INVARIANT_NAMES = {"Нелли", "Лили", "Мэри", "Эмили", "Наоми", "Бетти", "Молли", "Софи"}
_MALE_FIRST.update({"Льву": "Лев", "Львом": "Лев", "Льве": "Лев",
                    "Ильёй": "Илья", "Ильей": "Илья"})


def _surname_nominative(last: str, male: bool) -> str:
    parts = re.split(r"([-‐‑])", last)
    if len(parts) > 1:
        return "".join(part if index % 2 else _surname_nominative(part, male)
                       for index, part in enumerate(parts))
    if male:
        if last.endswith(("ова", "ева", "ёва", "ина", "ына", "ову", "еву", "ёву", "ину", "ыну")):
            return last[:-1]
        if last.endswith(("овым", "евым", "иным", "ыным")):
            return last[:-2]
        match = re.search(r"(ск|цк)(?:ого|ому|им|ом)$", last)
        if match:
            return last[:match.start()] + match[1] + "ий"
        # Only productive stems with an unambiguous regular reversal.
        return re.sub(r"((?:ян|ман))(?:а|у|ом|е)$", r"\1", last)
    if last.endswith(("овой", "евой", "ёвой", "иной", "ыной")):
        return last[:-2] + "а"
    if last.endswith(("ову", "еву", "ёву", "ину", "ыну")):
        return last[:-1] + "а"
    if last.endswith(("ую", "ой")):
        return last[:-2] + "ая"
    return last


def _first_nominative(first: str, male: bool, instrumental: bool) -> str:
    if male:
        if first in _MALE_FIRST:
            return _MALE_FIRST[first]
        for nominal in _MALE_A_NAMES:
            stem = nominal[:-1]
            endings = (("и", "е", "ю", "ей", "ёй", "ею", "ёю") if nominal.endswith("я") else
                       (("и" if stem[-1] in "гкхжчшщ" else "ы"), "е", "у", "ой", "ою"))
            if first == nominal or first in {stem + ending for ending in endings}:
                return nominal
        if instrumental:
            if first.endswith("ом"):
                return first[:-2]
            if first.endswith(("ием", "еем", "аем")):
                return first[:-2] + "й"
            if first.endswith("ем"):
                return first[:-2] + "ь"
        if first.endswith(("ия", "ию", "ея", "ею", "ая", "аю")):
            return first[:-1] + "й"
        if first.endswith(("я", "ю")):
            return first[:-1] + "ь"
        if first.endswith(("а", "у")):
            return first[:-1]
        if first.endswith("ы"):
            return first[:-1] + "а"
        return first
    if first in _FEMALE_INVARIANT_NAMES:
        return first
    for nominal in _FEMALE_SOFT_NAMES:
        if first in {nominal, nominal[:-1] + "и", nominal + "ю"}:
            return nominal
    if first.endswith(("ии", "ию")):
        return first[:-1] + "я"
    if first.endswith(("ьи", "ье", "ью")):
        return first[:-1] + "я"
    if instrumental and first.endswith("ей"):
        return first[:-2] + "я"
    if first.endswith(("ы", "у")):
        return first[:-1] + "а"
    if first.endswith("и"):
        return first[:-1] + ("а" if first[-2] in "гкхжчшщ" else "я")
    if first.endswith("ой"):
        return first[:-2] + "а"
    if first.endswith("ю"):
        return first[:-1] + "я"
    if first.endswith("е"):
        return first[:-1] + ("я" if first[-2] in "аоеёиюяьй" else "а")
    return first


def _nominative(last: str, first: str, patronymic: str) -> str:
    """Apply grammatical classes; preserve unresolvable surname stems."""
    patronymic = re.sub(r"\s+", " ", patronymic)
    male = bool(re.search(r"(?:вич|ьич)(?:а|у|ем|ом|е)?$|оглы$", patronymic, re.I))
    oblique = bool(re.search(r"(?:вич|ьич)(?:а|у|ем|ом|е)$|(?:в|ич)н(?:ы|е|у|ой)$", patronymic))
    instrumental = bool(re.search(r"(?:вич|ьич)(?:ем|ом)$|(?:в|ич)ной$", patronymic))
    if patronymic.casefold().endswith(" оглы"):
        oblique = bool(re.search(r"(?:ов|ев|ёв|ин|ын)(?:а|у|ым)$", last))
        instrumental = last.endswith(("овым", "евым", "иным", "ыным"))
    if not oblique:
        return f"{last} {first} {patronymic}"
    last = _surname_nominative(last, male)
    first = _first_nominative(first, male, instrumental)
    if male:
        patronymic = re.sub(r"((?:вич|ьич))(?:а|у|ем|ом|е)$", r"\1", patronymic)
    else:
        patronymic = re.sub(r"((?:в|ич)н)(?:ы|е|у|ой)$", r"\1а", patronymic)
    return f"{last} {first} {patronymic}"


def _source_parts(candidate: DebtorNameCandidate) -> tuple[str, str, str]:
    match = _FULL_NAME.match(candidate.source_value)
    assert match is not None
    return tuple(re.sub(r"\s+", " ", match.group(part))
                 for part in ("last", "first", "patronymic"))


def _unresolved_surname_case(last: str, patronymic: str, value: str) -> bool:
    """Flag a reverse inflection that has no unique regular nominative.

    An unchanged -а can also be the literal nominative (Сила). Keep the source
    proposal, but do not present the partial normalization as a resolved one.
    Written nominative corroboration below can remove this limitation.
    """
    # -овой may be -ова or -овая; -овым may be -ов, -овый or -овой.
    # Retain the regular proposal for JEV, but do not claim a unique reversal.
    if (re.search(r"(?:в|ич)н(?:ы|е|ой)$", patronymic)
            and last.endswith(("овой", "евой", "ёвой", "иной", "ыной"))):
        return True
    if (re.search(r"(?:вич|ьич)(?:ем|ом)$", patronymic)
            and last.endswith(("овым", "евым", "иным", "ыным"))):
        return True
    return (
        bool(re.search(r"(?:вич|ьич)(?:а|у|ем|ом|е)$", patronymic))
        and last == value.split()[0]
        and bool(re.search(r"(?:ого|его|ому|ему|ым|им|ом|[бвгджзклмнпрстфхцчшщ][ау])$", last, re.I))
    )


def _mention_context(text: str, start: int, reasoning_start: int | None,
                     operative_start: int | None) -> str:
    """Distinguish current identity anchors from a previous admission order."""
    if reasoning_start is not None and start < reasoning_start:
        return "current_header"
    before = text[max(0, start - 700):start]
    if operative_start is not None and start > operative_start:
        if _CURRENT_RECOGNITION.search(before[-180:]):
            return "current_recognition"
        return "current_other"
    if _HISTORICAL_ADMISSION.search(before):
        return "historical_admission"
    return "other"


def _case_forms(word: str, *, male: bool, surname: bool = False) -> set[str]:
    """Generate regular forms of an explicitly written nominative token.

    This is forward corroboration against another source mention, not reverse
    guessing of an unseen spelling or fuzzy correction of OCR characters.
    """
    parts = re.split(r"([-‐‑])", word)
    if surname and len(parts) > 1:
        choices = [[part] if index % 2 else _case_forms(part, male=male, surname=True)
                   for index, part in enumerate(parts)]
        return {"".join(combination).casefold().replace("ё", "е") for combination in product(*choices)}
    forms = {word}
    if word.endswith((" Оглы", " оглы", " Кызы", " кызы")):
        father, suffix = word.rsplit(" ", 1)
        forms.update(f"{variant} {suffix}" for variant in _case_forms(father, male=True))
    elif male:
        if surname and word.endswith(("ский", "цкий")):
            forms.update(word[:-2] + ending for ending in ("ого", "ому", "им", "ом"))
        elif surname and word.endswith(("ый", "ой")):
            forms.update(word[:-2] + ending for ending in ("ого", "ому", "ым", "ом"))
        elif word.endswith(("й", "ь")):
            forms.update(word[:-1] + ending for ending in ("я", "ю", "ем", "е"))
        elif word.endswith("а"):
            forms.update(word[:-1] + ending for ending in (("и" if word[-2] in "гкхжчшщ" else "ы"), "е", "у", "ой", "ою"))
        elif word.endswith("я"):
            forms.update(word[:-1] + ending for ending in ("и", "е", "ю", "ей", "ею"))
        elif word[-1].casefold() not in "аеёиоуыэюя":
            forms.update(word + ending for ending in ("а", "у", "ом", "ем", "е"))
            if surname and word.endswith(("ов", "ев", "ёв", "ин", "ын")):
                forms.add(word + "ым")
        if not surname:
            forms.update(form for form, nominative in _MALE_FIRST.items() if nominative == word)
    elif surname and word.endswith("ая"):
        forms.update(word[:-2] + ending for ending in ("ой", "ую"))
    elif word.endswith("ия"):
        forms.update(word[:-1] + ending for ending in ("и", "ю", "ей", "ею"))
    elif word.endswith("а"):
        genitive = "и" if word[-2] in "гкхжчшщ" else "ы"
        forms.update(word[:-1] + ending for ending in (genitive, "е", "у", "ой", "ою"))
    elif word.endswith("я"):
        forms.update(word[:-1] + ending for ending in ("и", "е", "ю", "ей", "ею"))
    elif word.endswith("ь"):
        forms.update(word[:-1] + ending for ending in ("и", "ью"))
    return {form.casefold().replace("ё", "е") for form in forms}


def _nominal_source(candidate: DebtorNameCandidate) -> bool:
    last, first, patronymic = _source_parts(candidate)
    if not re.search(r"(?:вич|ьич|вна|ична| оглы| кызы)$", patronymic, re.I):
        return False
    return candidate.value == f"{last} {first} {patronymic}"


def _same_source_person(nominal: DebtorNameCandidate, other: DebtorNameCandidate) -> bool:
    reference = _source_parts(nominal)
    observed = _source_parts(other)
    male = bool(re.search(r"(?:вич|ьич| оглы)$", reference[2], re.I))
    return all(
        word.casefold().replace("ё", "е") in _case_forms(base, male=male, surname=index == 0)
        for index, (base, word) in enumerate(zip(reference, observed))
    )


def _role_score(text: str, start: int, end: int) -> int:
    before = text[max(0, start - 220):start]
    after = text[end:end + 360]
    if _OTHER_ROLE.search(before):
        return 0
    if re.search(r"\bуправляющ\w*(?:[\s–—-]+[а-яё]+){0,9}\s*[–—-]?\s*$", before, re.I):
        return 0
    if re.search(r"\bпривлечь\b[^.;]{0,130}$", before, re.I):
        return 0
    if re.search(r"(?:истребовать[^.;]{0,100}|лицо,\s*участвующее\s+в\s+деле\s+о\s+банкротстве:)\s*$",
                 before, re.I):
        return 0
    if re.search(r"(?:состоит\s+в\s+(?:зарегистрированном\s+)?браке\s+с|"
                 r"финансов\w*\s+управляющ\w*\s+по\s+делу\s+о\s+банкротстве)\s*"
                 r"(?:граждан(?:ин|к)[а-яё]*\s*)?$", before, re.I):
        return 0
    if re.match(r"[^.;]{0,110}(?:согласи[ея]\s+на\s+утверждение|за\s+должника)", after, re.I):
        return 0
    if re.match(r"\s*[,;(]?\s*(?:далее[^)]{0,55})?\bдолжник\b", after, re.I):
        return 10
    if re.match(r"[^.;]{0,160}\bобратил(?:ся|ась)\b", after, re.I) and re.search(
        r"(?:признани|банкрот|несостоятельн)", after, re.I
    ):
        return 9
    extended_after = text[end:end + 1100]
    if re.match(r"\s*\([^)]{0,850}\)\s*обратил(?:ся|ась)\b", extended_after, re.I):
        if re.search(r"признани|банкрот|несостоятельн", extended_after, re.I):
            return 9
    if re.search(r"\bпризнать\s+(?:граждан(?:ин|к)[а-яё]*\s*)?$", before, re.I):
        return 9
    if _DIRECT_DEBTOR.search(before):
        return 7
    if re.search(r"(?:признани[ияею]|несостоятельност[ьи])[^.;]{0,85}$", before, re.I):
        if re.search(r"банкрот|несостоятельн", before + after[:120], re.I):
            return 6
    return 0


def extract_debtor_full_name_candidates(text: str) -> DebtorNameCandidates:
    """Return debtor proposals with exact evidence slices in the original text.

    ``start``/``end`` address ``quote`` (Python string code points). A selected
    proposal is only a suggestion for the confirmation stage. Conflicting full
    names deliberately leave selection empty.
    """
    proposals: list[tuple[int, DebtorNameCandidate]] = []
    references: list[tuple[int, DebtorNameCandidate]] = []
    contexts: dict[DebtorNameCandidate, str] = {}
    reasoning = _REASONING_HEADING.search(text)
    operative = _OPERATIVE_HEADING.search(text, reasoning.end() if reasoning else 0)
    for match in _FULL_NAME.finditer(text):
        last, first, patronymic = match.group("last", "first", "patronymic")
        if not _PATRONYMIC.search(patronymic):
            continue
        if patronymic.casefold() in {"оглы", "кызы"}:
            continue
        name_start, name_end = match.span("name")
        score = _role_score(text, name_start, name_end)
        # Include the action after a potentially long birth/address parenthesis.
        evidence_tail = 1100 if score == 9 else 220
        start, end = max(0, name_start - 180), min(len(text), name_end + evidence_tail)
        value = _nominative(last, first, patronymic)
        surface = f"{last} {first} {patronymic}"
        transformations = []
        if value != re.sub(r"\s+", " ", surface):
            transformations.append("regular_case_endings")
        if _unresolved_surname_case(last, patronymic, value):
            transformations.append("unresolved_surname_nominative")
        if "(" in match.group("name"):
            transformations.append("omit_parenthesized_previous_surnames")
        candidate = DebtorNameCandidate(
            value=value,
            quote=text[start:end], start=start, end=end,
            requires_confirmation=True,
            source_value=match.group("name"),
            normalization="+".join(transformations) or "whitespace_only",
        )
        if score:
            proposals.append((score, candidate))
            contexts[candidate] = _mention_context(
                text, name_start, reasoning.start() if reasoning else None,
                operative.start() if operative else None,
            )
        # An otherwise unanchored nominative mention may corroborate the same
        # debtor when it introduces a birth date and registration statement.
        biography = text[name_end:name_end + 650]
        personal_statement = re.match(r"\s*,\s*\d{1,2}\.\d{1,2}\.\d{4}", biography)
        if _nominal_source(candidate) and (
            score or (personal_statement and re.search(r"зарегистрирован", biography, re.I))
        ):
            references.append((score, candidate))
    if not proposals:
        for match in _INITIAL_NAME.finditer(text):
            score = _role_score(text, match.start(), match.end())
            if score:
                start, end = max(0, match.start() - 180), min(len(text), match.end() + 220)
                proposals.append((score, DebtorNameCandidate(
                    value=re.sub(r"\s+", " ", match.group()),
                    quote=text[start:end], start=start, end=end,
                    requires_confirmation=True,
                    source_value=match.group(), normalization="whitespace_only",
                )))
        candidates = [candidate for _, candidate in sorted(proposals, key=lambda p: -p[0])]
        conflict = len({re.sub(r"\s+", "", c.value).casefold() for c in candidates}) > 1
        return DebtorNameCandidates(
            selected=candidates[0] if candidates and not conflict else None,
            candidates=candidates,
            problem=("conflicting_debtor_names" if conflict else
                     "initials_only" if candidates else "not_found"),
        )
    grouped: dict[str, list[tuple[int, DebtorNameCandidate]]] = defaultdict(list)
    canonical_mentions: dict[str, DebtorNameCandidate] = {}
    references.sort(key=lambda item: (-item[0], item[1].start))
    for score, candidate in proposals:
        compatible = [reference for _, reference in references
                      if _same_source_person(reference, candidate)]
        distinct_references = {reference.value.casefold().replace("ё", "е")
                               for reference in compatible}
        key = candidate.value.casefold().replace("ё", "е")
        if len(distinct_references) == 1:
            reference = compatible[0]
            key = reference.value.casefold().replace("ё", "е")
            canonical_mentions[key] = reference
        grouped[key].append((score, candidate))
    for key, reference in canonical_mentions.items():
        group = grouped[key]
        if not any(reference == candidate for _, candidate in group):
            closest = min((candidate for _, candidate in group),
                          key=lambda candidate: abs(candidate.start - reference.start))
            start, end = min(reference.start, closest.start), max(reference.end, closest.end)
            group.append((0, replace(
                reference, quote=text[start:end], start=start, end=end,
                normalization=reference.normalization + "+source_nominative_corroboration",
            )))
    ranked = sorted(grouped.values(), key=lambda group: (-max(p[0] for p in group), -len(group)))
    candidates = []
    for group in ranked:
        reference_values = {reference.value for reference in canonical_mentions.values()
                            if any(_same_source_person(reference, candidate) for _, candidate in group)}
        candidates.extend(candidate for _, candidate in sorted(
            group, key=lambda p: (p[1].value not in reference_values, not _nominal_source(p[1]),
                                  "unresolved_surname_nominative" in p[1].normalization,
                                  -p[0], p[1].start),
        ))
    # A previous admission order is not a second current recognition. Resolve
    # it only when the same full identity is independently anchored in both
    # the current header and current operative recognition. Counts alone never
    # resolve different people, and all source candidates remain available.
    current_groups = [group for group in ranked if {
        contexts.get(candidate) for _, candidate in group
    } >= {"current_header", "current_recognition"}]
    active_groups = ranked
    if len(current_groups) == 1:
        active_groups = [group for group in ranked if not all(
            contexts.get(candidate) == "historical_admission" for _, candidate in group
        )]
    conflict = len(active_groups) > 1 and max(p[0] for p in active_groups[1]) >= 7
    choice = None if conflict else candidates[0]
    if not conflict and len(active_groups) < len(ranked):
        current_group = current_groups[0]
        current_candidates = [candidate for candidate in candidates
                              if any(candidate == item for _, item in current_group)]
        choice = current_candidates[0]
        anchors = [candidate for _, candidate in current_group
                   if contexts.get(candidate) in {"current_header", "current_recognition"}]
        start, end = min(c.start for c in anchors), max(c.end for c in anchors)
        choice = replace(choice, quote=text[start:end], start=start, end=end,
                         normalization=choice.normalization + "+current_identity_anchors")
        candidates.insert(0, choice)
    unresolved = choice is not None and "unresolved_surname_nominative" in choice.normalization
    # -ого/-его is certainly oblique, but -а can itself be a source nominative
    # (Сила). Keep the latter as an explicitly unresolved source proposal for
    # semantic verification instead of losing a possibly correct literal name.
    certainly_oblique = unresolved and bool(re.search(r"(?:ого|его)$", _source_parts(choice)[0]))
    return DebtorNameCandidates(
        selected=None if certainly_oblique else choice, candidates=candidates,
        problem=("conflicting_debtor_names" if conflict else
                 "ambiguous_nominative" if unresolved else None),
    )


def ignored_historical_debtor_identity_blocks(text: str) -> list[HistoricalDebtorIdentityBlock]:
    """Return proven historical other-person blocks without masking history.

    Available only when the current header and operative recognition agree and
    there is no other current-identity conflict. Reuses the same debtor rules;
    there is no majority vote or access to human annotations. Other field
    extractors may reject identifiers *inside* these exact source spans while
    retaining, for example, the preceding application-acceptance date.
    """
    result = extract_debtor_full_name_candidates(text)
    selected = result.selected
    if selected is None or "current_identity_anchors" not in selected.normalization:
        return []
    reasoning = _REASONING_HEADING.search(text)
    operative = _OPERATIVE_HEADING.search(text, reasoning.end() if reasoning else 0)
    selected_key = selected.value.casefold().replace("ё", "е")
    candidates = {(candidate.source_value, candidate.value) for candidate in result.candidates}
    blocks = []
    for match in _FULL_NAME.finditer(text):
        name_start, name_end = match.span("name")
        if _mention_context(text, name_start, reasoning.start() if reasoning else None,
                            operative.start() if operative else None) != "historical_admission":
            continue
        value = _nominative(match.group("last"), match.group("first"), match.group("patronymic"))
        if value.casefold().replace("ё", "е") == selected_key:
            continue
        if (match.group("name"), value) not in candidates:
            continue
        end = name_end
        after_name = re.match(r"\s*\(", text[name_end:])
        if after_name:
            opening = name_end + after_name.end() - 1
            depth = 0
            # A missing or excessive parenthesis never swallows later clauses.
            limit = min(len(text), opening + 2500,
                        operative.start() if operative else len(text))
            for position in range(opening, limit):
                if text[position] == "(":
                    depth += 1
                elif text[position] == ")":
                    depth -= 1
                    if depth == 0:
                        end = position + 1
                        break
        blocks.append(HistoricalDebtorIdentityBlock(
            value=value, name_start=name_start, name_end=name_end,
            start=name_start, end=end, quote=text[name_start:end],
        ))
    return blocks

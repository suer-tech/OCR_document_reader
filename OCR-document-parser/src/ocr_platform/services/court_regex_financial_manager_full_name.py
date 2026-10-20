"""Source-bound appointment candidates; every result still needs confirmation.

Only operative appointments are considered. Names from nominations, appearances,
fee awards and earlier decisions are deliberately not used to fill missing names.
The offsets and quote always refer to the unmodified input string.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Candidate:
    value: str
    quote: str
    start: int
    end: int
    rule: str = "operative_appointment"
    requires_confirmation: bool = True


@dataclass(frozen=True)
class Result:
    selected: Candidate | None
    candidates: tuple[Candidate, ...]
    problem: str | None = None
    requires_confirmation: bool = True


_WORD = r"[А-ЯЁ][а-яё]+(?:[-‑][А-ЯЁ][а-яё]+)?"
_PATRONYMIC = (
    r"[А-ЯЁ][а-яё]*(?:вич(?:а|у|ем)?|вн(?:а|у|ы|е|ой)|ич(?:а|у|ем)?|ичн(?:а|у|ы|е|ой))"
)
_NAME = re.compile(
    rf"(?<![А-Яа-яЁё])(?P<full>{_WORD}\s+{_WORD}\s+{_PATRONYMIC})(?![а-яё])"
    rf"|(?<![А-Яа-яЁё])(?P<initials>{_WORD}\s+[А-ЯЁ]\.\s*[А-ЯЁ]\.?)"
)
_OPERATIVE = re.compile(
    r"(?:^|\n)\s*\*{0,2}(?:р\s*е\s*ш\s*и\s*л|о\s*п\s*р\s*е\s*д\s*е\s*л\s*и\s*л)\s*(?::\*{0,2}|(?=\n))",
    re.IGNORECASE,
)
_APPOINT = re.compile(r"\b(?:утвердить|назначить)\b", re.IGNORECASE)
_ROLE = re.compile(
    r"\b(?:(?:финансовым|арбитражным)\s+управляющим|(?:в\s+должности|на\s+должность)\s+финансового\s+управляющего|управляющим\s+имуществом)\b",
    re.IGNORECASE,
)
_NEXT = re.compile(
    r"\n\s*(?:\d+[.)]\s*)?(?:Обязать|Привлечь|Установить|Разъяснить|Назначить|Утвердить|Истребовать|Должнику|Финансовому|Решение|Определение|Судебное|С\s+даты)\b"
)


def _nominative(name: str) -> str:
    """Conservative regular Russian inflections, without expanding initials."""
    name = re.sub(r"\s+", " ", name).strip()
    if re.search(r"[А-ЯЁ]\.", name):
        surname, initials = name.split(" ", 1)
        if surname.endswith(("ову", "еву", "ину")):
            surname = surname[:-1] + "а"
        elif surname.endswith(("овой", "евой", "иной")):
            surname = surname[:-2] + "а"
        return surname + " " + re.sub(r"\s+", "", initials)
    surname, given, patronymic = name.split(" ")
    if re.search(r"(?:вн|ичн)(?:у|ы|е|ой|а)$", patronymic):
        patronymic = re.sub(r"(вн|ичн)(?:у|ы|е|ой|а)$", r"\1а", patronymic)
        if given.endswith("ию"):
            given = given[:-2] + "ия"
        elif given.endswith("ью"):
            given = given[:-1]
        elif given.endswith("у") and given not in {"Алсу"} and not given.endswith("иу"):
            given = given[:-1] + "а"
        elif given.endswith("ии"):
            given = given[:-2] + "ия"
        if surname.endswith(("ову", "еву", "ину")):
            surname = surname[:-1] + "а"
        elif surname.endswith("ую"):
            surname = surname[:-2] + "ая"
        elif surname.endswith(("овой", "евой", "иной")):
            surname = surname[:-2] + "а"
    elif patronymic.endswith("ича"):
        patronymic = patronymic[:-1]
        irregular = {"Павла": "Павел", "Льва": "Лев", "Петра": "Петр", "Илью": "Илья"}
        if given in irregular:
            given = irregular[given]
        elif given.endswith("ия"):
            given = given[:-2] + "ий"
        elif given.endswith("ея"):
            given = given[:-1] + "й"
        elif given.endswith("а"):
            given = given[:-1]
        if surname.endswith(("ова", "ева", "ина", "ына")):
            surname = surname[:-1]
        elif surname.endswith("ского"):
            surname = surname[:-3] + "ий"
    return f"{surname} {given} {patronymic}"


def extract_financial_manager_full_name(text: str) -> Result:
    """Return explicit current appointment candidates, never an inferred person."""
    if not isinstance(text, str) or not text.strip():
        return Result(None, (), "empty_text")
    operative = list(_OPERATIVE.finditer(text))
    if not operative:
        return Result(None, (), "operative_part_not_found")
    body_start = operative[-1].end()
    candidates: list[Candidate] = []
    for appointment in _APPOINT.finditer(text, body_start):
        line_start = max(
            body_start, text.rfind("\n", body_start, appointment.start()) + 1
        )
        line_prefix = text[line_start : appointment.start()]
        if re.search(
            r"(?:Определением|Решением|ранее|просит|просил[аи]?|предлагает|предложил[аи]?)\b",
            line_prefix,
            re.IGNORECASE,
        ):
            continue
        end = min(appointment.end() + 1400, len(text))
        stop = _NEXT.search(text, appointment.end(), end)
        if stop:
            end = stop.start()
        clause = text[appointment.end() : end]
        role = _ROLE.search(clause)
        # End the sentence outside requisites in parentheses. In particular do
        # not absorb names of spouses/children from the following orders.
        depth = 0
        for index, character in enumerate(clause):
            if character == "(":
                depth += 1
            elif character == ")":
                depth = max(0, depth - 1)
            elif (
                character == "."
                and depth == 0
                and index
                and (not role or index > role.end())
                and (clause[index - 1].islower() or clause[index - 1] == ")")
                and re.match(r"\s*(?:\n|[А-ЯЁ])", clause[index + 1 :])
            ):
                clause = clause[: index + 1]
                break
        role = _ROLE.search(clause)
        prefix = text[max(body_start, appointment.start() - 260) : appointment.start()]
        # Inverted syntax: "Финансовым управляющим должника утвердить ФИО".
        prefix_role = re.search(
            r"(?:^|\n)\s*(?:\d+[.)]\s*)?Финансовым\s+управляющим[^.;:]{0,220}$",
            prefix,
            re.IGNORECASE,
        )
        if not role and not prefix_role:
            continue
        names = list(_NAME.finditer(clause))

        # An unclosed requisites parenthesis must not turn a later spouse into
        # the manager. Names inside parentheses are annotations, not subjects.
        def outside_parentheses(match: re.Match[str], source: str = clause) -> bool:
            nesting = 0
            for char in source[: match.start()]:
                if char == "(":
                    nesting += 1
                elif char == ")":
                    nesting = max(0, nesting - 1)
            return nesting == 0

        names = [name for name in names if outside_parentheses(name)]
        if not names:
            continue
        # A name before the role is its object: "Утвердить ФИО (...) финансовым
        # управляющим имуществом ФИО должника". Otherwise the last full name in
        # the appointment precedes registry/address metadata and follows debtor.
        before = [m for m in names if role and m.end() <= role.start()]
        after = [m for m in names if not role or m.start() >= role.end()]
        chosen = before[0] if before else (after[-1] if after else None)
        if chosen is None:
            continue
        start = appointment.end() + chosen.start()
        finish = appointment.end() + chosen.end()
        candidates.append(
            Candidate(_nominative(chosen.group()), text[start:finish], start, finish)
        )
    if not candidates:
        return Result(None, (), "appointment_name_not_found")
    distinct = {c.value.casefold().replace("ё", "е") for c in candidates}
    if len(distinct) > 1:
        return Result(None, tuple(candidates), "conflicting_appointments")
    selected = candidates[-1]
    # The source can itself repeat the debtor as manager. Surface the conflict;
    # do not replace the source with a different name from a nomination.
    first_appointment = _APPOINT.search(text, body_start)
    debtor_part = (
        text[body_start : first_appointment.start()] if first_appointment else ""
    )
    for name in _NAME.finditer(debtor_part):
        preceding = debtor_part[max(0, name.start() - 110) : name.start()]
        if re.search(
            r"(?:Признать|Ввести\s+в\s+отношении)\b", preceding, re.IGNORECASE
        ) and _nominative(name.group()).casefold().replace(
            "ё", "е"
        ) == selected.value.casefold().replace("ё", "е"):
            return Result(None, tuple(candidates), "source_conflict_manager_is_debtor")
    return Result(selected, tuple(candidates))

"""Source-only proposals for a debtor birthplace in a personal identity block.

This service proposes evidence; confirmation belongs to the caller. It does not
infer geography, repair place names, or use other extracted fields.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class DebtorBirthPlaceCandidate:
    value: str
    quote: str
    start: int
    end: int
    requires_confirmation: bool = True


@dataclass(frozen=True)
class DebtorBirthPlaceResult:
    selected: DebtorBirthPlaceCandidate | None
    candidates: list[DebtorBirthPlaceCandidate]
    problem: str | None


_WORD = r"[А-ЯЁ][а-яё]+(?:[-‐‑][А-Яа-яЁё][а-яё]+)?"
_NAME = re.compile(
    rf"(?=(?<![А-Яа-яЁё])(?P<name>{_WORD}\s+"
    rf"(?:\({_WORD}(?:\s*,\s*{_WORD})*\)\s+)?"
    rf"{_WORD}\s+{_WORD}(?:\s+(?:Оглы|Кызы))?)(?![А-Яа-яЁё]))"
)
_MARKER = re.compile(
    r"(?P<combined>дат[аы]\s+и\s+мест[оа]\s+рождени[яе])|"
    r"мест[оа]\s+(?:рождени[яе]|рожд\.?)(?:\s+должник[ауы]?)?|"
    r"\bм\.\s*р\.|\bурож\.|\bрод\.|урожен(?:ец|ца|ка|ки|ку|цем|кой)|родил(?:ся|ась)",
    re.I,
)
# Positional identity syntax: DOB, literal settlement, next identity field.
# A date without a birth label is accepted only directly in a name's bracket.
_POSITIONAL = re.compile(
    r"(?<![\d.])\d{1,2}\s*\.\s*\d{1,2}\s*\.\s*\d{4}"
    r"\s*(?P<birth_label>года?\s+рожд(?:ения|\.)|г\.\s*р\.)?\s*[,;]\s*"
    r"(?=(?:г\.|гор\.|город\b|с\.|село\b|д\.|дер\.|деревня\b|"
    r"п\.|пос\.|поселок\b|посёлок\b|пгт\.?|ст\.|станица\b)\s*[А-ЯЁ])",
    re.I,
)

_DATE_PREFIX = re.compile(
    r"\s*[:–—-]?\s*(?:[«\"]?\d{1,2}[»\"]?\s*[.]\s*\d{1,2}\s*[.]\s*\d{4}|"
    r"[«\"]?\d{1,2}[»\"]?\s+(?:января|февраля|марта|апреля|мая|июня|"
    r"июля|августа|сентября|октября|ноября|декабря)\s+\d{4})"
    r"\s*(?:года?\s+рождения|года?|г\.\s*р\.|г\.)?\s*[,;:]?\s*",
    re.I,
)
_STOP = re.compile(
    r"\b(?:ИНН|СНИЛС|ОГРНИП|паспорт|номер\s+индивидуального|"
    r"страхов(?:ой|ого)\s+номер|дат[аы]\s+рождения|"
    r"адрес\w*|мест[оа]\s+(?:жительства|регистрации|пребывания)|"
    r"зарегистрирован\w*|прожива\w*|фактическ\w*\s+прожива\w*|"
    r"пол\s*[:–—-]|гражданство|несостоятельн\w*|банкрот\w*|"
    r"о\s+признании|обратил\w*|обратилась|является|состоящий|"
    r"финансов\w*\s+управляющ\w*|должник\w*|супруг\w*|"
    r"улица|квартира)\b|\b(?:ул|кв)\.|"
    r"(?<!\d)\d{1,2}\s*\.\s*\d{1,2}\s*\.\s*\d{4}(?!\d)|"
    r"\b\d{1,2}\s+(?:января|февраля|марта|апреля|мая|июня|июля|"
    r"августа|сентября|октября|ноября|декабря)\s+\d{4}\b|[;\n]\s*\n",
    re.I,
)
_OTHER = re.compile(
    r"(?:управляющ\w*|супруг\w*|кредитор\w*|представител\w*|"
    r"судь[яиею]|дет[ьи]|сын\w*|доч\w*|реб[её]нок|реб[её]нка)"
    r"(?:\s+\w+){0,3}\s*[:,–—-]?\s*$",
    re.I,
)
_ROLE = re.compile(
    r"(?:должник\w*|должниц\w*|граждан\w*|признать|"
    r"(?:ввести|открыть)\s+в\s+отношении|о\s+банкротстве|несостоятельности\s*\(банкротстве\))"
    r"\s*[:,–—-]?\s*(?:(?:ИП|гр\.)\s*)?$",
    re.I,
)
_APPLICATION = re.compile(r"заявлени[еяюй]\s*(?:о\s+банкротстве\s*)?[:–—-]?\s*$", re.I)
_BANKRUPTCY = re.compile(
    r"о\s+признании|несостоятельн\w*|банкрот\w*|далее\s*[-–—]?\s*должник", re.I
)


def _identity_start(text: str, marker: re.Match[str]) -> int | None:
    before = text[max(0, marker.start() - 800) : marker.start()]
    if re.search(r"должник[ауы]?$", marker.group(), re.I):
        return max(0, marker.start() - 80)
    if not _OTHER.search(before) and re.search(
        r"(?:должник\w*|должниц\w*)\s*[,(:]?\s*$", before, re.I
    ):
        return max(0, marker.start() - 80)
    names = [
        m
        for m in _NAME.finditer(text, max(0, marker.start() - 800), marker.start())
        if not (
            m.group("name").split()[-1] in {"Оглы", "Кызы"}
            and len(m.group("name").split()) == 3
        )
    ]
    if not names:
        return None
    name = names[-1]
    prefix = text[max(0, name.start("name") - 220) : name.start("name")]
    between = text[name.end("name") : marker.start()]
    if _OTHER.search(prefix) or re.search(
        r"\b(?:супруг\w*|дет[ьи]|сын\w*|доч\w*|реб[её]нок|реб[её]нка|управляющ\w*)\b",
        between,
        re.I,
    ):
        return None
    if re.search(r"[!?]|\)\s*[.;]\s*\S", between):
        return None
    after = text[marker.end() : marker.end() + 650]
    if _ROLE.search(prefix) or (
        _APPLICATION.search(prefix) and _BANKRUPTCY.search(after)
    ):
        return max(0, name.start("name") - 100)
    # A name followed by a parenthesized identity and an explicit debtor label.
    if re.search(
        r"\(далее\s*[-–—]?\s*должник\)|\)\s*(?:[,–—-]\s*)?(?:далее\s*[-–—]?\s*)?должник\b",
        after,
        re.I,
    ):
        return max(0, name.start("name") - 100)
    if re.search(
        r"\)\s+обратил(?:ся|ась)\s+в\s+(?:Арбитражный\s+суд|суд)", after, re.I
    ) and re.search(
        r"с\s+заявлением[^.]{0,180}(?:банкрот|несостоятельн)",
        after,
        re.I,
    ):
        return max(0, name.start("name") - 100)
    return None


def _normalize(raw: str) -> str:
    value = re.sub(r"\s+", " ", raw).strip(" ,;:–—-")
    value = re.sub(r"(?<=[А-Яа-яЁё])[-‐‑]\s+(?=[А-Яа-яЁё])", "-", value)
    value = re.sub(r"(?<!\w)[pр]-н[aа](?!\w)", "р-на", value, flags=re.I)
    value = re.sub(r"\s+([,;])", r"\1", value)
    value = re.sub(
        r"\b(г|гор|д|с|п|пос|пгт|ст|обл|респ)\.\s*",
        lambda m: m[1].lower() + ". ",
        value,
        flags=re.I,
    )
    value = value.strip()
    value = re.sub(r"(?<=[А-Яа-яЁё]{5})\.$", "", value)
    if raw.isupper():
        value = re.sub(
            r"[А-ЯЁ]{2,}",
            lambda m: (
                m[0]
                if m[0]
                in {
                    "СССР",
                    "ССР",
                    "РСФСР",
                    "РФ",
                    "РД",
                    "АССР",
                    "КБАССР",
                    "ТАССР",
                }
                else m[0].capitalize()
            ),
            value,
        )
    return re.sub(r"\s+([,;])", r"\1", value).strip()


def _value_end(text: str, start: int) -> int:
    limit = min(len(text), start + 350)
    stop = _STOP.search(text, start, limit)
    if stop:
        limit = stop.start()
    depth = 0
    for index in range(start, limit):
        char = text[index]
        if char == "(":
            depth += 1
        elif char == ")":
            if depth == 0:
                return index
            depth -= 1
        elif char == "." and depth == 0:
            preceding = re.search(r"[А-Яа-яЁё-]+$", text[start:index])
            if (
                preceding
                and preceding[0].casefold()
                not in {
                    "г",
                    "гор",
                    "д",
                    "дер",
                    "с",
                    "п",
                    "пос",
                    "пгт",
                    "ст",
                    "обл",
                    "респ",
                    "авт",
                    "р-н",
                    "р-на",
                    "р-он",
                    "р-она",
                    "им",
                    "совх",
                    "арм",
                    "сел",
                    "раб",
                    "р",
                }
                and (index + 1 == len(text) or text[index + 1].isspace())
            ):
                return index
    return limit


def _equivalent(value: str) -> str:
    """Only spelling/spacing equivalence, never geographic completion."""
    value = value.casefold().replace("ё", "е")
    value = re.sub(r"\bгород\b|\bгор\.", "г.", value)
    value = re.sub(r"\bобласт[ьи]\b|\bобл\.?(?!\w)", "обл", value)
    return re.sub(r"[\s,.;]", "", value)


def _same_identity_prefix_candidate(
    text: str,
    candidates: list[DebtorBirthPlaceCandidate],
) -> DebtorBirthPlaceCandidate | None:
    """Use a complete literal place over an abbreviated repeat for one person.

    This requires the same immediately following INN and compatible inflected
    full names. It does not resolve different people or contradictory places.
    """
    identities = []
    for candidate in candidates:
        inn = re.match(
            r"\s*[,;]?\s*ИНН\s*:?\s*(\d{12})(?!\d)", text[candidate.end :], re.I
        )
        markers = list(_MARKER.finditer(candidate.quote))
        if not inn or not markers:
            return None
        names = list(_NAME.finditer(candidate.quote, 0, markers[-1].start()))
        if not names:
            return None
        # Case endings only; a shared INN is mandatory in addition to this key.
        name = " ".join(
            re.sub(r"(?:ой|ую|ая|а|у|ы|е|и)$", "", word.casefold())
            for word in names[-1].group("name").split()
        )
        identities.append((inn[1], name))
    if len(set(identities)) != 1:
        return None
    normalized = [
        re.sub(r"[\s,.;]+", " ", c.value.casefold()).strip() for c in candidates
    ]
    longest = max(range(len(candidates)), key=lambda i: len(normalized[i]))
    full = normalized[longest]
    if all(full == value or full.startswith(value + " ") for value in normalized):
        return candidates[longest]
    return None


def extract_debtor_birth_place_candidates(text: str) -> DebtorBirthPlaceResult:
    """Return proposals with exact original evidence offsets and no auto approval.

    A birthplace marker or delimited DOB-to-place block must belong to a
    nearby debtor identity. Conflicting
    literal places remain unselected so the caller can request confirmation.
    """
    candidates: list[DebtorBirthPlaceCandidate] = []
    markers = [(m, False) for m in _MARKER.finditer(text)]
    markers.extend((m, True) for m in _POSITIONAL.finditer(text))
    markers.sort(key=lambda pair: pair[0].start())
    for marker, positional in markers:
        evidence_start = _identity_start(text, marker)
        if evidence_start is None:
            continue
        if positional and not marker.group("birth_label"):
            names = list(
                _NAME.finditer(text, max(0, marker.start() - 220), marker.start())
            )
            if not names or not re.fullmatch(
                r"\s*\(\s*", text[names[-1].end("name") : marker.start()]
            ):
                continue
        start = marker.end()
        date = _DATE_PREFIX.match(text, start)
        if date:
            start = date.end()
        elif marker.groupdict().get("combined"):
            continue
        prefix = re.match(r"\s*[:–—-]?\s*(?:в\s+)?", text[start:], re.I)
        if prefix:
            start += prefix.end()
        end = _value_end(text, start)
        raw = text[start:end].rstrip(" ,;:–—-\r\n(")
        if positional:
            tail = text[end : end + 100]
            if not re.match(
                r"\s*[,;:.)]*\s*(?:ИНН|СНИЛС|адрес|место\s+(?:жительства|регистрации)|о\s+признании)",
                tail,
                re.I,
            ):
                continue
            # A street/house identifies an unlabelled postal address, not a
            # birthplace. Do not keep just its city after truncating the street.
            if re.search(
                r"\b(?:ул\.|улиц\w*|проспект|пр-кт|кв\.|квартира|дом|д\.\s*\d)|\d",
                raw,
                re.I,
            ):
                continue
        if not raw or len(raw) >= 340 or not re.search(r"[А-Яа-яЁё]", raw):
            continue
        if re.search(
            r"\b(?:рождения|ФИО|фамилия|имя|отчество)\b|\d{6,}|\b[АA]\d{2,3}\s*[-–]",
            raw,
            re.I,
        ):
            continue
        if re.match(r"(?:не\s+(?:указан|установлен)|неизвестн|отсутству)", raw, re.I):
            continue
        value = _normalize(raw)
        if not value or len(value) < 2:
            continue
        evidence_end = start + len(raw)
        candidates.append(
            DebtorBirthPlaceCandidate(
                value,
                text[evidence_start:evidence_end],
                evidence_start,
                evidence_end,
            )
        )
    values = {_equivalent(c.value) for c in candidates}
    selected = candidates[0] if candidates and len(values) == 1 else None
    if len(values) > 1:
        selected = _same_identity_prefix_candidate(text, candidates)
    problem = (
        "conflicting_debtor_birth_places"
        if len(values) > 1 and selected is None
        else None
        if candidates
        else "not_found"
    )
    return DebtorBirthPlaceResult(selected, candidates, problem)

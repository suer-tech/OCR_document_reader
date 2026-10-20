"""Source-only debtor residence proposals, always requiring confirmation.

This module deliberately does not complete addresses or correct proper names.
Offsets use Python Unicode positions in the original input, before normalization.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ocr_platform.services.court_regex_debtor_full_name import (
    ignored_historical_debtor_identity_blocks,
)


@dataclass(frozen=True)
class DebtorRegistrationAddressCandidate:
    value: str
    quote: str
    start: int
    end: int
    requires_confirmation: bool = True


@dataclass(frozen=True)
class DebtorRegistrationAddressResult:
    selected: DebtorRegistrationAddressCandidate | None
    candidates: list[DebtorRegistrationAddressCandidate]
    problem: str | None


_WORD = r"[А-ЯЁ][а-яё]+(?:[-‐‑][А-Яа-яЁё]+)?"
_NAME = re.compile(
    rf"(?<![А-Яа-яЁё]){_WORD}\s+(?:\({_WORD}(?:,\s*{_WORD})*\)\s+)?{_WORD}\s+"
    r"(?:[А-ЯЁ][а-яё]*(?:вич[ауе]?|вичем|ьич[ауе]?|вн[аыуе]|вной|ичн[аыуе])|"
    rf"{_WORD}\s+(?:[Оо]глы|[Кк]ызы))(?![А-Яа-яЁё])"
)
_PATRONYMIC = re.compile(r"(?:вич[ауе]?|вичем|ьич[ауе]?|вн[аыуе]|вной|ичн[аыуе]|оглы|кызы)$", re.I)
_ROLE = re.compile(
    r"(?:должни[кц]\w*|граждан\w*|заявлени[еяю]\s*(?:граждан\w*\s*)?|"
    r"признать\s*(?:несостоятельн\w*\s*\(банкрот\w*\)\s*)?(?:должника\s*)?|о\s+банкротстве|"
    r"(?:открыть|ввести)\s+в\s+отношении|"
    r"несостоятельности\s*\(банкротстве\))\s*[-–—:,]?\s*"
    r"(?:(?:ИП|гр\.|индивидуальн\w*\s+предпринимател\w*)\s+)?$", re.I
)
_OTHER = re.compile(
    r"\b(?:управляющ\w*|кредитор\w*|представител\w*|супруг\w*|"
    r"супруг[ауеи]|сын\w*|доч\w*|реб[её]н\w*|судь[яиюе]|СРО)\b", re.I
)
_OTHER_NAME = re.compile(
    r"(?:управляющ\w*|кредитор\w*|представител\w*|супруг\w*|судь[яиюе])"
    r"(?:\s+\w+){0,3}\s*[-–—:,]?\s*$", re.I
)
_LABEL = re.compile(
    r"\b(?:(?:адрес|место)\s+(?:места\s+)?(?:регистрации|жительства|проживания)"
    r"(?:\s*\((?:регистрации|проживания)\))?"
    r"(?:\s+(?:по\s+регистрации\s*/\s*Фактическое\s+проживание|"
    r"согласно\s+документам\s+о\s+регистрации\s+по\s+месту\s+жительства))?"
    r"(?:\s+по\s+месту\s+(?:пребывания|жительства)[^:]{0,100})?"
    r"(?:\s+по\s+адресу)?"
    r"(?:\s+должника)?\s*(?:[:–—-]\s*|(?=(?:Россия\b|Российск\w*\s+Федерац|"
    r"\d{6}\b|г\.|гор\.|город\b|[А-ЯЁ][а-яё-]+\s+(?:обл\.|область|край))))|"
    r"зарегистрирован\w*\s*(?::\s*|(?:и\s+прожива\w*\s+)?по\s+(?:месту\s+жительства\s+)?адресу\s*:?\s*|в\s+)|"
    r"прожива(?:ет|ющ\w*)\s+(?:по\s+адресу\s*:?\s*|в\s+)|"
    r"адрес\s+(?:преимущественного\s+)?места\s+пребывания\s*:\s*|"
    r"адрес\s*:\s*)", re.I
)
_IMPLICIT_LABEL = re.compile(
    r"\b(?:ИНН\s*:?\s*\d{12}|СНИЛС\s*:?\s*\d{3}[-\s]*\d{3}[-\s]*\d{3}[-\s]*\d{2})"
    r"\s*[,;]\s*|\(\s*(?=\d{6}\b)", re.I
)
_STOP = re.compile(
    r";|\b(?:ИНН|СНИЛС|ОГРН|страховой\s+номер|идентификационный\s+номер|"
    r"паспорт\w*|дата\s+рождения|"
    r"место\s+рождения|адрес\s+(?:регистрации|проживания|для)|"
    r"почтовый\s+адрес|несостоятельн\w*|банкрот\w*|о\s+(?:признании|несостоятельности)|обратил\w*|"
    r"признан\w*|ввести|введена|процедуру|далее|в\s+отсутствие|при\s+участии|"
    r"как\s+следует|с\s+уч[её]том|в\s+соответствии|в\s+срок\s+не\s+позднее)\b|"
    r"\n\s*\n|(?<=\.)\s*\n\s*\d{1,3}\.\s|:\s*\n\s*[-–]", re.I
)
_GEOGRAPHY = re.compile(
    r"\b(?:г\.|гор\.|город\b|ул\.?\b|улиц\w*|обл\.|област\w*|кра[йя]|"
    r"Респ\w*|с\.|пос\.|пгт\.?|дер\w*|станиц\w*|район\w*|р-н|х\.|"
    r"проспект\w*|пр\.|пр-кт|пер\.|переул\w*|ш\.|шоссе)", re.I
)


def _name_key(name: str) -> tuple[str, ...]:
    # Inflection changes endings; this key only detects obviously different
    # identities, and is never used to invent a name or address.
    name = re.sub(r"\([^)]*\)\s*", "", name)
    words = [word.casefold().replace("ё", "е") for word in name.split()[:3]]
    first_name = re.sub(r"[аыуеиюяй]$", "", words[1])
    return words[0][:5], first_name[:3], words[2][:5]


def _names(text: str) -> list[re.Match[str]]:
    return [m for m in _NAME.finditer(text) if _PATRONYMIC.search(m.group().split()[-1])]


def _is_debtor(text: str, name: re.Match[str]) -> bool:
    before = text[max(0, name.start() - 150):name.start()]
    if _OTHER_NAME.search(before):
        return False
    if re.search(r"(?:утвердить|назначить)\s+финансов\w*\s+управляющ\w*"
                 r"[^.;]{0,100}$", before, re.I):
        # "управляющим гражданина-должника — ФИО" names the manager.
        return False
    if _ROLE.search(before):
        # An applicant can be a creditor. A different named bankruptcy target
        # cannot lend the applicant its debtor role.
        target = re.search(r"\)\s*о\s+признании\s+(?:(?:должника|гражданина)\s+)?",
                           text[name.end():name.end() + 850], re.I)
        if target:
            offset = name.end() + target.end()
            target_name = _NAME.match(text, offset)
            if target_name and _name_key(target_name.group()) != _name_key(name.group()):
                return False
        return True
    return bool(re.match(r"\s*\([^)]{0,600}\)\s*[,–—-]?\s*"
                         r"(?:\(далее\s*[-–—]?\s*)?(?:заявитель,?\s*)?должник\b",
                         text[name.end():], re.I))


def _normalize(value: str) -> str:
    # A complete page/case header can interrupt an address. Keep the original
    # evidence span, remove only this recognised standalone service line.
    value = re.sub(r"(?:^|\n)\s*\d{1,3}\s+(?:[АA]\d{1,3}[-–—]\d+/\d{2,4}|"
                   r"\d{2,10}_\d{5,12})\s*\n", " ", value)
    value = re.sub(r"(?<=[А-Яа-яЁё])[¬\u00ad]\s*(?=[А-Яа-яЁё])", "", value)
    value = re.sub(r"(?<=[А-Яа-яЁё])-\s*\n\s*\d{1,3}\s*\n\s*(?=[А-Яа-яЁё])", "-", value)
    value = re.sub(r"(?<=[А-Яа-яЁё])-\s*\n\s*(?=[А-Яа-яЁё])", "-", value)
    value = re.sub(r"\s+", " ", value).strip(" ,;:.–—")
    value = re.sub(r"\s*,\s*", ", ", value)
    value = re.sub(r"\s*;\s*", ", ", value)
    return value


def _address_key(value: str) -> str:
    # Repeated source mentions may differ only by wrapping and punctuation.
    value = value.casefold().replace("ё", "е")
    value = re.sub(r"^((?:\d{6}[,\s]*)?)(?:россия|российская\s+федерация)\b[,\s]*", r"\1", value)
    for pattern, replacement in (
        (r"\b(?:гор\.|город\b|г\.)", "г."),
        (r"\b(?:область|области|обл\.)", "обл."),
        (r"\bс[нh][тt]\b", "снт"),
    ):
        value = re.sub(pattern, replacement, value)
    return re.sub(r"[\s,.;:]+", "", value)


def _address_end(tail: str) -> int:
    stop = _STOP.search(tail)
    end = stop.start() if stop else len(tail)
    # Narrative continuation: a later event date is not a residence component.
    date = re.search(r",?\s+\d{1,2}\s+(?:января|февраля|марта|апреля|мая|июня|июля|"
                     r"августа|сентября|октября|ноября|декабря)\s+\d{4}\s+г(?:ода|\.)?", tail[:end], re.I)
    if date:
        end = date.start()
    depth = 0
    for i, char in enumerate(tail[:end]):
        if char == "(":
            if re.match(r"\(\s*(?:ИНН|СНИЛС|дата|\d{2}\.\d{2}\.\d{4})", tail[i:], re.I):
                return i
            depth += 1
        elif char == ")":
            if not depth:
                return i
            depth -= 1
    # A metadata stop inside an opening parenthesis also excludes that bracket.
    return end if not depth else tail.rfind("(", 0, end)


def extract_debtor_registration_address_candidates(text: str) -> DebtorRegistrationAddressResult:
    """Propose residence addresses bound to the debtor's identity.

    Bare ``адрес:`` requires debtor identity metadata. An address without a
    residence label is allowed directly in the debtor's postcode parenthesis,
    or after the debtor's INN/SNILS in that same identity block. All different debtor
    addresses, including residence/registration pairs, cause abstention.
    No filesystem, network, historical fields or outside reference is consulted.
    """
    names = _names(text)
    historical_blocks = ignored_historical_debtor_identity_blocks(text)
    debtor_keys = {_name_key(n.group()) for n in names if _is_debtor(text, n)}
    explicitly_named_debtors = {
        _name_key(n.group()) for n in names if _is_debtor(text, n) and re.search(
            r"(?:признать\s*(?:(?:граждан\w*|должни[кц]\w*)\s*)?|должни[кц]\w*|"
            r"(?:открыть|ввести)\s+в\s+отношении)\s*[-–—:,]?\s*$",
            text[max(0, n.start() - 100):n.start()], re.I
        )
    }
    candidates = []
    owners = set()
    for label in sorted([*_LABEL.finditer(text), *_IMPLICIT_LABEL.finditer(text)],
                        key=lambda match: match.start()):
        # The shared identity resolver proves this is a historical other-person
        # block only from agreeing current header + current recognition. Reject
        # labels inside those exact original spans; never mask or rewrite text.
        if any(block.start <= label.start() < block.end for block in historical_blocks):
            continue
        if label.re is _IMPLICIT_LABEL and not re.match(
            r"(?:\d{6}\b|[А-ЯЁ][А-Яа-яЁё-]+,\s*(?:[А-ЯЁ][А-Яа-яЁё-]+,\s*)?"
            r"(?:ул\.|пр\.|пер\.|тер\.|ш\.|город\b|г\.)|Россия\b|Республика\b|"
            r"г\.(?!р\.)|гор\.|(?:[А-ЯЁ][а-яё-]+\s+){1,2}(?:край|область|обл\.))",
            text[label.end():], re.I
        ):
            continue
        if re.search(r"(?:почтовый|электронный|юридический)\s*$",
                     text[max(0, label.start() - 30):label.start()], re.I):
            continue
        preceding = [n for n in names if n.end() <= label.start() and label.start() - n.end() < 650]
        name = preceding[-1] if preceding else None
        direct = re.search(r"должни[кц]\w*\s*[,:(–—-]?\s*$",
                           text[max(0, label.start() - 65):label.start()], re.I)
        if name:
            between = text[name.end():label.start()]
            if label.re is _IMPLICIT_LABEL and (
                ")" in between or (label.group().lstrip().startswith("(") and between.strip())
            ):
                continue
            if (_name_key(name.group()) not in debtor_keys or _OTHER.search(between)
                    or _OTHER_NAME.search(text[max(0, name.start() - 150):name.start()])
                    or re.search(r"\)\s*\.\s|[!?]", between)):
                continue
            start = max(0, name.start() - 80)
            owner = _name_key(name.group())
        elif direct:
            between = ""
            start = max(0, label.start() - 65)
            owner = None
        else:
            continue
        if re.fullmatch(r"адрес\s*:\s*", label.group(), re.I) and not (
            direct or re.search(r"ИНН|СНИЛС|рожден|рождения", between, re.I)
            or re.search(r"\b(?:ИНН|СНИЛС)\b", text[label.end():label.end() + 250].split(")")[0], re.I)
        ):
            continue
        tail = text[label.end():label.end() + 450]
        raw = tail[:_address_end(tail)]
        raw = re.sub(r"[,\s]+о\s*$", "", raw)
        value = _normalize(raw)
        if not value or len(value) > 300 or not _GEOGRAPHY.search(value):
            continue
        # Missing source boundaries must never swallow an entire next sentence.
        if re.search(r"\b(?:суд|управляющ\w*|заявлени\w*|руб(?:ль|ля|лей)\b|составля\w*|"
                     r"обязательств\w*|имуществен\w*)\b", value, re.I):
            continue
        end = label.end() + len(raw.rstrip())
        candidates.append(DebtorRegistrationAddressCandidate(value, text[start:end], start, end))
        if owner:
            owners.add(owner)
    values = {_address_key(c.value) for c in candidates}
    problem = ("conflicting_debtors" if len(owners) > 1 or len(explicitly_named_debtors) > 1 else
               "conflicting_debtor_registration_addresses" if len(values) > 1 else
               None if candidates else "not_found")
    return DebtorRegistrationAddressResult(
        candidates[0] if candidates and problem is None else None, candidates, problem
    )

"""Source-bound requisites for RTK3; no metadata, network or party dictionaries."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime


def compact(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold()


@dataclass(frozen=True)
class Requisite:
    value: str | None
    quote: str = ""
    problem: str = ""


LEGAL_FORMS = [
    (r"\bпубличн\w*\s+акционерн\w*\s+обществ\w*", "ПАО"),
    (r"непубличн\w*\s+акционерн\w*\s+обществ\w*", "АО"),
    (r"акционерн\w*\s+обществ\w*", "АО"),
    (r"обществ\w*\s+с\s+ограниченн\w*\s+ответственност\w*", "ООО"),
    (r"профессиональн\w*\s+коллекторск\w*\s+организаци\w*", "ПКО"),
]
GENERIC = {"пао", "ао", "ооо", "ип", "пко", "банк", "кредитор", "заявитель"}


def normalize_creditor(value: str) -> str:
    """Normalize spelling of legal forms and typography, never replace a brand."""
    value = re.sub(r"\s+", " ", value).strip()
    value = re.sub(r"^(?:кредитор[а-яё]*|заявител[а-яё]*|взыскател[а-яё]*)\s*[:—–-]?\s*", "", value, flags=re.I)
    for pattern, replacement in LEGAL_FORMS:
        value = re.sub(pattern, replacement, value, flags=re.I)
    for form in ["ООО", "ПАО", "АО", "ПКО", "ИП"]:
        value = re.sub(r"\b" + form + r"\b", form, value, flags=re.I)
    if re.search(r"налогов|\bФНС\b|\bИФНС\b", value, re.I):
        value = re.sub(r"межрайонн[а-яё]+\s+инспекци[а-яё]+", "Межрайонная инспекция", value, flags=re.I)
    value = re.sub(r"№\s*([0-9])", r"№\1", value)
    value = re.sub(r"\s*\(?\s*(?:ИНН|ОГРН|КПП)\s*[:№]?\s*[0-9].*$", "", value, flags=re.I).rstrip(" ,;(")
    value = re.sub(r",\s*(?:г\.?\s+|город\s+).*$", "", value, flags=re.I)
    # Straight/curly quotation pairs only; keep internal capitalization and ё.
    value = re.sub(r'["“„]([^"“”„]+)["”]', r"«\1»", value)
    value = re.sub(r"«\s*", "«", value)
    value = re.sub(r"\s*»", "»", value)
    value = re.sub(r"\s+([,;])", r"\1", value).strip(" ,;")
    if re.fullmatch(r"(?:ПАО\s+)?Банк\s+ВТБ(?:\s*\(ПАО\))?", value, re.I):
        return "Банк ВТБ"
    if "«" not in value and (organization := re.fullmatch(r"(ПАО|АО|ООО)(\s+ПКО)?\s+([^()]+)", value)):
        value = organization[1] + (organization[2] or "") + " «" + organization[3].strip() + "»"
    return value


def identity(value: str) -> tuple[str | None, frozenset[str]]:
    value = normalize_creditor(value)
    form = re.search(r"\b(?:ООО|ПАО|АО|ИП)\b", value)
    tokens = frozenset(t for t in re.findall(r"[а-яёa-z0-9]+", value.casefold()) if t not in GENERIC)
    return form.group(0) if form else None, tokens


def creditor_value(value: str | None, quote: str, text: str) -> Requisite:
    if not value or not quote.strip() or compact(quote) not in compact(text):
        return Requisite(None, problem="Наименование кредитора не подтверждено буквальной цитатой.")
    normalized = normalize_creditor(value)
    form, tokens = identity(normalized)
    source = normalize_creditor(quote)
    source_tokens = set(re.findall(r"[а-яёa-z0-9]+", source.casefold()))
    # A shared generic word 'банк' cannot prove an invented organization name.
    person_names = list(persons(normalized))
    same_person = bool(form in {None, "ИП"} and len(person_names) == 1
        and compact(re.sub(r"^ИП\s+", "", normalized)) == compact(person_names[0].group(0)) and any(
        person_key(person_names[0]) == person_key(candidate) for candidate in persons(quote)))
    source_forms = set(re.findall(r"\b(?:ООО|ПАО|АО|ИП)\b", source))
    if not tokens or (not tokens <= source_tokens and not same_person) or (form and form not in source_forms):
        return Requisite(None, quote, "Наименование кредитора не соответствует организации в цитате.")
    if not form and len(source_forms) == 1 and normalized != "Банк ВТБ":
        normalized = normalize_creditor(next(iter(source_forms)) + " " + normalized)
    return Requisite(normalized, quote)


INN = re.compile(r"\bИНН\s*[:№]?\s*([0-9]{12}|[0-9]{10})(?![0-9])", re.I)
WORD = r"[А-ЯЁ][А-ЯЁа-яё]+(?:-[А-ЯЁа-яё]+)*"
PERSON = re.compile(rf"\b(?!(?:ИП|ПАО|АО|ООО|ПКО)\b)({WORD})(?:\s+\({WORD}\))?\s+({WORD})\s+({WORD})")


def person_key(match: re.Match) -> tuple[str, str, str]:
    def stem(word):
        return re.sub(r"(?:ой|ей|ом|ем|ую|а|я|ы|и|у|ю|е)$", "", word.casefold())
    return tuple(stem(word) for word in match.groups())


def persons(text: str):
    for match in PERSON.finditer(text):
        if re.search(r"(?:вич[а-яё]*|вн[а-яё]+)$", match[3], re.I):
            yield match


def debtor_anchors(text: str) -> set[tuple[str, str, str]]:
    """Use current-act introduction, before historical reasons/settlement parties."""
    text = re.sub(r"\s+", " ", text)
    stop = re.search(r"\b(?:у\s*с\s*т\s*а\s*н\s*о\s*в\s*и\s*л|о\s*п\s*р\s*е\s*д\s*е\s*л\s*и\s*л)\s*:", text, re.I)
    intro = text[:stop.start() if stop else min(len(text), 6000)]
    anchors = set()
    for match in persons(intro):
        prefix = intro[max(0, match.start()-140):match.start()]
        if re.search(r"(?:супруг[а-яё]*|поручител[а-яё]*|гарант|управляющ[а-яё]*)[^.;]{0,60}$", prefix, re.I):
            continue
        if re.search(r"(?:реестр[а-яё]*\s+требований\s+кредиторов|о\s+банкротстве|"
                     r"в\s+рамках\s+дел[а-яё]*\s+по\s+заявлению|"
                     r"должник[а-яё]*|должниц[а-яё]*|гражданин[а-яё]*|гражданк[а-яё]*)"
                     r"\s*(?:(?:должник|гражданк|гражданин)[а-яё]*\s*)?[—–,:-]?\s*$", prefix, re.I):
            anchors.add(person_key(match))
    return anchors


def source_inn(text: str, role: str, creditor: str | None = None) -> Requisite:
    """Resolve unique labeled INN from the identified subject's own record."""
    source = re.sub(r"\s+", " ", text)
    anchors = debtor_anchors(source) if role == "debtor_inn" else set()
    wanted_form, wanted_tokens = identity(creditor or "")
    candidates = {}
    for match in INN.finditer(source):
        start = max(0, match.start()-650)
        prefix = source[start:match.start()]
        if role == "debtor_inn":
            names = list(persons(prefix))
            if len(match[1]) != 12 or len(anchors) != 1 or not names:
                continue
            name = names[-1]
            if person_key(name) not in anchors:
                continue
            record = prefix[name.start():] + match.group(0)
            # Do not cross from a person's record into a subsequent organization.
            if re.search(r"\b(?:ООО|ПАО|АО|поручител|управляющ)\b", record, re.I):
                continue
        else:
            if not wanted_tokens:
                continue
            starts = list(re.finditer(r"\b(?:ООО|ПАО|АО|ИП)\b|" + "|".join(
                pattern for pattern, _ in LEGAL_FORMS[:4]), prefix, re.I))
            if not starts:
                continue
            record_prefix = prefix[starts[-1].start():]
            # Never carry an organization's name across the closing of its
            # requisites into the next debtor/manager's tax ID.
            if ")" in record_prefix or re.search(
                    r"должник|должниц|граждан|поручител|управляющ|требований\s+кредиторов", record_prefix, re.I):
                continue
            if not re.match(r"ИП\b", record_prefix, re.I) and list(persons(record_prefix)):
                continue
            name = normalize_creditor(record_prefix).split("(")[0].strip(" ,;")
            form, tokens = identity(name)
            if wanted_form and form != wanted_form:
                continue
            if tokens != wanted_tokens:
                continue
            # Keep an actual source excerpt, not the normalized organization text.
            record = record_prefix + match.group(0)
        # A record containing two tax IDs is not a single-subject proof.
        if len(INN.findall(record)) != 1:
            continue
        candidates[match[1]] = record
    if len(candidates) == 1:
        value, quote = next(iter(candidates.items()))
        return Requisite(value, quote)
    return Requisite(None, problem="ИНН не связан однозначно с участником текущего дела.")


MONTHS = {"января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
          "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12}
DATE = re.compile(r"(?<![0-9])([0-9]{1,2})\s*\.\s*([0-9]{1,2})\s*\.\s*([0-9]{4})(?![0-9])|"
                  r"(?<![0-9])(?:[«\"]\s*)?([0-9]{1,2})(?:\s*[»\"])?\s+(" + "|".join(MONTHS) + r")\s+([0-9]{4})(?![0-9])", re.I)


def dates(text: str) -> list[tuple[str, str]]:
    found = []
    for match in DATE.finditer(text):
        day, month, year = (int(match[1]), int(match[2]), int(match[3])) if match[1] else (
            int(match[4]), MONTHS[match[5].casefold()], int(match[6]))
        try:
            value = datetime(year, month, day).strftime("%d.%m.%Y")
        except ValueError:
            continue
        found.append((value, match.group(0)))
    return found


def date_value(value: str | None, quote: str) -> str | None:
    if not value or not re.fullmatch(r"[0-9]{2}\.[0-9]{2}\.[0-9]{4}", value):
        return None
    return value if any(candidate == value for candidate, _ in dates(quote)) else None


def ruling_date(text: str) -> Requisite:
    # Limit to the heading, before the judge starts describing the application.
    stop = re.search(r"\b(?:в\s+составе|рассмотрев|рассмотрел|ознакомившись|руководствуясь|"
                     r"у\s*с\s*т\s*а\s*н\s*о\s*в\s*и\s*л\s*:|"
                     r"о\s*п\s*р\s*е\s*д\s*е\s*л\s*и\s*л\s*:)", text, re.I)
    heading = text[:min(stop.start() if stop else len(text), 4000)]
    # An explicitly labelled publication/signature is never the act's heading date.
    heading = re.split(r"Дата\s+(?:публикации|размещения)|Электронная\s+подпись", heading, flags=re.I)[0]
    heading = re.sub(r"[0-9]{1,2}\.[0-9]{1,2}\.[0-9]{4}\s*(?:г\.?\s*)?№\s*[0-9]+[-–]?[А-Яа-я]*", "", heading)
    full = re.search(r"(?:изготовлен[оа]?|составлен[оа]?)\s+в\s+полном\s+объ[её]ме\s*[:—–-]?\s*([^\n]{0,80})", heading, re.I)
    if full and len(explicit := dict(dates(full[1]))) == 1:
        value = next(iter(explicit))
        return Requisite(value, full.group(0))
    candidates = dict(dates(heading))
    if len(candidates) == 1:
        value, quote = next(iter(candidates.items()))
        return Requisite(value, quote)
    return Requisite(None, heading, "Дата текущего определения не установлена однозначно в шапке.")


def publication_date(text: str) -> Requisite:
    candidates = {}
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if not re.search(r"\bКАД\b|kad\.arbitr\.ru", line, re.I):
            continue
        label = re.search(r"дата\s+(?:публикации|размещения)|(?:опубликован|размещ[её]н)[аоы]?", line, re.I)
        if label:
            if not dates(line[label.start():]) and index + 1 < len(lines):
                following = lines[index+1].strip()
                if re.fullmatch(r"[0-9. /:]+", following):
                    line += "\n" + following
            for value, _ in dates(line[label.start():]):
                candidates[value] = line
    if len(candidates) == 1:
        value, line = next(iter(candidates.items()))
        return Requisite(value, line)
    return Requisite(None, problem="Дата публикации КАД явно не указана или неоднозначна.")

"""Debtor identity from the application header, without remote model calls."""
from __future__ import annotations

import re
from typing import Any

from ocr_platform.api.schemas import ValidationIssue
from ocr_platform.services.passport_vision_service import _normalize_passport_value
from ocr_platform.services.fio_gender import infer_gender_from_fio as _gender

PROFILE_ID = "bankruptcy_application"
REVIEW_CODE = "bankruptcy_application_requires_review"
FLAGS = re.IGNORECASE | re.DOTALL
DATE = r"[0-9]{1,2}\s*\.\s*[0-9]{1,2}\s*\.\s*[0-9]{4}"
STOP = (
    r"адрес\s+для\s+(?:почтовой\s+)?корреспонденции|"
    r"фактическ\w*\s+(?:адрес|проживание)|в\s+лице\s+представителя|представитель|"
    r"кредитор\w*\s*(?:[0-9]+)?\s*:|уполномоченн\w*\s+орган\w*\s*:|"
    r"госпошлина|заявление(?:\s+должника)?\s+о\s+признании|"
    r"место\s+работы|семейн\w*\s+положение"
)
ADDRESS_LABEL = (
    r"(?:адрес\s+регистрации|зарегистрирован\w*(?:\s+по\s+адресу)?|"
    r"место\s+жительств\w*\s+по\s+регистрации)\s*:?\s*"
)


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip(" ,;:\n\t")


def _header(text: str) -> str:
    text = text.replace("\u00ad", "")
    text = re.sub(r"(?<=\w)-[ \t]*\r?\n\s*(?=\w)", "-", text)
    # Do not search the narrative or attachments for a missing header.
    title = re.search(r"\bзаявление\b", text, re.IGNORECASE)
    prefix = text[:title.start()] if title else text
    anchors = list(re.finditer(r"\b(?:должник|заявитель(?:\s*\(должник\))?)\s*:", prefix, FLAGS))
    if len(anchors) != 1:
        return ""
    header = prefix[anchors[0].end():]
    stop = re.search(STOP, header, FLAGS)
    return _clean(header[:stop.start()] if stop else header)


def extract_fields(text: str, profile: dict[str, Any]) -> dict:
    header = _header(text)
    results = {
        name: {"value": None, "confidence": None, "source": "bankruptcy_header",
               "extraction_method": "local_header",
               "reasoning": "Поле не найдено однозначно в шапке должника."}
        for name in profile["fields"]
    }

    def put(name, value, quote, *, problem=None):
        value = _normalize_passport_value(name, value) if value else None
        results[name].update(value=value, reasoning=(
            f'Из шапки должника: «{quote}».' if value else
            "Поле отсутствует или не прошло проверку; требуется проверка исходного текста."
        ))
        if problem:
            results[name]["validation_issue"] = problem

    def capture(name, pattern, *, group=1):
        matches = list(re.finditer(pattern, header, FLAGS))
        values = {_clean(m.group(group)) for m in matches}
        if len(values) == 1:
            value = values.pop()
            put(name, value, matches[0].group())
            return value
        if len(values) > 1:
            results[name]["validation_issue"] = "В шапке найдены разные значения поля."
        return None

    if not header:
        results["last_name"]["validation_issue"] = "Не найден единственный блок должника в шапке."
        return results

    # The primary surname precedes an optional parenthesized previous surname.
    fio = re.match(
        r"(?P<last>[А-ЯЁа-яё]+(?:[-’'][А-ЯЁа-яё]+)*)"
        r"(?:\s*\([А-ЯЁа-яё -]+\))?\s+"
        r"(?P<first>[А-ЯЁа-яё]+(?:[-’'][А-ЯЁа-яё]+)*)"
        r"(?:\s+(?:[—–]\s*)?(?P<patr>[А-ЯЁа-яё]+(?:[-’'][А-ЯЁа-яё]+)*(?:\s+(?:оглы|кызы|уулу))?))?"
        r"\s*[,;]?\s*(?=[0-9]|дата\s+рождения|паспорт|$)", header, FLAGS,
    )
    if fio:
        for name, group in [("last_name", "last"), ("first_name", "first"), ("patronymic", "patr")]:
            put(name, fio.group(group), fio.group())
        date_area = header[fio.end():]
        dob = re.match(r"(?:дата\s+рождения\s*:?\s*)?(" + DATE + r")", date_area, FLAGS)
        if dob:
            parts = re.sub(r"\s", "", dob[1]).split(".")
            put("birth_date", f"{int(parts[0]):02}.{int(parts[1]):02}.{parts[2]}", dob.group())
    first = results["first_name"]["value"]
    patr = results["patronymic"]["value"]
    gender = _gender(first, patr)
    put("gender", gender, fio.group() if fio else "")
    if gender:
        results["gender"].update(
            source="fio_inference",
            reasoning=f'Пол определён по ФИО «{first or ""} {patr or ""}», явно в документе не указан.',
        )

    capture("birth_place", r"место\s+рождения\s*:?\s*(.+?)(?=,?\s*(?:иностранн\w*\s+паспорт|паспорт|" + ADDRESS_LABEL + r")|$)")
    foreign = bool(re.search(r"иностранн\w*\s+паспорт|паспорт\s+иностранного\s+гражданина", header, FLAGS))
    results["passport_number"]["passport_kind"] = "foreign" if foreign else "russian"
    if len(re.findall(r"\bпаспорт\b", header, FLAGS)) > 1:
        results["passport_number"]["validation_issue"] = "В шапке несколько упоминаний паспорта; требуется сверка."
    passport = re.search(r"\bпаспорт(?:\s+иностранного\s+гражданина)?\s*:?\s*(.+?)(?=\s*[,;]?\s*выдан\w*\b|$)", header, FLAGS)
    if passport:
        number_text = _clean(passport[1])
        if foreign:
            if re.fullmatch(r"[A-ZА-ЯЁ0-9][A-ZА-ЯЁ0-9 /№-]{2,29}", number_text, re.IGNORECASE):
                put("passport_number", number_text, passport.group())
            results["passport_series"]["reasoning"] = "Иностранный паспорт: номер сохранён целиком, серия null."
        else:
            number = re.fullmatch(r"(?:серия\s*)?([0-9]{2})\s*([0-9]{2})\s*(?:№|номер|N)?\s*([0-9]{6})", number_text.rstrip(" ,;—–"), FLAGS)
            if number:
                put("passport_series", number[1] + number[2], passport.group())
                put("passport_number", number[3], passport.group())

    capture("issued_by", r"\bвыдан\w*\s*:?\s*(.+?)(?=\s*[,;]?\s*(?:дата\s+выдачи|" + DATE + r"|код\s+подразделения|" + ADDRESS_LABEL + r")|$)")
    issuance = re.search(r"\bвыдан\w*\s*:?\s*(.*?)(?=код\s+подразделения|место\s+рождения|" + ADDRESS_LABEL + r"|ИНН|СНИЛС|$)", header, FLAGS)
    explicit_dates = re.findall(r"дата\s+выдачи\s*:?\s*(" + DATE + r")", header, FLAGS)
    issue_dates = explicit_dates or (re.findall(DATE, issuance[1]) if issuance else [])
    issue_dates = {re.sub(r"\s", "", x) for x in issue_dates}
    if len(issue_dates) == 1:
        issue = issue_dates.pop()
        parts = issue.split(".")
        put("issue_date", f"{int(parts[0]):02}.{int(parts[1]):02}.{parts[2]}", issue)
    elif len(issue_dates) > 1:
        results["issue_date"]["validation_issue"] = "В шапке разные даты выдачи паспорта."
    code = capture("department_code", r"код\s+подразделения\s*:?\s*([0-9]{3}\s*-?\s*[0-9]{3})(?![0-9])")
    if code:
        digits = re.sub(r"\D", "", code)
        put("department_code", digits[:3] + "-" + digits[3:], code)
    address = capture("registration_address", ADDRESS_LABEL + r"(.+?)(?=\s*(?:ИНН|СНИЛС|(?:контактный\s+)?тел(?:ефон)?\s*:|место\s+рождения\s*:|дата\s+(?:рождения|выдачи)\s*:|(?:иностранн\w*\s+)?паспорт\s*:)|$)")
    if address:
        address = address.rstrip(" .;")
        put("registration_address", address, address)
        index = re.search(r"(?<![0-9])([0-9]{6})(?![0-9])", address)
        if index:
            put("post_index", index[1], address)
        components = [_clean(x) for x in address.split(",")]
        patterns = {
            "region": r"(?:обл\.?|область|край|республика|респ\.?|АО|А\.\s*ОКР\.?|ХМАО(?:-Югра)?|ЯНАО|автономн\w*\s+округ)",
            "city": r"^(?:г\.|гор\.|город\b|с\.(?!п\.)|село\b|д\.|деревня\b|п\.|пос\.|поселок\b|посёлок\b|пгт\.?)\s*[А-ЯЁа-яё]",
            "street": r"^(?:ул\.|улица\b|проспект\b|пр-т\b|пр-кт\b|пер\.|переулок\b|шоссе\b|ш\.|наб\.|набережная\b|бульвар\b|б-р\b|проезд\b|площадь\b|пл\.|туп\.|тупик\b)",
        }
        for name, pattern in patterns.items():
            found = [x for x in components if re.search(pattern, x, re.IGNORECASE)]
            if len(found) == 1:
                value = found[0]
                if name in {"city", "street"}:
                    value = re.split(r"\s+(?:д\.|дом|корп\.|кв\.|квартира|стр\.)\s*№?\s*[0-9]", value, flags=re.IGNORECASE)[0]
                put(name, value, address)
    return results


def field_issues(fields: dict, profile: dict) -> list[ValidationIssue]:
    def value(name):
        info = fields.get(name)
        return info.get("value") if isinstance(info, dict) else info

    number_info = fields.get("passport_number")
    foreign = isinstance(number_info, dict) and number_info.get("passport_kind") == "foreign"
    issues = []
    for name, config in profile["fields"].items():
        info = fields.get(name)
        problem = info.get("validation_issue") if isinstance(info, dict) else None
        required = config.get("required", False) and not (foreign and name in {"passport_series", "department_code"})
        if required and not value(name):
            problem = problem or "Отсутствует обязательное поле."
        if problem:
            issues.append(ValidationIssue(code=REVIEW_CODE, field_name=name, message=problem, severity="error"))
    return issues

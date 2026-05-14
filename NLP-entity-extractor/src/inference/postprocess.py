from __future__ import annotations

import re
from functools import lru_cache

from pymorphy3 import MorphAnalyzer

from .contracts import FioComponents

_SPACE_RE = re.compile(r"\s+")


@lru_cache(maxsize=1)
def get_morph() -> MorphAnalyzer:
    return MorphAnalyzer()


def normalize_whitespace(value: str) -> str:
    return _SPACE_RE.sub(" ", value).strip()


def titlecase_russian_name(value: str | None) -> str | None:
    if not value:
        return None
    value = normalize_whitespace(value)
    return "-".join(part[:1].upper() + part[1:].lower() for part in value.split("-"))


def infer_gender(first_name: str | None, patronymic: str | None) -> str | None:
    if patronymic:
        lowered = patronymic.lower()
        if lowered.endswith("вна") or lowered.endswith("ична"):
            return "femn"
        if lowered.endswith("вич") or lowered.endswith("ич"):
            return "masc"
    if first_name:
        lowered = first_name.lower()
        if lowered.endswith(("а", "я")) and lowered not in {"илья", "никита"}:
            return "femn"
    return None


def inflect_to_nominative(value: str | None) -> str | None:
    if not value:
        return None
    morph = get_morph()
    parts: list[str] = []
    for chunk in normalize_whitespace(value).split("-"):
        parsed = morph.parse(chunk)
        inflected = None
        for item in parsed:
            form = item.inflect({"nomn"})
            if form is not None:
                inflected = form.word
                break
        parts.append(inflected or chunk)
    return "-".join(parts)


def adjust_surname_for_gender(last_name: str | None, gender: str | None) -> str | None:
    if not last_name or gender != "femn":
        return last_name
    lowered = last_name.lower()
    if lowered.endswith(("ов", "ев", "ин", "ын")):
        return last_name + "а"
    return last_name


def normalize_fio_components(fio: FioComponents) -> FioComponents:
    first_name = inflect_to_nominative(fio.first_name)
    patronymic = inflect_to_nominative(fio.patronymic)
    gender = infer_gender(first_name, patronymic)
    last_name = adjust_surname_for_gender(inflect_to_nominative(fio.last_name), gender)
    return FioComponents(
        last_name=titlecase_russian_name(last_name),
        first_name=titlecase_russian_name(first_name),
        patronymic=titlecase_russian_name(patronymic),
    )
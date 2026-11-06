"""Reusable local gender inference from parsed FIO or literal patronymic evidence.

The two entry points intentionally preserve different evidence policies: name
inference can consult morphology, while source evidence only matches the written
patronymic ending. Neither function selects a person from a document or overrides
an explicitly stated sex; those decisions belong to the document handler.
"""
from __future__ import annotations

from functools import lru_cache
import re
from typing import Literal

Gender = Literal["Мужской", "Женский"]

_NOMINATIVE_MALE = re.compile(r"(?:ович|евич|ич|оглы|уулу)$", re.IGNORECASE)
_NOMINATIVE_FEMALE = re.compile(r"(?:овна|евна|ична|инична|кызы)$", re.IGNORECASE)

# Keep the established literal-evidence vocabulary, including case endings.
# Do not silently expand it using the dictionary or the nominative rules above.
_SOURCE_MALE = re.compile(r"(?:вич(?:а|у|ем|е)?|ьич(?:а|у|ем|е)?|оглы)$", re.IGNORECASE)
_SOURCE_FEMALE = re.compile(r"(?:вн(?:а|ы|е|у|ой)|ичн(?:а|ы|е|у|ой)|кызы)$", re.IGNORECASE)


def _patronymic_gender(value: str | None, male: re.Pattern, female: re.Pattern) -> Gender | None:
    if value:
        if male.search(value):
            return "Мужской"
        if female.search(value):
            return "Женский"
    return None


@lru_cache(maxsize=1)
def _morph():
    # Lazy and cached: source-only handlers do not load a morphology dictionary.
    import pymorphy3

    return pymorphy3.MorphAnalyzer()


def infer_gender_from_fio(first_name: str | None, patronymic: str | None) -> Gender | None:
    """Infer from nominative name components; ambiguous/conflicting clues -> None.

    Input is already separated into name and patronymic. Surnames are not used.
    Keep the existing Name-tag and score>=0.1 filter, including its treatment of
    uncommon names. This is an inference, not a transcription of an explicit field.
    """
    votes = set()
    patronymic_vote = _patronymic_gender(patronymic, _NOMINATIVE_MALE, _NOMINATIVE_FEMALE)
    if patronymic_vote:
        votes.add(patronymic_vote)
    if first_name:
        candidates = [p for p in _morph().parse(first_name) if "Name" in p.tag and p.score >= 0.1]
        genders = {p.tag.gender for p in candidates}
        if genders == {"masc"}:
            votes.add("Мужской")
        elif genders == {"femn"}:
            votes.add("Женский")
    return next(iter(votes)) if len(votes) == 1 else None


def infer_gender_from_source_patronymic(source: str | None) -> Gender | None:
    """Read a literal ending, including declined forms, without guessing by name.

    A caller may pass its source FIO ending in the patronymic, but must separately
    establish whose FIO it is and retain its original quote/offsets for review.
    """
    return _patronymic_gender(source, _SOURCE_MALE, _SOURCE_FEMALE)

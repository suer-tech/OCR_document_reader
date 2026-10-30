"""Dictionary-only reversal of an explicitly oblique surname; no spelling repair."""
from functools import lru_cache

from pymorphy3 import MorphAnalyzer
from pymorphy3.units import DictionaryAnalyzer


@lru_cache(maxsize=1)
def _dictionary() -> MorphAnalyzer:
    # No suffix/prefix prediction, fuzzy е/ё substitution, or name guessing.
    return MorphAnalyzer(units=[DictionaryAnalyzer()], char_substitutes={})


@lru_cache(maxsize=4096)
def known_surname_nominative(word: str, *, male: bool) -> str | None:
    """Require one known surname paradigm and an oblique gender-matched form.

    The caller must independently establish oblique case from the full name.
    A written nominative alternative prevents this reverse transformation.
    """
    gender = "masc" if male else "femn"
    parses = [p for p in _dictionary().parse(word)
              if p.is_known and "Surn" in p.tag and p.tag.gender == gender]
    if not parses or any(p.tag.case == "nomn" for p in parses):
        return None
    choices = set()
    for parse in parses:
        if parse.tag.case not in {"gent", "accs", "datv", "ablt", "loct"}:
            return None
        nominal = parse.inflect({"nomn"})
        if nominal is None:
            return None
        choices.add(nominal.word)
    if len(choices) != 1:
        return None
    result = choices.pop()
    # Known paradigms can restore a stem, but cannot insert a different е/ё.
    if word.count("ё") != result.count("ё") or word.count("Ё") != result.count("Ё"):
        return None
    return result.capitalize()

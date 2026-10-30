"""Literal court identifier checks: no added, deleted or corrected digits."""
import re
from dataclasses import dataclass

_NUMBERS = re.compile(r"(?<![0-9_])[0-9]+(?:[\s_‐‑‒–—−-]+[0-9]+)*(?![0-9_])")
_INN_LABEL = re.compile(r"\b(?:И\s*Н\s*Н|идентификационный\s+номер\s+налогоплательщика)\b", re.IGNORECASE)
_PAGE_HEADER = re.compile(
    r"^[ \t]*(?P<page>[0-9]{1,3})[ \t]+(?P<code>[0-9]{5,}_[0-9]{5,})[ \t]*\r?\n",
    re.MULTILINE,
)


@dataclass(frozen=True)
class IdentifierSourceIssue:
    message: str
    reject_value: bool = False


def _number_runs(text: str) -> list[tuple[int, int, str]]:
    runs = []
    for match in _NUMBERS.finditer(text):
        raw = match[0]
        # An adjacent numbered operative clause is not the twelfth SNILS digit.
        if re.match(r"\.\s+[А-ЯЁ]", text[match.end():]):
            clause = re.search(r"\s+[0-9]{1,2}$", raw)
            if clause and len(re.sub(r"[^0-9]", "", raw[:clause.start()])) == 11:
                raw = raw[:clause.start()]
        runs.append((match.start(), match.start() + len(raw), re.sub(r"[^0-9]", "", raw)))
    return runs


def identifier_source_issue(name: str, value: str, text: str) -> IdentifierSourceIssue | None:
    """Keep unsupported readings visible for review; reject proven truncations."""
    digits = re.sub(r"[^0-9]", "", value)
    runs = _number_runs(text)
    if any(number == digits for _, _, number in runs):
        return None
    if name == "debtor_snils":
        headers = list(_PAGE_HEADER.finditer(text))
        repeated = {h["code"] for h in headers
                    if len({m["page"] for m in headers if m["code"] == h["code"]}) >= 2}
        if repeated:
            # A page counter and repeated document stamp may interrupt a SNILS
            # across pages. Ignore only verified repeated headers in this view;
            # the original text and identifier digits remain unchanged.
            without_headers = _PAGE_HEADER.sub(lambda m: "" if m["code"] in repeated else m[0], text)
            if any(number == digits for _, _, number in _number_runs(without_headers)):
                return None
    title = "ИНН" if name == "debtor_inn" else "СНИЛС"
    if name == "debtor_inn":
        for label in _INN_LABEL.finditer(text):
            after = text[label.end():]
            prefix = re.match(r"\s*\)?\s*[:№–—-]?\s*", after)
            start = label.end() + prefix.end()
            for left, _, number in runs:
                if left == start and len(number) > len(digits) and number.startswith(digits):
                    return IdentifierSourceIssue(
                        f"ИНН {digits} обрезан относительно полного номера {number} в акте; "
                        "удалять цифры нельзя, требуется ручная проверка", True,
                    )
    return IdentifierSourceIssue(
        f"Полный {title} {value} не подтверждён непрерывной последовательностью цифр "
        "в тексте акта; значение сохранено для сверки с изображением, требуется ручная проверка",
    )

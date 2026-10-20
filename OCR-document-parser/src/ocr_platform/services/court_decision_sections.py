"""Existing court section boundaries shared by both extraction paths."""
import re


def extract_motivating_part(text: str) -> str | None:
    heading = re.search(r"У\s*С\s*Т\s*А\s*Н\s*О\s*В\s*И\s*Л", text, re.I)
    if heading is None:
        return None
    operative = re.search(r"Р\s*Е\s*Ш\s*И\s*Л", text[heading.end():], re.I)
    if operative is None:
        return None
    value = text[heading.end():heading.end() + operative.start()]
    return re.sub(r"^[\s:,.\-–—()]+", "", value).strip() or None


def extract_resolutive_part(text: str) -> str | None:
    heading = re.search(r"Р\s*Е\s*Ш\s*И\s*Л", text, re.I)
    if heading is None:
        return None
    signature = re.search(r"электронная\s+подпись\s+действительна", text[heading.end():], re.I)
    end = heading.end() + signature.start() if signature else len(text)
    return re.sub(r"^[\s:,.\-–—()]+", "", text[heading.end():end]).strip() or None

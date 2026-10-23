"""Existing court section boundaries shared by both extraction paths."""
import re

MOTIVATING_HEADING = re.compile(
    r"У\s*С\s*Т\s*А\s*Н\s*О\s*В\s*И\s*Л(?:[\s:,]*следующее\s*[.:])?", re.I,
)

_ALTERNATIVE_MOTIVATION = re.compile(
    r"^[ \t]*(?:(?:При\s+рассмотрении\s+(?:материалов\s+дела|заявления),?\s+)?"
    r"(?:арбитражным\s+)?судом\s+установлены\s+следующие\s+обстоятельства|"
    r"Изучив\s+материалы\s+дела,?\s+(?:исследовав\s+представленные\s+документы,?\s+)?"
    r"(?:арбитражный\s+)?суд\s+приходит\s+к\s+следующ(?:ему(?:\s+выводу)?|им\s+выводам))"
    r"\s*[.:]", re.I | re.M,
)
_NARRATIVE_MOTIVATION = re.compile(
    r"^[ \t]*(?:На\s+рассмотрение\s+(?:арбитражного\s+суда|суда)\b"
    r"[^.!?;:]{0,200}\b(?:поступило|передано)\s+заявление\b|"
    r"Должник\s+обратил(?:ся|ась)\b[^.!?;:]{0,200}\bс\s+заявлением\b)", re.I | re.M,
)
_OPERATIVE_LINE = re.compile(
    r"^[ \t]*(?:р[ \t]*е[ \t]*ш[ \t]*и[ \t]*л|"
    r"о[ \t]*п[ \t]*р[ \t]*е[ \t]*д[ \t]*е[ \t]*л[ \t]*и[ \t]*л)"
    r"[ \t]*(?::|$)", re.I | re.M,
)
RESOLUTIVE_ONLY_TITLE = re.compile(
    r"^[^\S\r\n]*[*_(]*р[^\S\r\n]*е[^\S\r\n]*з[^\S\r\n]*о[^\S\r\n]*л[^\S\r\n]*"
    r"ю[^\S\r\n]*т[^\S\r\n]*и[^\S\r\n]*в[^\S\r\n]*н[^\S\r\n]*а[^\S\r\n]*я\s+"
    r"ч[^\S\r\n]*а[^\S\r\n]*с[^\S\r\n]*т[^\S\r\n]*ь"
    r"(?:\s+[*_]*(?:решения|определения)[*_]*)?[*_)]*[^\S\r\n]*\r?$", re.I | re.M,
)


def alternative_motivation_span(text: str) -> tuple[int, int] | None:
    """Narrow source-observed prose opening, never an inferred whole preamble."""
    if RESOLUTIVE_ONLY_TITLE.search(text[:4000]):
        return None
    operative = list(_OPERATIVE_LINE.finditer(text))
    if len(operative) != 1:
        return None
    end = operative[0].start()
    openings = list(_ALTERNATIVE_MOTIVATION.finditer(text, 0, end))
    if len(openings) != 1:
        return None
    start = openings[0].start()
    # These full acts introduce facts before their legal-reasoning transition.
    # Retain the earlier, literal narrative opening rather than dropping facts.
    narrative = list(_NARRATIVE_MOTIVATION.finditer(text, 0, start))
    if len(narrative) > 1:
        return None
    if narrative:
        start = narrative[0].start()
    # A later prose transition inside a section cannot replace its earlier
    # standard opening, including when the selective LLM supplies boundaries.
    if MOTIVATING_HEADING.search(text[:start]):
        return None
    # An opening quoted within another sentence cannot establish this section.
    if re.search(r"(?:цитата|из\s+(?:решения|определения)(?:\s+\w+){0,4}|[«“])\s*:?\s*$",
                 text[max(0, start-100):start], re.I):
        return None
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end-1].isspace():
        end -= 1
    return (start, end) if end > openings[0].end() else None


def extract_motivating_part(text: str) -> str | None:
    heading = MOTIVATING_HEADING.search(text)
    span = alternative_motivation_span(text)
    # A later narrative «суд установил, что» must not shorten a section whose
    # earlier, explicit prose opening already establishes complete boundaries.
    if span and (heading is None or span[0] < heading.start()):
        return text[span[0]:span[1]]
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

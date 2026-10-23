"""Unconfirmed regex candidates for the court's advance report obligation.

This module does not calculate report dates or infer a confidence score. Every
candidate needs the planned external classifier confirmation before acceptance.
"""
from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class EarlyReportCandidate:
    value: bool | None
    reason: str
    evidence: str | None
    start: int | None
    end: int | None
    requires_confirmation: bool = True


_HEADING = re.compile(
    r"(?im)(?:^|\n)\s*(?:р\s*е\s*ш\s*и\s*л|о\s*п\s*р\s*е\s*д\s*е\s*л\s*и\s*л)\s*:?[ \t]*(?:\n|$)|"
    r"\b(?:решил|определил)\s*:", re.I,
)
_REPORT = re.compile(r"\bотч[её]т\w*", re.I)
_ACTOR = re.compile(r"(?:финансов\w*|арбитражн\w*|конкурсн\w*)\s+управляющ\w*", re.I)
_SUBMIT = re.compile(r"\b(?:представить|предоставить|направить|подать|представлением|представление|поступление|представлению)\b", re.I)
_DATE = r"(?:\d{1,2}\s*[./]\s*\d{1,2}\s*[./]\s*\d{2,4}|[«\"“”]?\s*\d{1,2}[»\"“”]?\s+[а-яё]+\s+\d{4})"
_ADVANCE = re.compile(
    r"\bзаблаговременн\w*|\bдо\s+(?:даты\s+)?окончания\s+(?:срока\s+)?процедур\w*|"
    r"\bдо\s+истечения\s+[а-яё-]*месячного\s+срока|"
    r"\b(?:к|до)\s+(?:дате\s+судебного\s+заседания|(?:указанной|назначенной)\s+(?:выше\s+)?дат[еы])|"
    r"\bне\s+позднее\s+[а-яё\d]+\s+(?:рабочих\s+)?(?:дней|месяцев)\s+с\s+даты|"
    r"\b(?:не\s+позднее|не\s+менее)\b.{0,100}?\bдо\b|"
    r"\bза\s+(?:\d+|[а-яё]+)(?:\s+\([^)]{1,25}\))?\s+(?:(?:рабоч\w*|календарн\w*)\s+)?"
    r"(?:дн\w*|день|месяц\w*|недел\w*)\s+до\b|"
    rf"\b(?:к|до|не\s+позднее(?:\s+чем)?)\s*{_DATE}", re.I | re.S,
)
_NEGATION = re.compile(
    r"\bне\s+(?:обязывать|требовать|представлять|предоставлять|направлять|подавать)|"
    r"\b(?:освободить|освобождается)\b.{0,160}?\b(?:обязанност\w*|представлен\w*)|"
    r"\bотменить\b.{0,120}?\bобязанност\w*", re.I | re.S,
)
_EXCLUDED = re.compile(
    r"\b(?:согласно|в\s+соответствии|на\s+основании)\s+(?:пункт\w*|стать\w*|ст\.)|"
    r"\b(?:просит|просил|ходатайствует|представил|направил|предоставил)\b", re.I,
)
_OTHER_ACTOR = re.compile(r"^(?:\d+[.)]?\s*)?(?:лицам,|должнику\b|кредиторам\b|обязать\s+должника\b)", re.I)
_UNSUPPORTED_TIME = re.compile(r"\bк\s+(?:судебному\s+заседанию|заседанию)|\bв\s+порядке\s+подготовки\s+к\s+судебному", re.I)
_BOUNDARY = re.compile(
    r"[.!?](?=\s+[А-ЯЁ])|;(?=\s*(?:Финансов|Арбитраж|Конкурс|Обязать|Назначить|Признать))|"
    r"\n[ \t]*[-–—][ \t]+|"
    # A semicolon can also be a list of objects sharing one verb/deadline.
    # Split only when a new addressee or temporal instruction starts.
    r";(?=\s*(?:после\s+(?:завершения|окончания)|не\s+позднее|в\s+срок|"
    r"заблаговременно|за\s+(?:\d+|[а-яё]+)\s+(?:дн|рабоч)|до\s+\d))|"
    r"(?=\b(?:Финансовому|Арбитражному|Конкурсному)\s+управляющему\b)"
)
_OBJECT_BULLET = re.compile(r"\n[ \t]*[-–—][ \t]+")
_OBJECT_START = re.compile(r"(?:ходатайств\w*|отч[её]т\w*|актуальн\w*\s+отч[её]т\w*|"
                           r"анализ\w*|сведени\w*|доказательств\w*|реестр\w*|выписк\w*|документ\w*)\b", re.I)
_REPORT_OBJECT_START = re.compile(r"(?:актуальн\w*\s+)?отч[её]т\w*\b", re.I)
_LIST_END = re.compile(r"\n[ \t]*\n|^[ \t]*(?:Разъяснить|Обязать|Назначить|Признать|"
                       r"Финансовому|Арбитражному|Конкурсному|Решение|Определение)\b", re.I | re.M)


def report_object_list_spans(text: str) -> list[tuple[int, int]]:
    """Literal report objects sharing one colon-introduced submission order.

    A bullet with a new temporal instruction does not inherit the header's due
    date. Mentioning a report inside proof/analysis is not a report object.
    """
    spans = []
    for submit in _SUBMIT.finditer(text):
        intro = re.match(r"[^.;!?:]{0,100}:\s*(?=\n[ \t]*[-–—][ \t]+)", text[submit.end():])
        if not intro:
            continue
        actors = list(_ACTOR.finditer(text, max(0, submit.start()-500), submit.start()))
        if not actors:
            continue
        start = actors[-1].start()
        if _OBJECT_BULLET.search(text[start:submit.start()]):
            continue
        first = submit.end()+intro.end()
        limit = min(len(text), first+2500)
        bullets = list(_OBJECT_BULLET.finditer(text, first, limit))
        end = first
        report = False
        for i, bullet in enumerate(bullets):
            stop = bullets[i+1].start() if i+1 < len(bullets) else limit
            if boundary := _LIST_END.search(text, bullet.end(), stop):
                stop = boundary.start()
            content = text[bullet.end():stop].strip()
            if (not _OBJECT_START.match(content) or _ADVANCE.search(content)
                    or re.search(r"\b(?:в\s+срок|после\s+завершения)\b", content, re.I)):
                break
            report |= bool(_REPORT_OBJECT_START.match(content))
            end = stop
            if stop < (bullets[i+1].start() if i+1 < len(bullets) else limit):
                break
        if report:
            spans.append((start, end))
    return spans


def extract_early_report_required(text: str) -> EarlyReportCandidate:
    """Return a candidate boolean, exact source interval, or a refusal reason."""
    headings = list(_HEADING.finditer(text))
    if not headings:
        return EarlyReportCandidate(None, "operative_part_not_found", None, None, None)
    operative_start = headings[-1].end()
    operative = text[operative_start:]
    boundaries = [0] + [m.end() for m in _BOUNDARY.finditer(operative)] + [len(operative)]
    object_lists = report_object_list_spans(operative)
    positives = []
    negatives = []
    unsupported = False
    unresolved_reference = False
    for report in _REPORT.finditer(operative):
        left = max(b for b in boundaries if b <= report.start())
        right = min(b for b in boundaries if b >= report.end())
        if shared := next((span for span in object_lists if span[0] <= report.start() < span[1]), None):
            left, right = shared
        if right - left > 6000:
            continue
        clause = operative[left:right]
        if re.search(r"\bк\s+назначенной\s+(?:выше\s+)?дате\b", clause, re.I) and _SUBMIT.search(clause):
            from .court_regex_report_deadline import resolve_report_date_reference

            if resolve_report_date_reference(text, appointed=True) is None:
                unresolved_reference = True
                continue
        if re.search(r"\bв\s+случае\s+назначения\b", clause, re.I):
            continue
        if _UNSUPPORTED_TIME.search(clause) and _SUBMIT.search(clause):
            unsupported = True
        if _NEGATION.search(clause) and _ADVANCE.search(clause) and _ACTOR.search(clause):
            start, end = operative_start + left, operative_start + right
            negatives.append(EarlyReportCandidate(False, "explicit_negative_order", text[start:end], start, end))
            continue
        if not _SUBMIT.search(clause) or not _ADVANCE.search(clause):
            continue
        # The actor can be introduced in the preceding instruction sentence.
        actor_context = operative[max(0, left - 2500):right]
        if (not _ACTOR.search(actor_context) or _EXCLUDED.match(clause.lstrip())
                or _OTHER_ACTOR.match(clause.lstrip())):
            continue
        # Advance delivery of proof to creditors is not advance report submission.
        advances = list(_ADVANCE.finditer(clause))
        if all(re.search(r"доказательств\w*\s*$", clause[max(0, m.start()-35):m.start()], re.I)
               or re.match(r"\s+направления\s+в\s+адрес\s+кредиторов", clause[m.end():], re.I)
               for m in advances):
            continue
        start = operative_start + left
        end = operative_start + right
        candidate = EarlyReportCandidate(True, "advance_report_order", text[start:end], start, end)
        if _NEGATION.search(clause):
            negatives.append(candidate)
        else:
            positives.append(candidate)
    if positives and negatives:
        return EarlyReportCandidate(None, "conflicting_report_orders", None, None, None)
    if positives:
        return positives[0]
    if negatives:
        item = negatives[0]
        return EarlyReportCandidate(False, "explicit_negative_order", item.evidence, item.start, item.end)
    if unresolved_reference:
        return EarlyReportCandidate(None, "unresolved_report_date_reference", None, None, None)
    if unsupported:
        return EarlyReportCandidate(None, "unsupported_report_time_relation", None, None, None)
    return EarlyReportCandidate(False, "no_advance_report_order", None, None, None)

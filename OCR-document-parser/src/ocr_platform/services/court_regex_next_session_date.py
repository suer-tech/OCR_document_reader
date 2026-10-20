"""Source-bound next-hearing dates; candidates need downstream confirmation.

No dates are derived from a procedure term, operator edits, or today's date.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import re

from ocr_platform.services.court_decision_dates import extract_decision_date_candidates

_MONTHS = {"января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5,
           "июня": 6, "июля": 7, "августа": 8, "сентября": 9, "октября": 10,
           "ноября": 11, "декабря": 12}
_DATE = re.compile(
    r"(?<![\w.])(?P<d>\d{1,2})\s*[.\-/]\s*(?P<m>\d{1,2})\s*[.\-/]\s*(?P<y>\d{4})(?!\d)|"
    r"(?<!\w)[«\"“”„]*\s*(?P<wd>\d{1,2})\s*[»\"“”]*\s*"
    r"(?P<wm>" + "|".join(_MONTHS) + r")\s+(?P<wy>\d{4})(?!\d)", re.I,
)
_HEADING = re.compile(
    r"\b(?:р\s*е\s*ш\s*и\s*л|о\s*п\s*р\s*е\s*д\s*е\s*л\s*и\s*л)\s*(?::|\r?\n)", re.I,
)
_ORDER = re.compile(r"\b(?:назначить|назначено|состоится|отложить|перенести|рассмотреть|рассмотрение\s+отч[её]та)\b", re.I)
_EVENT = re.compile(r"заседани[еяию]|рассмотрени[еяию]|рассмотрению|разбирательств[оа]", re.I)
_CONDITIONAL = re.compile(
    r"(?:вправе|может|могут|будет|будут|право|в\s+случае|при\s+поступлении|после\s+поступления)\b", re.I,
)
_STOP = re.compile(
    r"\b(?:обязать|предложить|утвердить|истребовать|признать|ввести|направить|разъяснить|"
    r"установить|должнику|финансовому\s+управляющему)\b", re.I,
)
_MALFORMED = re.compile(
    r"(?<![\d.])\d{1,2}\s*\.\s*\d{1,2}\s*\.\s*\d{2,3}(?!\d)|"
    r"\d{1,2}\s+(?:" + "|".join(_MONTHS) + r")\s+\d{2,3}(?!\d)", re.I,
)
_CANCEL = re.compile(r"\b(?:отменить|снять\s+с\s+рассмотрения|считать\s+отмен[её]нн[ыо]м)\b", re.I)


@dataclass(frozen=True)
class NextSessionDateCandidate:
    value: str
    quote: str
    start: int
    end: int
    date_start: int
    date_end: int
    role: str = "scheduled_consideration"


@dataclass(frozen=True)
class NextSessionDateResult:
    selected: NextSessionDateCandidate | None
    candidates: tuple[NextSessionDateCandidate, ...]
    problem: str = ""


def _calendar_value(match: re.Match) -> str | None:
    try:
        if match["d"]:
            value = datetime(int(match["y"]), int(match["m"]), int(match["d"]))
        else:
            value = datetime(int(match["wy"]), _MONTHS[match["wm"].casefold()], int(match["wd"]))
        return value.strftime("%d.%m.%Y")
    except ValueError:
        return None


def extract_next_session_date_candidates(text: str) -> NextSessionDateResult:
    """Extract explicit current appointments, abstaining on conflicting dates.

    A later explicit postponement supersedes earlier appointments. Independent
    multiple dates remain ambiguous. Malformed or past dates are not repaired.
    """
    headings = list(_HEADING.finditer(text))
    offset = headings[-1].end() if headings else 0
    body = text[offset:]
    candidates = []
    invalid = False
    superseding_at = -1
    orders = []
    for found in _ORDER.finditer(body):
        # "Назначить рассмотрение отчета" is one appointment, not two commands.
        if found[0].casefold().startswith("рассмотрение") and orders:
            previous = orders[-1]
            gap = body[previous.end():found.start()]
            if len(gap) < 180 and not re.search(r"[.;!?]", gap):
                continue
        orders.append(found)
    for index, order in enumerate(orders):
        if order[0].casefold() in {"назначено", "состоится"} and not headings:
            continue
        before = body[max(0, order.start()-350):order.start()]
        clause_before = re.split(r"[;!?]|[.]\s+(?=[А-ЯЁ]|\d{1,2}[.)](?:\s|$))", before, flags=re.I)[-1]
        if _CONDITIONAL.search(clause_before):
            continue
        end = min(len(body), order.end()+650)
        if index+1 < len(orders):
            end = min(end, orders[index+1].start())
        tail = body[order.end():end]
        if stop := _STOP.search(tail):
            tail = tail[:stop.start()]
        # Event noun can precede the command: "Рассмотрение дела назначить на...".
        event_after = _EVENT.search(tail[:450])
        event_before = _EVENT.search(clause_before)
        consideration = order[0].casefold().startswith("рассмотрение")
        if not event_after and not event_before and not consideration:
            continue
        if _CONDITIONAL.search(tail[:(_DATE.search(tail).start() if _DATE.search(tail) else len(tail))]):
            continue
        dates = list(_DATE.finditer(tail))
        if not dates:
            invalid |= bool(_MALFORMED.search(tail))
            continue
        if malformed := _MALFORMED.search(tail):
            if malformed.start() < dates[0].start():
                invalid = True
                continue
        # A birthday inside a long case description is not an appointment date.
        # Require the appointment preposition immediately before date/time.
        relation = r"\b(?:на|с)\s+(?:\d{1,2}\s*(?:час(?:а|ов)?\.?|[:.])\s*\d{1,2}\s*(?:мин(?:ут[аы]?)?\.?)?\s*)?$"
        if consideration:
            relation = r"\b(?:на|до)\s*$"
            if not re.search(r"осуществляется|состоится|проводится", tail, re.I):
                continue
        linked_dates = [item for item in dates if re.search(relation, tail[:item.start()], re.I)]
        if not linked_dates and order[0].casefold() == "рассмотреть":
            linked_dates = dates[:1]
        if not linked_dates and order[0].casefold() == "состоится":
            linked_dates = [item for item in dates[:1] if not tail[:item.start()].strip()]
        if not linked_dates:
            continue
        first = linked_dates[0]
        if order[0].casefold() in {"отложить", "перенести"} and len(linked_dates) > 1:
            if re.search(r"\bс\s*$", tail[:first.start()], re.I):
                first = linked_dates[-1]
        for found in dates[dates.index(first):]:
            between = tail[first.end():found.start()] if found is not first else ""
            # A second appointment date joined by 'и/или' is a real conflict;
            # dates appearing later in addresses, legal references, etc. are not.
            if found is not first and not re.fullmatch(r"[\s,;]*(?:и|или|либо)[\s,;]*(?:на\s+)?", between, re.I):
                break
            value = _calendar_value(found)
            if value is None:
                invalid = True
                continue
            start = offset+order.start()
            if event_before and (not event_after or event_after.start() > found.start()):
                start = offset+order.start()-len(clause_before)+event_before.start()
            date_start = offset+order.end()+found.start()
            date_end = offset+order.end()+found.end()
            role = "postponed_consideration" if order[0].casefold() in {"перенести", "отложить"} else "scheduled_consideration"
            if consideration and re.search(r"без\s+проведения\s+судебного\s+заседания", tail, re.I):
                role = "consideration_without_hearing"
            candidates.append(NextSessionDateCandidate(value, text[start:date_end], start, date_end,
                                                       date_start, date_end, role))
            if role == "postponed_consideration":
                superseding_at = order.start()+offset
    if superseding_at >= 0:
        candidates = [item for item in candidates if item.date_start >= superseding_at]
    for cancellation in _CANCEL.finditer(body):
        end = min(len(body), cancellation.end()+200)
        if following := _ORDER.search(body, cancellation.end()):
            end = min(end, following.start())
        context = body[cancellation.end():end]
        if not _EVENT.search(context):
            continue
        dates = {value for found in _DATE.finditer(context) if (value := _calendar_value(found))}
        before = [item for item in candidates if item.date_start < offset+cancellation.start()]
        if not dates and len({item.value for item in before}) > 1:
            return NextSessionDateResult(None, tuple(candidates), "ambiguous_cancellation")
        candidates = [item for item in candidates if item.date_start >= offset+cancellation.start()
                      or (dates and item.value not in dates)]
    unique = tuple(dict.fromkeys(candidates))
    act = extract_decision_date_candidates(text).selected
    if act:
        act_date = datetime.strptime(act.value, "%d.%m.%Y")
        if any(datetime.strptime(item.value, "%d.%m.%Y") <= act_date for item in unique):
            return NextSessionDateResult(None, unique, "appointment_not_after_act")
    if invalid:
        return NextSessionDateResult(None, unique, "invalid_appointment_date")
    if len({item.value for item in unique}) > 1:
        return NextSessionDateResult(None, unique, "conflicting_appointments")
    if unique:
        return NextSessionDateResult(unique[0], unique)
    return NextSessionDateResult(None, (), "no_explicit_appointment")

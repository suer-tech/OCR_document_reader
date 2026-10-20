"""Source-bound venue candidates for an explicitly scheduled consideration.

Pure extraction only: no court directory, header fallback, date repair, confidence
assignment, or automatic confirmation. Positions always refer to the input text.
"""
from __future__ import annotations

from dataclasses import dataclass
import re

from ocr_platform.services.court_regex_next_session_date import extract_next_session_date_candidates


_ADDRESS = re.compile(r"\bпо\s+адресу\s*:?\s*", re.I)
_VENUE = re.compile(r"(?:в\s+(?:помещении|здании)\s+.{0,150}?суд[а-яё]*|"
                    r"(?:судебное\s+)?заседание\s+состоится)", re.I | re.S)
_STOP = re.compile(
    r"\b(?:обязать|предложить|утвердить|истребовать|направить|разъяснить|"
    r"установить|назначить|перенести|отложить|финансовому\s+управляющему|"
    r"должнику|определение\s+(?:может|подлежит)|решение\s+(?:может|подлежит)|"
    r"с\s+даты|при\s+отсутствии|в\s+соответствии|по\s+запросу|согласно)\b", re.I,
)
_NON_VENUE = re.compile(r"корреспонденц|регистраци|проживани|управляющ|саморегулируем|\bсро\b", re.I)
_ROOM = re.compile(r"\b(?:зал(?:е|а)?(?:\s+судебных\s+заседаний)?|ка[аб]+\.?|кабинет(?:е|а)?)"
                   r"\s*№?\s*\d+[а-яa-z]?(?:[./-]\d+)?", re.I)
_STREET = re.compile(r"\b(?:ул(?:ица|ице|ицы)?\.?|проспект|пр\.|пр[.\s-]*(?:кт|т)|пер(?:еулок)?\.?|"
                     r"площадь|пл\.|набережная|наб\.|Кремль|шоссе|бульвар|б-р)(?=\W|$)", re.I)
_ABBREVIATIONS = {"г", "гор", "ул", "д", "стр", "корп", "каб", "кааб", "пр", "пр-т", "пр-кт",
                  "пер", "пл", "наб", "обл", "респ", "им", "б"}
_TAIL = re.compile(r"[,;\s]*(?:тел(?:ефон)?\.?\s*:?|судья\b)", re.I)
_FLOOR = re.compile(r"[,\s]*(?:\d+\s*(?:-[а-яё]+)?\s*этаж|этаж\s*\d+)", re.I)
_DIRECT_PREFIX = re.compile(r"(?:\s|[,.:;()—-]|\d|года\b|г\b|в\b|на\b|с\b|"
                            r"час(?:а|ов)?\b|мин(?:ут[аы]?)?\b)*", re.I)


@dataclass(frozen=True)
class AddressSourceSpan:
    quote: str
    start: int
    end: int


@dataclass(frozen=True)
class HearingAddressCandidate:
    value: str
    quote: str
    start: int
    end: int
    address_start: int
    address_end: int
    appointment_date: str
    sources: tuple[AddressSourceSpan, ...]
    role: str = "scheduled_consideration_venue"
    location_kind: str = "street_address"
    requires_review: bool = True


@dataclass(frozen=True)
class HearingAddressResult:
    selected: HearingAddressCandidate | None
    candidates: tuple[HearingAddressCandidate, ...]
    problem: str = ""


def _clean(value: str) -> str:
    """Collapse whitespace; preserve source spelling, postal code and room."""
    return re.sub(r"\s+", " ", value).strip(" \t\r\n,;:.()")


def _location_end(value: str) -> int:
    """Stop at a sentence boundary, preserving address abbreviations/decimals."""
    end = len(value)
    for pattern in (_STOP, _TAIL):
        if found := pattern.search(value):
            end = min(end, found.start())
    for found in re.finditer(r"\.(?=\s|$)", value[:end]):
        prefix = value[:found.start()]
        token = re.search(r"([А-ЯЁа-яё-]+)$", prefix)
        word = token[1].casefold() if token else ""
        # Initials are legal street names: ул. Л. Павличенко.
        if word in _ABBREVIATIONS or (len(word) == 1 and word.isalpha()):
            continue
        return found.start()
    return end


def extract_court_hearing_address_candidates(text: str) -> HearingAddressResult:
    """Find the venue of the selected explicit next appointment, or abstain.

    Only the local scheduling clause and its immediate venue sentence are read.
    A bare header address never supplies a venue. A room alone is retained as a
    partial candidate, but cannot be selected as a complete street address.
    """
    dates = extract_next_session_date_candidates(text)
    appointment = dates.selected
    if appointment is None:
        return HearingAddressResult(None, (), "appointment:" + dates.problem)
    # A second command or an obligation terminates the appointment context.
    limit = min(len(text), appointment.date_end + 650)
    after = text[appointment.date_end:limit]
    if stop := _STOP.search(after):
        limit = appointment.date_end + stop.start()
        after = text[appointment.date_end:limit]
    addresses = []
    partials = []
    for marker in _ADDRESS.finditer(after):
        before = after[:marker.start()]
        # A location following the same date is linked through a court-premises
        # phrase or through "заседание состоится"; exclude correspondence etc.
        linked = _VENUE.search(before)
        direct = bool(_DIRECT_PREFIX.fullmatch(before))
        if not linked and not direct:
            continue
        if _NON_VENUE.search(before[linked.start():] if linked else before):
            continue
        start = appointment.date_end + marker.end()
        location = text[start:limit]
        end = start + _location_end(location)
        raw = text[start:end].rstrip(" \t\r\n,;:.()")
        end = start + len(raw)
        if not raw or not _STREET.search(raw) or not re.search(r"\d", raw):
            continue
        spans = [AddressSourceSpan(text[start:end], start, end)]
        value = _clean(raw)
        # Some acts put the room before the street: "зал 610 в помещении ...".
        earlier_room = _ROOM.search(before)
        if earlier_room and not _ROOM.search(raw):
            room_start = appointment.date_end + earlier_room.start()
            room_end = appointment.date_end + earlier_room.end()
            if floor := _FLOOR.match(text[room_end:start]):
                room_end += floor.end()
            spans.append(AddressSourceSpan(text[room_start:room_end], room_start, room_end))
            value += ", " + _clean(text[room_start:room_end])
        addresses.append(HearingAddressCandidate(
            value, text[appointment.start:end], appointment.start, end, start, end,
            appointment.value, tuple(spans),
            role=("consideration_without_hearing_venue" if appointment.role == "consideration_without_hearing"
                  else "scheduled_consideration_venue"),
        ))
    if not addresses:
        if room := _ROOM.search(after):
            start = appointment.date_end + room.start()
            end = appointment.date_end + room.end()
            floors_before = list(_FLOOR.finditer(after[:room.start()]))
            if floors_before and not after[floors_before[-1].end():room.start()].strip(" ,\t\r\n"):
                start = appointment.date_end + floors_before[-1].start()
                while text[start] in " ,\t\r\n":
                    start += 1
            if floor := _FLOOR.match(text[end:limit]):
                end += floor.end()
            partials.append(HearingAddressCandidate(
                _clean(text[start:end]), text[appointment.start:end], appointment.start,
                end, start, end, appointment.value,
                (AddressSourceSpan(text[start:end], start, end),), location_kind="room_only",
            ))
        return HearingAddressResult(None, tuple(partials), "partial_location_only" if partials else "no_linked_location")
    if len({item.value for item in addresses}) != 1:
        return HearingAddressResult(None, tuple(addresses), "ambiguous_locations")
    return HearingAddressResult(addresses[0], tuple(addresses))

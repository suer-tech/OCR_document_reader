"""Conservative RTK3 candidates from the current court act."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict

from ocr_platform.services import rtk3_requisites as req
from ocr_platform.services.rtk3_evidence import (
    Component, Fact, compact, excluded_component, money, previous_cost, source_case,
)


class Components(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[Component]
    complete: bool
    quote: str


class Salary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: bool | None
    quote: str


class CourtTotal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    amount: str
    scope: Literal["with_duty", "without_duty", "unknown"]
    quote: str


@dataclass
class Claim:
    quote: str
    fields: dict[str, Fact | Components | Salary]
    total: CourtTotal | None
    contract_number: str | None = None
    parent_total_conflict: bool = False
    contract_reference: Fact | None = None
    cost_reference: Fact | None = None
    itemized_admission: Decimal | None = None


@dataclass
class Candidates:
    fields: dict[str, Fact]
    claims: list[Claim]
    creditor_tax_subject: str | None = None


OPERATIVE = re.compile(r"\bо\s*п\s*р\s*е\s*д\s*[еи]\s*л\s*и\s*л[аи]?\s*(?:[:：]|(?=\r?\n))", re.I)
ENDING = re.compile(
    r"\b(?:(?:настоящее\s+)?определение(?:\s+суда)?[, ]+(?:может|подлежит|вступает|изготовлено|вынесенное|вынесено|принято)|"
    r"на определение может|в случае подачи апелляционной жалобы|"
    r"лица, участвующие в деле, вправе|заявление о составлении мотивированного|"
    r"судья\s+[А-ЯЁ]|информация о движении дела|"
    r"мотивированное определение составляется|по заявлению лица, участвующего в деле, арбитражный суд составляет|"
    r"должнику разъясняется|суд разъясняет)", re.I,
)
COMMAND = re.compile(
    r"(?:^\s*(?:[0-9]{1,2}[.)]\s*)?|(?<=[.!?;])\s+(?:[0-9]{1,2}[.)]\s*|[0-9]{1,2}\s+(?:\d{3,}_\d{3,}\s+)?)?|"
    r"\s+[0-9]{1,2}[.)]\s*|(?<=,)\s+(?=Взыскать\b)|"
    r"(?<=\s)(?=(?-i:Взыскать)\b))"
    r"(?:(?:указанн\w*\s+|заявленн\w*\s+)?требован\w*\s+"
    r"(?:(?![;.!?]\s+(?:включить|признать|взыскать|определен|в\s+остальной|указанн\w*\s+требован)).){0,720}?|"
    r"заявлени\w*\s+(?:(?![.!?]\s+(?:включить|признать|взыскать)).){0,320}?|"
    r"в\s+удовлетворении\s+(?:заявлени|требован|ходатайств)\w*\s+(?:(?![.!?;]\s+включить).){0,560}?|"
    r"финансовому\s+управляющему\s+(?:имуществом\s+(?:[А-ЯЁ][а-яё-]+\s+){2,3})?|"
    r"в\s+восстановлении\s+срока[^.]{0,130}?\s+|"
    r"производство\s+по\s+заявлени\w*\s+(?:(?![.!?]\s+(?:Включить|Признать)).){0,560}?|"
    r"принять\s+уточнени\w*.{0,180}?\.\s+|в\s+остальной\s+части[^.]{0,130}?\s+)?"
    r"(?P<verb>включить|признать|взыскать|отказать|выделить|прекратить|возвратить|вернуть|"
    r"учесть|учитывать|оставить|отложить|установить|удовлетворить)\b", re.I,
)
QUEUE_WORDS = (
    r"перв(?:ую|ой|ая|ою)", r"втор(?:ую|ой|ая|ою)", r"трет(?:ью|ьей|ья|ьею|ий)", r"четв[её]рт(?:ую|ой|ая|ою)",
)
QUEUE = re.compile(
    r"\b(?P<num>" + "|".join(QUEUE_WORDS) + r"|[1-4](?:-?(?:ю|й|я|ой|ую|ей|ью))?)\s+очеред\w*"
    r"|\bочеред\w*\s+(?P<reverse>[1-4])\b"
    r"|\bв\s+(?P<section>третий)\s+раздел\s+реестра\s+требований\s+кредиторов\b"
    r"|(?P<outside>\bза\s+реестром\b|после\s+(?:полного\s+)?удовлетворения\s+требований\s+кредиторов"
    r"\s*,?\s*включ[её]н\w*\s+в\s+реестр)", re.I,
)
AMOUNT = (
    r"(?<![\w,.])(?:[0-9]{1,3}(?:\.[0-9]{3})+(?:, ?[0-9]{1,2})?\s*(?:руб(?:лей|ля|ль)?\b\.?|р\.)|"
    r"(?:[0-9]{1,3}(?: [0-9]{3})+|[0-9]+)"
    r"(?:(?:[.,] ?[0-9]{1,2})?(?:\s*\([а-яё -]{3,200}\))?\s*(?:руб(?:лей|ля|ль)?\.?\b\.?|р\.)"
    r"(?:\s*[0-9]{1,2}\s*(?:коп(?:еек|ейки|ейка)?\.?\b\.?|к\.))?"
    r"|[.,] ?[0-9]{1,2}(?=\.?\s+(?:[-–—]\s*)?(?:основн\w*\s+долг|процент\w*|неустойк\w*|пени)\b)))"
)
AMOUNTS = re.compile(AMOUNT, re.I)
GROSS = re.compile(r"(?:в\s+(?:(?:общем\s+)?размере|(?:общей\s+)?сумме)|на\s+(?:общую\s+)?сумму)\s*$", re.I)
BREAKDOWN = re.compile(r"\b(?:в\s+том\s+числе|в\s*т\s*\.\s*ч\s*\.|"
                       r"из\s+(?:них|которых|которой)|в\s+составе\s*:)\s*[:—–,-]?", re.I)
COLLATERAL = re.compile(r"\bзалог\w*", re.I)
SALARY_WORDS = re.compile(
    r"(?:задолжен\w*\s+по\s+(?:выплате\s+)?заработн\w*\s+плат\w*|"
    r"заработн\w*\s+плат\w*|выходн\w*\s+пособи\w*)", re.I,
)
TAX_KIND, CONTRACTS = req.TAX_KIND, req.GROUND_PATTERNS
LEGAL_NAME = (
    r"(?:(?:" + "|".join(pattern for pattern, _ in req.LEGAL_FORMS[:4])
    + r"|\b(?:ПАО|АО|ООО|НАО|ПКО|ФГКУ|ФГБУ)\b)\s+(?:(?:КБ|ПКО|МКК|МФК|Банк|"
    r"микрофинансов\w*\s+компани\w*|микрокредитн\w*\s+компани\w*|"
    r"профессиональн\w*\s+коллекторск\w*\s+организаци\w*|коллекторск\w*\s+бюро)\s+)?"
    r'(?:["«](?:ПКО|профессиональн\w*\s+коллекторск\w*\s+организаци\w*)\s*["«][^"»]{2,100}["»]["»]?'
    r'|«[^»]{2,120}»(?:\s+(?-i:БАНК)\b(?=\s*(?:[.,;\(]|по\s+кредитн|о\s+(?:включении|возмещении)|$)))?'
    r'|"[^"]{2,120}"|[„“][^“”]{2,120}[“”]'
    r'|(?-i:[А-ЯЁA-Z][А-ЯЁа-яёA-Za-z0-9-]{2,})(?=\s*(?:\(|,|[0-9]|\b(?:расходы|удовлетворить|обоснованн\w*|о\s+включении|включить|в\s+(?:реестр|третью|размере))|$)))'
    r"|Банк(?:а|у|ом)?\s+ВТБ(?:\s*\(ПАО\))?"
    r'|Банк(?:а|у|ом)?\s+[«"][^»"]{2,100}[»"]\s*\((?:ПАО|АО)\)'
    r"|Банк(?:а|у|ом)?\s+[А-ЯЁA-Z][А-ЯЁа-яёA-Za-z-]{1,60}\s*\((?:ПАО|АО)\)"
    r'|[«"][^»"]{2,100}[»"]\s*\((?:ПАО|АО|(?:публичн\w*\s+)?акционерн\w*\s+обществ\w*)\)'
    r"|" + req.TAX_OFFICE + r"|" + req.FEDERAL_TAX_NAME + r")"
)
CREDITOR = re.compile(r"(?:\bтребован\w*|\bзаявлени\w*)\s+(?:(?:кредитор|уполномоченн\w*\s+орган)\w*"
                      r"(?:\s+Российск\w*\s+Федераци\w*)?(?:\s+в\s+лице)?\s*[-–—]?\s+)?(?P<name>" + LEGAL_NAME + ")", re.I)
PAYEE = re.compile(r"\bв\s+пользу\s+(?:кредитора\s+)?(?P<name>" + LEGAL_NAME + ")", re.I)
TAX_REPRESENTATION = re.compile(
    r"\s*(?:,\s*г\.\s*[а-яё-]+)?\s*(?:\([^()]{0,320}\)\s*)?,?\s*в\s+лице\s+(?P<name>"
    + req.TAX_OFFICE + ")", re.I,
)
PUBLIC_TAX_CREDITOR = "ФНС России"
CATEGORY_FIELDS = {
    "principal_debt": {"principal", "interest", "prior_cost"},
    "financial_sanctions": {"sanction"},
    "pau_only_amount": {"duty"},
}
SEPARATE_SANCTIONS = re.compile(
    r"учитывающ\w*\s+отдельно(?:\s+в\s+реестре\s+требований\s+кредиторов)?\s+и\s+"
    r"подлежащ\w*\s+удовлетворению\s+после\s+погашения\s+основной\s+суммы", re.I,
)
CURRENT_DUTY = re.compile(
    r"(?:по|за\s+(?:рассмотрение|подачу))\s+(?:настоящ\w*|данн\w*|обособленн\w*)\s+"
    r"(?:заявлени|спор|требован)\w*|(?:пункт\w*\s*3\s*стать\w*|п\.\s*3\s*ст\.)\s*137\b",
    re.I,
)
INLINE_SEPARATE_PAYMENT = re.compile(
    r",\s*котор\w*\s+учесть\s+в\s+реестре\s+отдельно\s+"
    r"(?:для\s+удовлетворения\s+)?после\s+погашения\s+основн\w*\s+(?:сумм\w*|задолжен\w*)",
    re.I,
)
INLINE_DUTY = re.compile(
    r"\b(?:а\s+также|и\s+отдельно)\s+(?:расход\w*\s+по\s+(?:уплате|оплате)\s+)?"
    r"(?:государственн\w*\s+пошлин\w*|госпошлин\w*)\s+(?:в\s+размере|в\s+сумме)\s+"
    rf"(?P<amount>{AMOUNT})"
    r"\s+с\s+уч[её]том\s+положени\w*\s+(?:пункт\w*\s*3\s*стать\w*|п\.\s*3\s*ст\.)\s*137\b",
    re.I,
)
INLINE_ACCOUNTED_DUTY = re.compile(
    rf"\bа\s+также\s+(?P<amount>{AMOUNT})"
    r"\s+расходов\s+по\s+(?:уплате|оплате)\s+государственной\s+пошлины,\s+"
    r"уплаченной\s+платежным\s+поручением\s+от\s+\d{1,2}\.\d{1,2}\.\d{4}\s*№\s*[\w/-]+\s*\.\s+"
    r"Требования\s+в\s+части\s+неустойки\s+и\s+государственной\s+пошлины\s+учитываются\s+отдельно\s+"
    r"в\s+реестре\s+требований\s+кредиторов\b[^.]{0,150}\bпосле\s+погашения\s+основной\s+суммы",
    re.I,
)
SECTION_ACCOUNTED_DUTY = re.compile(
    r"\bа\s+также\s+государственн\w*\s+пошлин\w*\s+в\s+сумме\s+"
    rf"(?P<amount>{AMOUNT})\s+с\s+режимом\s+уч[её]та\s+и\s+удовлетворения\s+данного\s+требования"
    r"\s+к\s+должнику\s+в\s+порядке,\s*установленном\s+пунктом\s+3\s+статьи\s+137\b",
    re.I,
)
FEE_ACCOUNTING = re.compile(
    r"(?<=\.)\s+Требование\s+в\s+части\s+(?:пени\s*/\s*неустойки\s+и\s+штрафа"
    r"\s*\(при наличии\)|неустойки\s+и\s+штрафа),\s+"
    rf"государственная\s+пошлина\s+в\s+размере\s+(?P<amount>{AMOUNT})"
    r"\s+учитываются\s+в\s+составе\s+требований\s+кредиторов\s+[^.]{0,100}"
    r"отдельно\s+и\s+подлежат\s+удовлетворению\s+после\s+погашения\b[^.]{0,200}",
    re.I,
)
STANDALONE_FEE_ACCOUNTING = re.compile(
    r"^требован\w*\s+об\s+оплате\s+государственн\w*\s+пошлин\w*\s+в\s+размере\s+"
    rf"{AMOUNT}\s+учитыва(?:ть|ется|ются)\b[^.]{{0,220}}\bпосле\s+погашения\b", re.I,
)
AGREEMENT_LIST_ITEM = re.compile(r"(?<=[:;])\s*по\s+соглашени\w*\s+о\s+кредитовани\w*\s*№", re.I)
AGREEMENT_LIST_FEE = re.compile(
    rf"(?<=;)\s*(?P<amount>{AMOUNT})\s*[-–—]\s*расход\w*\s+(?:на\s+оплату|по\s+уплате)"
    r"\s+государственн\w*\s+пошлин\w*,\s*в\s+порядке\s+(?:положени\w*\s+)?"
    r"(?:пункт\w*\s*3\s*стать\w*|п\.\s*3\s*ст\.)\s*137\b", re.I,
)
CREDIT_CONTRACT_NUMBER = re.compile(
    r"\b(?:по|в\s+рамках)\s+кредитн\w*\s+договор\w*\s+№\s*([\w./-]+)", re.I,
)
EXPIRED_REGISTRY_ORDER = re.compile(
    r"\bв\s+(?:удовлетворении\s+заявлени\w*\s+кредитора\s+о\s+)?восстановлении\s+срока"
    r"\b.{0,250}\bотказать\b.{0,1300}\bв\s+порядке\s*,?\s*установленном\s+"
    r"(?:пунктом\s+4\s+статьи|п\.\s*4\s+ст\.)\s*142\b", re.I,
)
CONTRACT_BASIS = re.compile(
    r"\b(?:(?:по|на\s+основании|в\s+рамках|основанн\w*\s+на)\s+(?:"
    + CONTRACTS["Кредитный договор"] + "|" + CONTRACTS["Договор займа"] + ")"
    r"|процент\w*\s+за\s+пользование\s+займом\b)", re.I,
)
BASIS_INTRO = r"(?:по|из|на\s+основании|в\s+рамках|основанн\w*\s+на)"
BASIS_NUMBER = r"\s*:?\s*(?:№|N|#)\s*[\w/-]+(?:\.[\w/-]+)*"
BASIS_DATE_DIGIT = r"[0-9ОоOo]"
# A quotation keeps the printed date even when the calendar date is unreadable or invalid.
BASIS_DATE_LITERAL = (
    rf'(?:{BASIS_DATE_DIGIT}{{1,3}}\s*[./-]\s*{BASIS_DATE_DIGIT}{{1,3}}\s*[./-]\s*'
    rf'{BASIS_DATE_DIGIT}{{2,4}}|[«"]?{BASIS_DATE_DIGIT}{{1,2}}[»"]?\s+[а-яё]+\s+{BASIS_DATE_DIGIT}{{4}})'
    r'(?:\s*г\.(?!\w)|\s+года\b|\b)(?![./-][0-9])'
)
BASIS_DATE = rf"\s+от\s+{BASIS_DATE_LITERAL}"
BASIS_BARE_NUMBER = (
    rf"\s+(?=[\w./-]*[0-9])[\w/-]+(?:\.[\w/-]+)*(?={BASIS_DATE}|\s+(?:в\s+размере|на\s+сумму)\b)"
)
BASIS_REFERENCE = rf"(?:(?:{BASIS_NUMBER}|{BASIS_BARE_NUMBER})(?:{BASIS_DATE})?|(?:{BASIS_DATE})(?:{BASIS_NUMBER})?)"
BASIS_PREFIX = re.compile(
    rf"\b(?:(?:(?:по\s+)?(?:задолжен\w*|требован\w*|долг\w*)\s+|задолжен\w*,\s+(?=основанн))?"
    rf"{BASIS_INTRO}|неисполнени\w*)\s*$", re.I,
)
BASIS_CREDIT_LINE = re.compile(
    r"\s+о\s+предоставлени\w*\s+(?:возобновляем\w*\s+)?кредитн\w*\s+лини\w*", re.I,
)
BASIS_TAX_PERIOD = re.compile(r"\s+за\s+(?:19|20)\d{2}(?:\s*[-–—]\s*(?:19|20)\d{2})?\b"
                              r"(?:\s+(?:гг?\.|год(?:ы|а)?\b))?", re.I)
BASIS_SIGNING = re.compile(
    r"\b\d{1,2}\.\d{1,2}\.\d{4}\s+между\s+[^;]{1,140}?\s+и\s+[^;]{1,80}?\s+заключ[её]н\w*\s*$", re.I,
)
BASIS_QUALIFIER = re.compile(
    rf"\s*\(\s*кредитн\w*\s+карт\w*\s*\)|\s+пут[её]м\s+подписани\w*\s+"
    rf"(?:эмиссионн\w*(?:{BASIS_DATE})?\s+)?контракт\w*{BASIS_REFERENCE}", re.I,
)
OCR_CONTRACT_REFERENCE = re.compile(
    rf"\b{BASIS_INTRO}\s+(?:[кk][рp][еe]д[иi]?тн\w*\s+)?"
    rf"д[оo]?г[оo]в[оo]?[рp]\w*(?:\s+(?:микро)?з[аa]йм\w*)?{BASIS_REFERENCE}", re.I,
)
BASIS_LIST_SEPARATOR = re.compile(r"\s*(?:[,;]\s*(?:(?:и|а\s+также)\s*)?|(?:и|а\s+также)\s+)\s*", re.I)


def operative_part(text: str) -> str:
    markers = list(OPERATIVE.finditer(text))
    if len(markers) != 1:
        return ""
    return ENDING.split(text[markers[0].end():].strip(), maxsplit=1)[0].strip()


def queue_candidates(text: str) -> list[Fact]:
    found = {}
    for match in QUEUE.finditer(text):
        if match["reverse"] and re.match(r"\s+[АA]\d{1,3}-\d+/\d{4}\b", text[match.end():], re.I):
            continue
        word = match["num"] or match["reverse"] or match["section"]
        if not word:
            value = "За реестром"
        elif word[0].isdigit():
            value = word[0] + " очередь"
        else:
            value = next(f"{i} очередь" for i, pattern in enumerate(QUEUE_WORDS, 1)
                         if re.fullmatch(pattern, word, re.I))
        found.setdefault(value, Fact(value=value, quote=match[0]))
    if (len(found) == 2 and 'За реестром' in found and len(list(AMOUNTS.finditer(text))) == 1
            and re.search(r"как\s+подлежащ\w*\s+удовлетворению\s+за\s+сч[её]т\s+имущества", text, re.I)):
        # For one expressly identified amount, its actual satisfaction order
        # overrides a generic registry queue mentioned in the same sentence.
        return [found['За реестром']]
    return list(found.values())


def claimant_matches(part: str):
    for match in CREDITOR.finditer(part):
        preceding = part[:match.start()]
        explanations = list(re.finditer(r"\bРазъяснить\b", preceding, re.I))
        decisions = list(COMMAND.finditer(preceding))
        if not explanations or (decisions and explanations[-1].start() < decisions[-1].start()):
            yield match


def creditor_candidate(text: str, operative: str) -> Fact | None:
    for part in (operative, text[:4500]):
        found = {}
        represented = set()
        for match in claimant_matches(part):
            name = re.sub(r"^банка\s+втб", "Банк ВТБ", match["name"], flags=re.I)
            quote = match[0]
            if req.tax_identity(name) == "фнс" and (office := TAX_REPRESENTATION.match(part, match.end())):
                name = office["name"]
                quote = part[match.start():office.end()]
                represented.add(req.tax_identity(name))
            if re.fullmatch(r"Федеральн\w*\s+налогов\w*\s+служб\w*", name, re.I):
                # Preserve the named federal authority, independently of its representative.
                name = "Федеральная налоговая служба"
            name = re.sub(
                r'["«](ПКО|профессиональн\w*\s+коллекторск\w*\s+организаци\w*)\s*["«]([^"»]+)["»]["»]?',
                r'ПКО «\2»', name, flags=re.I,
            )
            value = req.normalize_creditor(name)
            found.setdefault(req.tax_identity(value) or req.identity(value), Fact(value=value, quote=quote))
        if len(represented) == 1 and set(found) == {"фнс", *represented}:
            # Repeated references to the same federal claimant may omit its
            # representative, including when OCR splits the office's name.
            found.pop("фнс")
        if len(found) > 1:
            return None
        if found:
            return next(iter(found.values()))
    return None


def tax_creditor_subject(creditor: Fact, text: str, operative: str) -> tuple[str | None, str]:
    """Select the named INN owner separately from the public tax creditor name."""
    if req.tax_identity(creditor.value) != "фнс":
        return creditor.value, creditor.quote
    intro = re.split(r"\b(?:у\s*с\s*т\s*а\s*н\s*о\s*в\s*и\s*л|"
                     r"о\s*п\s*р\s*е\s*д\s*е\s*л\s*и\s*л)[аи]?\s*:", text[:4500], maxsplit=1, flags=re.I)[0]
    for part in (operative, intro):
        offices = {}
        for match in claimant_matches(part):
            name, quote = match["name"], match[0]
            if req.tax_identity(name) == "фнс":
                office = TAX_REPRESENTATION.match(part, match.end())
                if not office:
                    continue
                name, quote = office["name"], part[match.start():office.end()]
            if re.fullmatch(req.TAX_OFFICE, name, re.I):
                name = req.normalize_creditor(name)
                offices[req.tax_identity(name)] = (name, quote)
        if len(offices) > 1:
            return None, creditor.quote
        if offices:
            return next(iter(offices.values()))
    return creditor.value, creditor.quote


def _listed_contract_wording(operative: str, claims: list[Claim]) -> str | None:
    contracts = [claim for claim in claims if claim.contract_number]
    if len(contracts) < 2 or len({claim.contract_number.casefold() for claim in contracts}) != len(contracts):
        return None
    if any(not claim.contract_number and not _separate_fee(claim.quote) for claim in claims):
        return None
    start, end, cursor = None, 0, 0
    for claim in contracts:
        offset = operative.find(claim.quote, cursor)
        reference = re.match(
            rf"(?:[-–—]\s+|\d+\.\d+\.\s+требован\w*\s+)?(?P<basis>{CONTRACT_BASIS.pattern}\s*:?\s*)?"
            rf"(?:{BASIS_DATE})?\s*(?:№|N|#)?\s*{re.escape(claim.contract_number)}(?![\w./-])(?:{BASIS_DATE})?",
            claim.quote, re.I,
        )
        if offset < 0 or reference is None:
            return None
        if cursor and operative[cursor:offset].strip(" ,;.-–—"):
            return None
        if start is None:
            if reference["basis"]:
                start = offset + reference.start("basis")
            else:
                left = max(0, offset - 150)
                introduction = operative[left:offset]
                heading = re.search(rf"{CONTRACT_BASIS.pattern}\s*:\s*$", introduction, re.I)
                if heading is None:
                    return None
                start = left + heading.start()
                for named_claim in CREDITOR.finditer(introduction, endpos=heading.start()):
                    if not introduction[named_claim.end():heading.start()].strip():
                        start = left + named_claim.start()
        end = offset + reference.end()
        cursor = offset + len(claim.quote)
    return operative[start:end]


def grounds_candidates(text: str, operative: str, claims: list[Claim]) -> dict[str, Fact]:
    listed = _listed_contract_wording(operative, claims)
    result = {"grounds_ocr": Fact(value=listed, quote=listed)} if listed else {}
    for is_operative, raw_source in ((True, operative), (False, text)):
        source = req.GENERAL_SANCTIONS_NOTICE.sub(lambda match: " " * len(match[0]), raw_source)
        matches = [(category, m) for category, pattern in CONTRACTS.items()
                   for m in re.finditer(pattern, source, re.I)
                   if not req.NEGATED_GROUND.search(source[max(0, m.start() - 45):m.start()])]
        if is_operative:
            matches += [("Налоговая задолженность", m) for m in re.finditer(
                r"\bпо\s+(?:обязательн\w*\s+платеж\w*|налогам\s+и\s+пеням)\b", source, re.I,
            )]
        if not matches and is_operative:
            matches = [("Налоговая задолженность", m) for m in re.finditer(
                rf"\b{TAX_KIND}\b|\bпо\s+уплате\s+обязательных\s+платежей(?=\s+в\s+общем\s+размере)",
                source, re.I,
            )]
        if not matches:
            continue
        categories = {category for category, _ in matches}
        if len(categories) != 1:
            return result
        category, first = matches[0]
        result["grounds"] = Fact(value=category, quote=first[0])
        if listed:
            return result
        references = {}
        for _, match in matches:
            detail = BASIS_CREDIT_LINE.match(source, match.end()) if category == "Кредитный договор" else None
            name_end = detail.end() if detail else match.end()
            suffix = re.match(rf"(?:{BASIS_REFERENCE})?", source[name_end:], re.I)[0]
            if not suffix and category == "Налоговая задолженность" and re.search(rf"{TAX_KIND}$", match[0], re.I):
                period = BASIS_TAX_PERIOD.match(source, name_end)
                if period:
                    suffix = period[0]
            if suffix:
                continuation = re.match(
                    rf"(?:(?:\s*[;,]\s*(?:и\s+)?|\s+и\s+)(?:{BASIS_NUMBER})(?:{BASIS_DATE})?"
                    rf"|\s+и{BASIS_DATE})*",
                    source[name_end + len(suffix):], re.I,
                )[0]
                suffix += continuation
                if re.search(r"\bдоговор(?:ам|ами|ах|ов)\b", match[0], re.I) and re.fullmatch(BASIS_DATE, suffix, re.I):
                    dates = re.match(rf"(?:,\s*{BASIS_DATE_LITERAL})*", source[name_end + len(suffix):], re.I)
                    suffix += dates[0]
            qualifier = BASIS_QUALIFIER.match(source, name_end + len(suffix))
            if qualifier:
                suffix += qualifier[0]
            preceding = source[max(0, match.start() - 80):match.start()]
            prefix = BASIS_PREFIX.search(preceding)
            if not prefix and re.match(r"процент\w*\s+за\s+пользование\s+займом\b", match[0], re.I):
                prefix = re.search(rf"{AMOUNT}\s*$", preceding, re.I)
            if not prefix and category in {"Кредитный договор", "Договор займа"}:
                prefix = re.search(
                    rf"\bо\s+включении\s+требован\w*\s+в\s+размере\s+{AMOUNT}\s*$", preceding, re.I,
                ) or BASIS_SIGNING.search(source[max(0, match.start() - 250):match.start()])
            key = compact(source[match.start():name_end] + suffix)
            start = match.start() - (len(prefix[0]) if prefix else 0)
            end = name_end + len(suffix)
            if key not in references or end - start > references[key][1] - references[key][0]:
                references[key] = start, end
        detailed = [(start, end) for start, end in references.values() if re.search(r"\d", source[start:end])]
        wording = sorted(detailed or references.values())
        wording = [(start, end) for start, end in wording
                   if not any(left <= start and end <= right and (left, right) != (start, end)
                              for left, right in wording)]
        if category == "Налоговая задолженность":
            general = [(start, end) for start, end in wording if re.search(
                r"обязательн\w*\s+платеж", source[start:end], re.I,
            )]
            if general:
                wording = [max(general, key=lambda span: span[1] - span[0])]
        if wording and all(BASIS_LIST_SEPARATOR.fullmatch(source[end:start])
                           for (_, end), (start, _) in zip(wording, wording[1:])):
            quote = raw_source[wording[0][0]:wording[-1][1]]
            result["grounds_ocr"] = Fact(value=quote, quote=quote)
        return result
    if listed:
        return result
    # A literal reference can establish the wording without a supported category.
    references = {}
    for match in OCR_CONTRACT_REFERENCE.finditer(operative):
        prefix = operative[max(0, match.start() - 90):match.start()]
        if re.search(r"залог|поручител|уступк", prefix, re.I):
            continue
        references.setdefault(compact(match[0]), match[0])
    for match in re.finditer(
        rf"\b(?:основанн\w*\s+на|установленн\w*)\s+судебн\w*\s+приказ\w*{BASIS_REFERENCE}",
        operative, re.I,
    ):
        references.setdefault(compact(match[0]), match[0])
    for match in re.finditer(r"\b(?:недоимк\w*|задолженност\w*)\s+по\s+исполнительск\w*\s+сбор\w*",
                             operative, re.I):
        references.setdefault(compact(match[0]), match[0])
    if len(references) == 1:
        quote = next(iter(references.values()))
        return {"grounds_ocr": Fact(value=quote, quote=quote)}
    return {}


def operative_debtor_inn(operative: str) -> req.Requisite:
    """Read a unique INN from an explicitly named debtor's own operative record."""
    role = (
        r"(?i:\b(?:реестр\w*\s+требован\w*(?:\s+(?:кредитор\w*|кредито-\s*ров))?\s+"
        r"(?:должник\w*|граждан\w*)?|должник\w*\s+(?:граждан\w*)?)"
        r"\s*[-–—,:]?\s*)"
    )
    name = rf"{req.WORD}(?:\s+{req.WORD}){{2,5}}"
    records = {}
    for match in re.finditer(role + name + r"\s*\([^)]{0,650}\)", operative):
        record = match[0]
        if re.search(r"поручител|управляющ|супруг|созаемщик|\b(?:ООО|ПАО|АО|ИП)\b", record, re.I):
            continue
        numbers = req.INN.findall(record)
        if len(numbers) == 1 and len(numbers[0]) == 12:
            records[numbers[0]] = record
    if len(records) == 1:
        number, quote = next(iter(records.items()))
        return req.Requisite(number, quote)
    return req.Requisite(None)


def component_kind(quote: str) -> str | None:
    if re.search(r"неустойк|\bпен(?:и|я|ей|ею|ей|ю)\b|штраф|санкц|мораторн", quote, re.I):
        return "sanction"
    if re.search(r"(?:гос[-–]?|государственн\w*\s+)?пошлин|судебн\w*\s+расход", quote, re.I):
        if previous_cost(quote):
            return "prior_cost"
        if re.search(r"пошлин", quote, re.I) and (CURRENT_DUTY.search(quote) or INLINE_SEPARATE_PAYMENT.search(quote)):
            return "duty"
        return None
    if re.search(r"процент", quote, re.I):
        return "interest"
    if re.search(r"основн\w+\s+(?:долг|дола\b|задолжен|сумм|требован)|ссудн\w*\s+задолжен|"
                 r"\bнедоимк\w*|задолжен\w*\s+по\s+(?:зарплат|заработн|налог)|"
                 rf"выходн\w*\s+пособи|страхов\w+\s+взнос\w*|\bдолг[ау]?\b|\b{TAX_KIND}\b", quote, re.I):
        return "principal"
    return None


def pledge_candidate(quote: str) -> Fact | None:
    if not COLLATERAL.search(quote):
        return None
    result = {}
    for clause in re.split(r";|(?<=\.)\s+(?=[А-ЯЁ])", quote):
        if not COLLATERAL.search(clause):
            continue
        if re.search(r"\b(?:выделить|отложить)\b.{0,180}\b(?:вопрос|рассмотрени|производств)\w*", clause, re.I):
            label = "separated"
        elif re.search(r"\bв\s+признании\b.{0,500}\bотказать\b|\bотказать\b.{0,400}\bв\s+признании\b|"
                       r"\bв\s+удовлетворении\s+(?:требован|заявлени)\w*\s+о\s+признании\b.{0,250}\bотказать\b|"
                       r"\b(?:как\s+)?не\s+обеспеченн\w*\s+залог", clause, re.I):
            label = "no"
        elif re.search(r"(?:\bкак\s+|\bв\s+качестве\s+|,\s*|\bустановить\s+(?:е[её]|его|требован\w*)\s+)"
                       r"(?:требован\w*\s*,?\s*)?обеспеченн\w*\s+залог", clause, re.I) and not re.search(
                r"отказ|прекрат|отлож|выдел|не\s+обеспеч", clause, re.I):
            label = "yes"
        else:
            continue
        result[label] = clause.strip()
    if len(result) != 1:
        return None
    label, evidence = next(iter(result.items()))
    return Fact(value=label, quote=evidence)


def _component_quote(text: str, match: re.Match, lower: int, upper: int, *, amount_first: bool) -> str:
    before = text[lower:match.start()]
    preceding = re.split(r";|,(?![0-9])|(?<!\d)\.(?!\d)", before)[-1]
    if not amount_first:
        # The qualifier after the comma belongs to the same penalty label.
        penalty = re.search(
            r"\b(?:пени|неустойк\w*)\s+[^,;.]{1,140},\s*начисленн\w*\s+на\s+[^,;.]{1,140}[-–—]\s*$",
            before, re.I,
        )
        if penalty:
            preceding = penalty[0]
    tail = text[match.end():upper]
    # An extra period can follow an amount with no currency before its dash label.
    decimal_period = "." if re.match(r"\.\s*[-–—]\s*", tail) and re.fullmatch(
        r"[0-9 ]+[.,] ?[0-9]{1,2}", match[0],
    ) else ""
    following = decimal_period + re.split(
        r";|,(?![0-9])|(?<!\d)\.(?!\d)|\)", tail[len(decimal_period):], maxsplit=1,
    )[0]
    # A parenthesis can carry the actual kind: 100 руб. (основной долг).
    following = re.split(
        r"\b(?:в том числе|из которых|из них|для удовлетворения|требован\w*\s+в\s+части|"
        r"и\s+отдельно|отдельно\s+в\s+третью|с\s+уч[её]том\s+применения)\b",
        following, maxsplit=1, flags=re.I,
    )[0]
    fragment = following if amount_first else preceding
    if amount_first and re.search(r"пошлин|судебн\w*\s+расход", fragment, re.I):
        # A comma may separate the old court-cost label from its provenance.
        # Extend only an explicit past recovery clause, before any next amount.
        remainder = text[match.end() + len(fragment):upper]
        provenance = re.match(
            r",\s*взысканн\w*\s+(?:заочн\w*\s+)?решени\w*\s+[^;]{0,180}?\bсуда\b",
            remainder, re.I,
        )
        if provenance:
            fragment += provenance[0]
        elif re.search(r"пошлин", fragment, re.I):
            repayment = re.match(
                r",\s*в\s+порядке\s+(?:положений\s+)?(?:пункт\w*\s*3\s*стать\w*|п\.\s*3\s*ст\.)\s*137\b",
                remainder, re.I,
            )
            repayment = repayment or INLINE_SEPARATE_PAYMENT.match(remainder)
            if repayment:
                fragment += repayment[0]
    return (match[0] + fragment if amount_first else fragment + match[0]).strip()


def _component_at(text: str, match: re.Match, lower: int, upper: int, *, amount_first: bool) -> Component | None:
    quote = _component_quote(text, match, lower, upper, amount_first=amount_first)
    kind = component_kind(quote)
    if kind is None and amount_first:
        award = INLINE_DUTY.search(text, lower, upper) or INLINE_ACCOUNTED_DUTY.search(text, lower, upper)
        if award and award.start("amount") == match.start():
            quote, kind = award[0], "duty"
    if kind and not excluded_component(quote, text):
        return Component(kind=kind, amount=match[0].strip(), quote=quote)
    return None


def _itemized_admission_with_commission(quote: str) -> tuple[Decimal, Components] | None:
    """Read an exhaustive contract list while leaving the commission uncategorized."""
    if not re.search(r"\bкомисси[яию]\b", quote, re.I) or not CONTRACT_BASIS.search(quote):
        return None
    parts = list(AMOUNTS.finditer(quote))
    values = [money(part[0]) for part in parts]
    if not parts or None in values:
        return None
    commissions, sanctions = 0, []
    for index, part in enumerate(parts):
        lower = parts[index - 1].end() if index else 0
        upper = parts[index + 1].start() if index + 1 < len(parts) else len(quote)
        item = _component_at(quote, part, lower, upper, amount_first=True)
        if item and item.kind in {"principal", "interest", "sanction"}:
            if item.kind == "sanction":
                sanctions.append(item)
        elif re.fullmatch(
                rf"{AMOUNT}\s*[-–—]\s*комисси[яию]",
                _component_quote(quote, part, lower, upper, amount_first=True), re.I):
            commissions += 1
        else:
            return None
    if commissions != 1 or len(parts) < 2:
        return None
    return sum(values, Decimal(0)), Components(items=sanctions, complete=True, quote=quote)


def claim_amounts(
    quote: str, fee: Component | None, *, embedded_cost: Component | None = None,
) -> tuple[dict[str, Components], CourtTotal | None]:
    notice = re.search(
        r"(?<=\.)\s+Требован\w*\s+(?:уполномоченного\s+органа|кредитора)\s+по\s+взысканию\s+"
        r"пен\w*\s+в\s+размере\s+(?P<amount>" + AMOUNT + r")\s+учитыва\w*\s+отдельно\b",
        quote, re.I,
    )
    if notice:
        preceding = quote[:notice.start()].strip()
        amounts = list(AMOUNTS.finditer(preceding))
        if any(money(m[0]) == money(notice["amount"]) and re.match(
                r"\s*[-–—]?\s*пен[ияе]\w*\b", preceding[m.end():], re.I) for m in amounts):
            quote = preceding
    nested = nested_contract_amounts(quote, fee)
    if nested is not None:
        return nested
    matches = [m for m in AMOUNTS.finditer(quote) if money(m[0]) is not None]
    if not matches:
        return {}, None
    split = BREAKDOWN.search(quote)
    gross = [m for m in matches if GROSS.search(quote[max(0, m.start() - 38):m.start()])]
    if split:
        outer = [m for m in matches if m.end() <= split.start()]
        if len(outer) == 1:
            gross = outer
        elif len(outer) == 2 and outer == gross and money(outer[0][0]) == money(outer[1][0]):
            if len(queue_candidates(quote[outer[0].end():outer[1].start()])) == 1:
                gross = outer[:1]
    gross = gross[0] if len(gross) == 1 else None
    naked = re.search(r"(?:в\s+размере|в\s+сумме)\s+([0-9 ]+(?:[,.] ?\d{1,2})?)\s*[, (]*$",
                      quote[:split.start()], re.I) if split and not gross else None
    # A lone sum explicitly tied to a kind is both a total and a component.
    separate_gross = gross is not None and len(matches) > 1 and (
        bool(split and gross.end() <= split.start())
        or quote[gross.end():matches[1].start()].lstrip().startswith("(")
    )
    parts = [m for m in matches if not (separate_gross and m is gross)]
    # A total may be repeated under the only queue immediately before the
    # itemized breakdown. Do not count the identical subtotal twice.
    if separate_gross and len(parts) > 1 and money(gross[0]) == money(parts[0][0]) and (
            GROSS.search(quote[max(0, parts[0].start() - 38):parts[0].start()])
            and BREAKDOWN.search(quote[parts[0].end():parts[1].start()])):
        parts = parts[1:]
    cost_start = quote.find(embedded_cost.quote) if embedded_cost else -1
    cost_end = cost_start + len(embedded_cost.quote) if cost_start >= 0 else -1
    alternatives = []
    for amount_first in (True, False):
        found = []
        for i, match in enumerate(parts):
            lower = parts[i - 1].end() if i else (split.end() if split and split.end() < match.start() else 0)
            upper = parts[i + 1].start() if i + 1 < len(parts) else len(quote)
            if cost_start >= 0 and cost_start <= match.start() and match.end() <= cost_end:
                piece = embedded_cost
            else:
                piece = _component_at(quote, match, lower, upper, amount_first=amount_first)
            if i == 0 and amount_first and piece is None and re.match(
                    r"\s+задолжен\w*\s*[, ]*(?:и\s+)?отдельно\b", quote[match.end():upper], re.I):
                label = re.match(r"\s+задолжен\w*", quote[match.end():upper], re.I)[0]
                piece = Component(kind="principal", amount=match[0].strip(), quote=match[0] + label)
            found.append(piece)
        alternatives.append(found)
    primary, secondary = alternatives
    # A consistent amount-first/label-first list is usually preferable to mixing
    # associations across adjacent amounts. Use both only when they agree or are disjoint.
    if primary and primary[0] is not None and secondary[0] is None:
        chosen = primary
    elif secondary and secondary[0] is not None and primary[0] is None:
        chosen = secondary
    elif len(parts) > 1 and sum(p is not None for p in primary) != sum(p is not None for p in secondary):
        chosen = max(alternatives, key=lambda row: sum(p is not None for p in row))
    else:
        chosen = [a if b is None or a and a.kind == b.kind else b if a is None else None
                  for a, b in zip(primary, secondary)]
    components = [piece for piece in chosen if piece is not None]
    stated = (gross[0] if separate_gross or gross and len(matches) == 1 else
              naked[1] if naked else matches[0][0] if len(matches) == 1 else None)
    same_fee = bool(fee and any(piece.kind == "duty" and money(piece.amount) == money(fee.amount)
                                for piece in components))
    total = CourtTotal(amount=stated.strip(), scope="without_duty" if fee and not same_fee else "unknown",
                       quote=stated.strip()) if stated else None
    declared_total = money(stated)
    covered = bool(parts and all(chosen))
    actual = sum((money(piece.amount) for piece in components), Decimal(0))
    if covered and stated:
        current = sum((money(item.amount) for item in components if item.kind == "duty"), Decimal(0))
        # A fully labelled admission with a separately awarded fee can expose
        # each printed component. The ledger flags any disagreement with the total.
        separately_priced = fee and not same_fee and separate_gross and not re.search(
            r"пошлин|судебн\w*\s+расход", quote, re.I,
        )
        covered = declared_total in {actual, actual - current} or separately_priced
    if fee and not same_fee:
        components.append(fee)
    if not covered:
        # A missed token can change any component sum. Preserve explicitly
        # separate current duty only if nothing else refers to duty in this row.
        partial = ({"pau_only_amount": Components(items=[fee], complete=True, quote=fee.quote)}
                   if fee and not re.search(r"пошлин", quote, re.I) else {})
        if (not fee and split and total and len(parts) == 2 and declared_total == sum(money(p[0]) for p in parts)
                and re.fullmatch(r"\s*[-–—]\s*задолженност\w*\s*;\s*", quote[parts[0].end():parts[1].start()], re.I)
                and re.match(r"\s*[-–—]\s*расход\w*\s+по\s+уплате\s+государственн\w*\s+пошлин",
                             quote[parts[1].end():], re.I)):
            total.scope = "with_duty"
        if (split and declared_total is not None and len(parts) == len(components) == 1
                and components[0].kind == "sanction"
                and declared_total > money(components[0].amount) and re.fullmatch(
                    r"\s*штрафн\w*\s+санкци\w*\s+в\s+размере\s*", quote[split.end():parts[0].start()], re.I,
                )):
            # The court stated the sanctions subtotal even though it did not
            # itemize the rest of the admission.
            partial["financial_sanctions"] = Components(items=components, complete=True, quote=quote)
        # Ambiguous old/current costs affect principal and duty, not a complete
        # independently labelled sanctions subtotal. Require a balanced explicit
        # breakdown and one consistent direction; never guess an unlabeled sum.
        direction = True if chosen is primary else False if chosen is secondary else None
        if direction is None and split and len(parts) == 1 and not components:
            part = parts[0]
            for side in (True, False):
                label = _component_quote(quote, part, split.end(), len(quote), amount_first=side)
                if re.search(r"пошлин|судебн\w*\s+расход", label, re.I):
                    direction = side
                    break
        if direction is not None and split and declared_total is not None:
            unknown = []
            for i, (part, component) in enumerate(zip(parts, chosen)):
                if component is not None:
                    continue
                lower = parts[i - 1].end() if i else split.end()
                upper = parts[i + 1].start() if i + 1 < len(parts) else len(quote)
                label = _component_quote(quote, part, lower, upper, amount_first=direction)
                if not re.search(r"пошлин|судебн\w*\s+расход", label, re.I) or excluded_component(label, quote):
                    break
                unknown.append((i, lower, part))
            else:
                listed_total = sum((money(part[0]) for part in parts), Decimal(0))
                with_costs = declared_total == listed_total
                separate_cost = (len(unknown) == 1 and unknown[0][0] == len(parts) - 1 and declared_total == actual
                                 and re.search(r"\bа\s+также\s*$", quote[unknown[0][1]:unknown[0][2].start()], re.I))
                if unknown and (with_costs or separate_cost):
                    partial["financial_sanctions"] = Components(
                        items=[c for c in components if c.kind == "sanction"], complete=True, quote=quote,
                    )
                    if with_costs and total and fee is None:
                        # The exact sum includes the costs regardless of their origin.
                        total.scope = "with_duty"
        return partial, total
    return {name: Components(items=[c for c in components if c.kind in kinds], complete=True, quote=quote)
            for name, kinds in CATEGORY_FIELDS.items()}, total


def contract_sections(quote: str) -> tuple[str, list[str]] | None:
    """Find the header and literal sections of an itemized list of contracts."""
    listed_contracts = re.search(r"\bкредитн\w*\s+договор\w*\s*:\s*(?=[-–—]\s+№)", quote, re.I)
    inline_contracts = False
    if listed_contracts:
        pattern = r"(?:^|\s)[-–—]\s+№\s*[\w./-]+\s+от\s+\d{1,2}\.\d{1,2}\.\d{4}"
    else:
        pattern = (r"(?:[-–—]\s+|(?:в\s+том\s+числе\s*:|из\s+(?:которых|них)\s*:|;)\s*)по\s+"
                   r"(?:кредитн\w*\s+договор\w*|договор\w*\s+(?:потребительск\w*\s+)?(?:микро)?займ\w*)")
    starts = list(re.finditer(pattern, quote, re.I))
    if not starts:
        starts = list(re.finditer(
            r"(?:\bв\s+том\s+числе\s*:\s*|(?<=;)\s*)"
            r"(?P<numbered>\d+\.\d+\.\s+требован\w*\s+по\s+кредитн\w*\s+договор\w*)", quote, re.I,
        ))
    if not starts:
        starts = list(AGREEMENT_LIST_ITEM.finditer(quote))
        inline_contracts = len(starts) >= 2
    if not starts:
        starts = list(re.finditer(r"\bв\s+рамках\s+кредитн\w*\s+договор\w*\s*№\s*[\w./-]+", quote, re.I))
        inline_contracts = len(starts) >= 2 and all(quote[:m.start()].rstrip().endswith(";") for m in starts[1:])
    if len(starts) < 2:
        return None
    heading = quote[:starts[0].start()]
    if inline_contracts and not (CREDITOR.search(heading) and len(queue_candidates(heading)) == 1):
        return None
    gross = list(AMOUNTS.finditer(heading))
    if len(gross) > 1 or (not gross and not (listed_contracts or inline_contracts)):
        return None
    boundaries = [*(m.start("numbered") if "numbered" in m.re.groupindex else m.start() for m in starts), len(quote)]
    parts = [quote[start:end].strip(" ;,") for start, end in zip(boundaries, boundaries[1:])]
    parts[0] = re.sub(r"^(?:в\s+том\s+числе|из\s+(?:которых|них))\s*:\s*", "", parts[0], flags=re.I)
    return heading, parts


def nested_contract_amounts(quote: str, fee: Component | None):
    """Aggregate explicit subtotals for reconciliation, without defining the output layout."""
    sections = contract_sections(quote)
    if not sections:
        return None
    heading, parts = sections
    gross = list(AMOUNTS.finditer(heading))
    grouped = {name: [] for name in CATEGORY_FIELDS}
    subtotal = Decimal(0)
    all_with_costs = True
    for part in parts:
        fields, total = claim_amounts(part, None)
        printed = money(total.amount) if total else None
        if printed is None:
            return None
        subtotal += printed
        all_with_costs &= total.scope == "with_duty"
        grouped = {name: items + fields[name].items for name, items in grouped.items() if name in fields}
    if gross and subtotal != money(gross[0][0]):
        return None
    if not grouped:
        return None
    if fee and "pau_only_amount" in grouped:
        grouped["pau_only_amount"].append(fee)
    scope = "with_duty" if all_with_costs and not fee else "without_duty"
    return (
        {name: Components(items=items, complete=True, quote=quote) for name, items in grouped.items()},
        CourtTotal(amount=gross[0][0].strip(), scope=scope, quote=gross[0][0].strip()) if gross else None,
    )


def _queue_allocation(admission: str, allocation: str) -> Claim | None:
    """Connect a named admission to its immediately following same-queue breakdown."""
    heading = re.match(r"установить следующую очередность удовлетворения требований(?: кредитора)?:", allocation, re.I)
    total = list(AMOUNTS.finditer(admission))
    if not heading or len(total) != 1 or queue_candidates(admission) or not CREDITOR.search(admission):
        return None
    breakdown = allocation[heading.end():]
    amounts = list(AMOUNTS.finditer(breakdown))
    if len(amounts) != 2 or re.search(r"пошлин|судебн\w*\s+расход|залог", breakdown, re.I):
        return None
    published_total = money(total[0][0])
    parts = [money(amount[0]) for amount in amounts]
    if published_total is None or None in parts or published_total != sum(parts):
        return None
    principal = breakdown[amounts[0].start():amounts[1].start()].strip()
    sanction = breakdown[amounts[1].start():].strip()
    queues = queue_candidates(principal)
    if (len(queues) != 1 or [q.value for q in queues] != [q.value for q in queue_candidates(sanction)]
            or SEPARATE_SANCTIONS.search(principal) or not SEPARATE_SANCTIONS.search(sanction)):
        return None
    quote = admission + " " + allocation
    fields = {
        "priority_queue": queues[0],
        "principal_debt": Components(items=[Component(kind="principal", amount=amounts[0][0], quote=principal)],
                                     complete=True, quote=quote),
        "financial_sanctions": Components(items=[Component(kind="sanction", amount=amounts[1][0], quote=sanction)],
                                         complete=True, quote=quote),
        "pau_only_amount": Components(items=[], complete=True, quote=quote),
        "is_salary_or_severance": Salary(value=False, quote=quote),
    }
    return Claim(quote, fields, CourtTotal(amount=total[0][0], scope="without_duty", quote=total[0][0]))


def _separate_fee(block: str) -> Component | None:
    amounts = list(AMOUNTS.finditer(block))
    if not amounts or len({money(m[0]) for m in amounts}) != 1:
        return None
    # Boilerplate can expressly say the fee is NOT a current payment.
    decision = re.split(r"\bРазъяснить\b", block, maxsplit=1, flags=re.I)[0].strip()
    if re.search(r"пошлин", decision, re.I) and not previous_cost(decision) and not excluded_component(decision, decision):
        explicit_recovery = re.match(r"взыскать\b", block, re.I)
        explicit_current = CURRENT_DUTY.search(block)
        if component_kind(block) == "duty" or explicit_recovery or explicit_current or STANDALONE_FEE_ACCOUNTING.search(block):
            return Component(kind="duty", amount=amounts[0][0].strip(), quote=decision)
    return None


def split_queue_items(quote: str) -> list[tuple[str, list[Fact]]]:
    divider = BREAKDOWN.search(quote)
    if not divider:
        return []
    introduction = quote[:divider.start()]
    parent_queue = queue_candidates(introduction)
    printed = list(AMOUNTS.finditer(introduction))
    if len(parent_queue) > 1 or len(printed) != 1:
        return []
    body = quote[divider.end():]
    # Court bullets start with a dash before a queue, an amount or a kind.
    # A dash between the amount and its label is part of the same bullet.
    starts = list(re.finditer(
        r"(?:^|\s)(?<!\w)[–—-]\s+(?=(?:[0-9][0-9 ]*[,.]|"
        r"в[о]?\s+(?:перв|втор|трет|четв)|задолжен\w*\s+(?:в\s+сумме|по\s+пен)))", body, re.I,
    ))
    if len(starts) < 2 or body[:starts[0].start()].strip():
        return []
    rows, totals = [], []
    for index, start in enumerate(starts):
        part = body[start.end():starts[index + 1].start() if index + 1 < len(starts) else len(body)].strip()
        part = re.split(r"\bтребован\w*\s+в\s+части\s+(?:пен|санкц|неустойк)", part, maxsplit=1, flags=re.I)[0].strip()
        queues = queue_candidates(part) or parent_queue
        values = [money(m[0]) for m in AMOUNTS.finditer(part)]
        if len(queues) != 1 or not values:
            return []
        if len(values) == 2 and values[0] == values[1] and BREAKDOWN.search(part):
            values.pop()
        if len(values) != 1 or values[0] is None:
            return []
        rows.append((part, queues))
        totals.append(values[0])
    return rows if sum(totals, Decimal(0)) == money(printed[0][0]) else []


def _is_final_unitemized_total(
    quote: str, total: CourtTotal, sanctions: Components | None, queue: str, *, refunded_duty: bool,
) -> bool:
    """Recognize the award without assigning unknown amounts to individual components."""
    if re.search(r"пошлин|судебн\w*\s+расход", quote, re.I):
        return False
    admission, *parts = AMOUNTS.finditer(quote)
    prefix = quote[:admission.start()]
    if money(admission[0]) != money(total.amount) or not GROSS.search(prefix):
        return False
    if not parts:
        suffix = quote[admission.end():]
        return bool(
            re.match(r"признать\s+(?:обоснованн|установленн)\w*\s+и\s+"
                     r"(?:включить|подлежащ\w*\s+включению)\b", prefix, re.I)
            or (queue == "За реестром" and re.search(
                r"\bпризнать\s+обоснованным\s+в\s+размере\s*$", prefix, re.I,
            ))
            or (queue == "За реестром" and re.match(r"признать\s+требован\w*\b", prefix, re.I)
                and re.match(r"\s+обоснованн\w*\s+и\s+подлежащ\w*\s+удовлетворению\b", suffix, re.I))
            or (re.match(r"включить\s+требован\w*\b", prefix, re.I)
                and re.match(r"\s+(?:задолжен\w*|неисполненн\w*\s+обязательств\w*)\s+"
                             r"в\s+\w+\s+очередь\s+реестра\b", suffix, re.I))
            or (re.match(r"включить\s+требован\w*\b", prefix, re.I)
                and queue_candidates(prefix) and not suffix.strip(" ."))
            or (re.search(r"задолжен\w*\s+в\s+размере\s*$", prefix, re.I)
                and re.match(r"\s*[-–—]\s+(?:в|во)\s+\w+\s+очередь\b", suffix, re.I))
        )
    return (
        refunded_duty and len(parts) == 1 and sanctions is not None and len(sanctions.items) == 1
        and money(parts[0][0]) == money(sanctions.items[0].amount)
        and bool(BREAKDOWN.match(quote[admission.end():parts[0].start()].lstrip(", ")))
    )


def _loan_with_separately_recognized_unclassified_fee(quote: str) -> list[Claim]:
    fee = re.search(
        rf"\bпо\s+оплате\s+госпошлин\w*\s+в\s+размере\s+(?P<amount>{AMOUNT}),\s*"
        r"(?=по\s+договору\s+(?:микро)?займа\b)", quote, re.I,
    )
    if not fee or CURRENT_DUTY.search(quote) or previous_cost(fee[0]) or COLLATERAL.search(quote):
        return []
    loan = quote[fee.end():]
    queues = queue_candidates(loan)
    if ([queue.value for queue in queues] != ["За реестром"]
            or not re.search(r"\bобоснованн\w*\s+и\s+подлежащ\w*\s+удовлетворению\b", loan, re.I)):
        return []
    numbers = req.contract_numbers(loan)
    fields, total = claim_amounts(loan, None)
    if len(numbers) != 1 or not total or not all(name in fields for name in CATEGORY_FIELDS):
        return []
    fields["priority_queue"] = queues[0]
    fields["is_salary_or_severance"] = Salary(value=False, quote=loan)
    fee_quote = fee[0].rstrip(", ")
    return [
        Claim(loan, fields, total, next(iter(numbers.values()))),
        Claim(fee_quote, {
            "priority_queue": queues[0],
            "financial_sanctions": Components(items=[], complete=True, quote=fee_quote),
            "is_salary_or_severance": Salary(value=False, quote=fee_quote),
        }, CourtTotal(amount=fee["amount"], scope="with_duty", quote=fee["amount"])),
    ]


def claim_candidates(operative: str, creditor: Fact | None) -> list[Claim]:
    if re.match(r"Утвердить\s+локальн\w*\s+план\s+реструктуризации\b", operative, re.I):
        # Quoted repayment-plan terms are not registry admissions. A separate
        # termination of the plan's inclusion application followed by a new
        # admission is an explicit boundary back to the court's own orders.
        boundary = re.search(
            r"(?<=\.)\s+Производство\s+по\s+заявлению\b.{0,450}?"
            r"о\s+включении\s+в\s+реестр\b.{0,450}?[-–—]\s*прекратить\.\s*(?=Включить\b)",
            operative, re.I,
        )
        if not boundary:
            return []
        operative = operative[boundary.end():]
    confirmation = re.match(
        rf"1\.\s*требован\w*\s+(?P<name>{LEGAL_NAME})\s+в\s+(?:размере|сумме)\s+(?P<amount>{AMOUNT})"
        r"\s+признать\s+обоснованн\w*\.\s+(?=2\.\s*включить\s+в\s+третий\s+раздел\s+реестра)",
        operative, re.I,
    )
    if confirmation:
        following = operative[confirmation.end():]
        payee, amount = CREDITOR.search(following), AMOUNTS.search(following)
        if (not payee or not amount or req.identity(payee["name"]) != req.identity(confirmation["name"])
                or money(amount[0]) != money(confirmation["amount"])):
            return []
        operative = following
    starts = list(COMMAND.finditer(operative))
    if not starts or operative[:starts[0].start()].strip(" \n.0123456789)"):
        return []
    blocks = [operative[m.start():(starts[i + 1].start() if i + 1 < len(starts) else len(operative))].strip()
              for i, m in enumerate(starts)]
    admitted, fees, separate_pledges, unknown_fees = [], [], [], []
    fee_queues = {}
    allocated = None
    collateral_confirmation = None
    restoration_refusal = None
    registry_refusal = False
    refunded_duty = False
    for match, block in zip(starts, blocks):
        command = compact(match["verb"])
        if registry_refusal:
            if (command != "признать" or not re.match(
                    r"признать\s+данные\s+требования\s+подлежащими\s+удовлетворению\b", block, re.I)
                    or [queue.value for queue in queue_candidates(block)] != ["За реестром"]
                    or AMOUNTS.search(block)):
                return []
            start = operative.index(admitted[0])
            end = operative.index(block, start + len(admitted[0])) + len(block)
            admitted[0] = operative[start:end]
            registry_refusal = False
            continue
        accounting = FEE_ACCOUNTING.search(block)
        if accounting and command in {"включить", "признать"}:
            fee_quote = accounting[0].strip()
            if money(accounting["amount"]) is not None and not excluded_component(fee_quote, block):
                fees.append(Component(kind="duty", amount=accounting["amount"], quote=fee_quote))
                block = block[:accounting.start()].strip()
        if command in {"включить", "признать"}:
            agreements = list(AGREEMENT_LIST_ITEM.finditer(block))
            if len(agreements) >= 2 and (expense := AGREEMENT_LIST_FEE.search(block, agreements[-1].end())):
                fees.append(Component(kind="duty", amount=expense["amount"], quote=expense[0].strip()))
                block = block[:expense.start()].strip()
        if command == "включить" and re.match(r"\s*(?:2\.\s*)?включить\s+в\s+третий\s+раздел\s+реестра", block, re.I):
            section_fee = SECTION_ACCOUNTED_DUTY.search(block)
            if section_fee and len(list(AMOUNTS.finditer(block))) == 2:
                fees.append(Component(kind="duty", amount=section_fee["amount"], quote=section_fee[0]))
                block = block[:section_fee.start()].rstrip(", ")
        embedded = re.search(
            r"(?<=[.;])\s+(?:Судебные\s+расходы\s+(?:" + LEGAL_NAME + r"\s+)?"
            r"по\s+уплате\s+государственной\s+пошлины|Государственная\s+пошлина)\b", block,
        )
        if embedded:
            # Preserve the exact original fee clause including its repayment order.
            fee_quote = block[embedded.start():].strip()
            fee = _separate_fee(fee_quote)
            if fee:
                fees.append(fee)
                block = block[:embedded.start()].strip()
        if command in {"признать", "включить", "взыскать", "учитывать"} and (fee := _separate_fee(block)):
            fees.append(fee)
            continue
        if command == "взыскать" and not AMOUNTS.search(block) and re.search(
                r"пошлин\w*\s+за\s+подачу\s+заявлени\w*\s+о\s+рассмотрении\s+требован\w*\s+о\s+включении",
                block, re.I):
            # Missing OCR fee amount does not invalidate an independently itemized
            # admitted debt. Keep the fee unknown, never substitute zero.
            unknown_fees.append(block)
            continue
        if command == "отказать" and re.search(
                r"\bв\s+удовлетворении\s+(?:заявлени|требован)\w*.{0,480}\b(?:о\s+признании|в\s+части\s+признания)\b"
                r".{0,520}?\bзалог\w*", block, re.I):
            separate_pledges.append(Fact(value="no", quote=block))
            continue
        if command in {"учесть", "учитывать"} and not AMOUNTS.search(block) and re.search(
                r"отдельно|санкц|неустойк|пошлин|после погашения", block, re.I):
            if len(fees) == 1 and re.match(
                r"уч(?:есть|итывать)\s+требован\w*\s+в\s+части[^.;]{0,100}?"
                r"(?:государственн\w*\s+пошлин\w*|госпошлин\w*)\s+в\s+составе",
                block, re.I,
            ):
                fee_queues.update((queue.value, queue) for queue in queue_candidates(block))
            continue
        if command in {"учесть", "учитывать"} and len(admitted) == 1 and re.search(
                r"неустойк\w*.{0,100}\bотдельно\b.{0,200}\bпосле\s+погашения\b", block, re.I
        ) and not re.search(r"пошлин|залог", block, re.I) and not CREDITOR.search(block):
            repeated = list(AMOUNTS.finditer(block))
            if len(repeated) == 1:
                known, _ = claim_amounts(admitted[0], None)
                sanctions = known.get("financial_sanctions")
                if sanctions and sanctions.items and money(repeated[0][0]) == sum(
                        money(item.amount) for item in sanctions.items):
                    continue
        if command == "установить" and len(admitted) == 1 and allocated is None:
            allocated = _queue_allocation(admitted[0], block)
            if allocated:
                continue
        if command in {"признать", "установить"} and len(admitted) == 1 and collateral_confirmation is None and creditor:
            pledge = pledge_candidate(block)
            named = CREDITOR.search(block)
            amounts = list(AMOUNTS.finditer(block))
            prior_amounts = {money(m[0]) for m in AMOUNTS.finditer(admitted[0])}
            same_amount = (len(amounts) == 1 and prior_amounts == {money(amounts[0][0])}
                           and None not in prior_amounts and re.search(r"\bпо\s+договор\w*\b", block, re.I))
            confirmation_contracts = CREDIT_CONTRACT_NUMBER.findall(block)
            same_contract = (not amounts and len(confirmation_contracts) == 1
                             and confirmation_contracts == CREDIT_CONTRACT_NUMBER.findall(admitted[0]))
            if (pledge and pledge.value == "yes" and named and (same_amount or same_contract)
                    and req.identity(creditor.value) == req.identity(named["name"])
                    and not queue_candidates(block)
                    and not COLLATERAL.search(admitted[0])):
                collateral_confirmation = pledge
                continue
        if command == "признать" and not AMOUNTS.search(block) and admitted and re.search(r"пошлин", block, re.I):
            # An unreadable fee amount is unresolved, not an absent/zero fee.
            unknown_fees.append(block)
            continue
        if command == "признать" and not AMOUNTS.search(block) and admitted and re.search(
                r"(?:пункт\w*\s*3\s*стать\w*|п\.\s*3\s*ст\.)\s*137", block, re.I):
            continue
        if command == "включить" and len(fees) == 1 and re.search(
                r"в\s+порядке\s+(?:части|пункта)\s+3\s+статьи\s+137", block, re.I):
            amounts = list(AMOUNTS.finditer(block))
            repeated_creditor = CREDITOR.search(block)
            if (len(amounts) == 1 and money(amounts[0][0]) == money(fees[0].amount)
                    and not queue_candidates(block) and creditor and repeated_creditor
                    and req.creditor_value(creditor.value, repeated_creditor['name'], operative).value):
                # This repeats inclusion of the separately awarded duty, not
                # another debt row. Its wording remains available to JEV.
                continue
        if command in {"включить", "признать", "учесть", "учитывать", "установить"} and (
                re.search(r"включ|реестр|обоснован", block, re.I) or queue_candidates(block)):
            if fee := _separate_fee(block):
                fees.append(fee)
            elif command == "включить" and not AMOUNTS.search(block) and admitted and (
                    not queue_candidates(admitted[-1]) and queue_candidates(block)):
                admitted[-1] = operative[operative.index(admitted[-1]):operative.index(block) + len(block)]
            elif AMOUNTS.search(block) or command == "включить":
                admitted.append(block)
            elif command == "признать" and re.search(r"обоснован", block, re.I):
                # The next operative sentence may state the actual queue and amount.
                continue
            else:
                return []
        elif command in {"взыскать", "учитывать"}:
            if fee := _separate_fee(block):
                fees.append(fee)
            else:
                return []
        elif command in {"выделить", "отложить", "отказать"} and (pledge := pledge_candidate(block)):
            separate_pledges.append(pledge)
        elif command in {"удовлетворить", "отказать"} and re.search(
                r"восстановлен|восстановить|пропущенн\w*\s+срок", block, re.I):
            if command == "отказать":
                restoration_refusal = block
            continue
        elif command == "отказать" and len(admitted) == 1 and not queue_candidates(admitted[0]) and re.match(
                r"отказать\s+во\s+включении\s+данных\s+требований\s+в\s+реестр\b", block, re.I):
            registry_refusal = True
            continue
        elif command == "удовлетворить" and not AMOUNTS.search(block):
            continue
        elif command == "отказать" and re.match(r"в\s+остальной\s+части", block, re.I):
            continue
        elif command == "отказать" and re.search(r"о\s+снижении\s+(?:размера\s+)?неустойки", block, re.I):
            continue
        elif command == "прекратить" and re.search(
                r"в\s+остальной\s+части.{0,160}текущ\w*\s+характер|"
                r"^производство\s+по\s+заявлению\b.{0,500}\bв\s+части\s+включения\b"
                r".{0,350}\bгосударственной\s+пошлины\b.{0,85}\bпрекратить\b", block, re.I):
            # An explicit exclusion of current costs does not remove the
            # independently admitted principal claim in an earlier paragraph.
            continue
        elif command in {"вернуть", "возвратить"} and re.search(r"пошлин", block, re.I):
            refunded_duty = True
            continue
        elif command == "учесть" and re.search(r"отдельно.*основн", block, re.I) and not AMOUNTS.search(block):
            continue
        else:
            return []
    if registry_refusal or not admitted or len(admitted) > 12 or (len(admitted) != 1 and (fees or unknown_fees)):
        return []
    if allocated:
        return [allocated] if len(admitted) == 1 and not (fees or separate_pledges or unknown_fees) else []
    if len(admitted) == 1 and not (fees or separate_pledges or unknown_fees or collateral_confirmation or refunded_duty):
        split_award = _loan_with_separately_recognized_unclassified_fee(admitted[0])
        if split_award:
            return split_award
    if collateral_confirmation and (len(admitted) != 1 or separate_pledges or unknown_fees):
        return []
    if unknown_fees and (fees or len(unknown_fees) != 1):
        return []
    if unknown_fees:
        payee = PAYEE.search(unknown_fees[0])
        if re.search(r"в\s+пользу", unknown_fees[0], re.I) and not (
                creditor and payee and req.creditor_value(creditor.value, payee["name"], operative).value):
            return []
    if fees:
        if len(fees) > 1:
            return []
        fee = fees[0]
        expressly_linked = re.search(
            r"(?:за рассмотрение|по)\s+(?:(?:настоящ\w*|обособленн\w*)\s+)?(?:заявлени|спор)|"
            r"(?:пункт\w*\s*3\s*стать\w*|п\.\s*3\s*ст\.)\s*137|"
            r"государственная\s+пошлина.{0,110}\bучитываются\s+в\s+составе\s+требований\s+кредиторов",
            fee.quote, re.I,
        )
        payee = PAYEE.search(fee.quote) or CREDITOR.search(fee.quote)
        named_payee = bool(creditor and payee and req.creditor_value(creditor.value, payee["name"], operative).value)
        generic_payee = re.search(r"в\s+пользу\s+кредитора\b", fee.quote, re.I)
        if not (named_payee or (expressly_linked or STANDALONE_FEE_ACCOUNTING.search(fee.quote)) and not payee and (
                not re.search(r"в\s+пользу", fee.quote, re.I) or generic_payee)):
            return []
    queue_rows = []
    for quote in admitted:
        queues = queue_candidates(quote)
        if not queues and restoration_refusal and len(admitted) == 1:
            start = operative.index(restoration_refusal)
            end = operative.find(quote, start + len(restoration_refusal))
            if end != -1 and not re.search(r"\bвключить\s+в\s+реестр\b", quote, re.I):
                proof = EXPIRED_REGISTRY_ORDER.search(operative[start:end + len(quote)])
                if proof:
                    queues = [Fact(value="За реестром", quote=proof[0])]
        if len(queues) > 1:
            split = split_queue_items(quote)
            if not split or fees or separate_pledges or unknown_fees:
                return []
            queue_rows.extend(split)
        else:
            queue_rows.append((quote, queues))
    partitions = []
    inconsistent_parts = set()
    priced_parts = set()
    for quote, queues in queue_rows:
        sections = contract_sections(quote)
        if not sections:
            if len(req.contract_numbers(quote)) > 1:
                # A single common award with no requested allocation can
                # establish a queue amount, but cannot populate contract rows.
                amounts = list(AMOUNTS.finditer(quote))
                if not (len(admitted) == len(queues) == len(fees) == len(amounts) == 1
                        and not BREAKDOWN.search(quote)
                        and re.search(r"\bв\s+общем\s+размере\s*$", quote[:amounts[0].start()], re.I)):
                    return []
            partitions.append((quote, queues))
            continue
        if unknown_fees or collateral_confirmation or len(queues) != 1:
            return []
        heading, parts = sections
        numbers = [req.contract_numbers(part) for part in parts]
        if any(len(item) != 1 for item in numbers) or len({next(iter(item)) for item in numbers}) != len(parts):
            return []
        if gross := AMOUNTS.search(heading):
            stated = [claim_amounts(part, None)[1] for part in parts]
            amounts = [money(total.amount) if total else None for total in stated]
            if None in amounts:
                return []
            if sum(amounts) != money(gross[0]):
                if not all(re.match(r"\d+\.\d+\.\s+требован\w*\s+по\b", part, re.I) for part in parts):
                    return []
                inconsistent_parts.update(parts)
            priced_parts.update(parts)
        partitions.extend((part, queues) for part in parts)
    mapped_pledges = {}
    for pledge in separate_pledges:
        references = req.contract_numbers(pledge.quote)
        matching = [i for i, (quote, _) in enumerate(partitions)
                    if len(references) == 1 and references.keys() & req.contract_numbers(quote).keys()]
        if len(partitions) == 1:
            matching = [0]
        if len(matching) != 1 or matching[0] in mapped_pledges:
            return []
        mapped_pledges[matching[0]] = pledge
    rows = []
    single_fee = fees[0] if fees and len(partitions) == 1 else None
    for index, (quote, queues) in enumerate(partitions):
        if not AMOUNTS.search(quote):
            return []
        fields, total = claim_amounts(quote, single_fee)
        itemized_admission = None
        if single_fee and total is None and "principal_debt" not in fields:
            itemized = _itemized_admission_with_commission(quote)
            if itemized:
                itemized_admission, fields["financial_sanctions"] = itemized
        if queues:
            fields["priority_queue"] = queues[0]
            if (not fees and not unknown_fees and total and total.scope == "unknown"
                    and _is_final_unitemized_total(
                        quote, total, fields.get("financial_sanctions"), queues[0].value, refunded_duty=refunded_duty,
                    )):
                total.scope = "with_duty"
            if quote in priced_parts and total and total.scope == "unknown" and (
                    not re.search(r"пошлин|судебн\w*\s+расход", quote, re.I)):
                total.scope = "with_duty"
        contracts = req.contract_numbers(quote)
        own = pledge_candidate(quote)
        external = mapped_pledges.get(index)
        if (own or external) and not (own and external and own.value != external.value):
            fields["pledge_resolution"] = own or external
        salary = SALARY_WORDS.search(quote)
        if salary:
            fields["is_salary_or_severance"] = Salary(value=True, quote=salary[0])
        elif itemized_admission is not None or all(name in fields for name in CATEGORY_FIELDS):
            fields["is_salary_or_severance"] = Salary(value=False, quote=quote)
        elif total and total.scope != "unknown" and (basis := CONTRACT_BASIS.search(quote)):
            fields["is_salary_or_severance"] = Salary(value=False, quote=basis[0])
        if unknown_fees:
            # An embedded duty would make the relationship to the unreadable
            # separate fee ambiguous, so let the model resolve the whole layout.
            duty = fields.get("pau_only_amount")
            if duty and duty.items:
                return []
            fields.pop("pau_only_amount", None)
            if total:
                total.scope = "without_duty"
        if collateral_confirmation:
            fields["pledge_resolution"] = collateral_confirmation
            start = operative.index(quote)
            end = operative.index(collateral_confirmation.quote, start + len(quote))
            quote = operative[start:end + len(collateral_confirmation.quote)]
            contracts = req.contract_numbers(quote)
        number = next(iter(contracts.values())) if len(contracts) == 1 else None
        if number and re.search(
                rf"[,;]\s*по\s+договор\w*\s+(?:на|о)\s+предоставлени\w*\s+"
                rf"(?:возобновляем\w*\s+)?кредитн\w*\s+лини\w*\s+пут[её]м\s+подписани\w*\s+"
                rf"эмиссионн\w*\s+контракт\w*(?:{BASIS_DATE})?{BASIS_NUMBER}", quote, re.I):
            number = None
        rows.append(Claim(quote, fields, total, number, parent_total_conflict=quote in inconsistent_parts,
                          itemized_admission=itemized_admission))
    if fees and not single_fee:
        fee = fees[0]
        fields = {name: Components(items=[fee] if "duty" in kinds else [], complete=True, quote=fee.quote)
                  for name, kinds in CATEGORY_FIELDS.items()}
        fee_order = re.split(r"(?<=\.)\s+В\s+силу\s+пункт\w*\s+3\b", fee.quote, maxsplit=1, flags=re.I)[0]
        fee_queues.update((queue.value, queue) for queue in queue_candidates(fee_order))
        if len(fee_queues) == 1:
            fields["priority_queue"] = next(iter(fee_queues.values()))
        if (pledge := pledge_candidate(fee.quote)) and re.search(
                r"\bуч(?:есть|итывать)\s+как\s+обеспеченн\w*\s+залог", pledge.quote, re.I):
            fields["pledge_resolution"] = pledge
        fields["is_salary_or_severance"] = Salary(value=False, quote=fee.quote)
        rows.append(Claim(fee.quote, fields, CourtTotal(amount=fee.amount, scope="with_duty", quote=fee.amount)))
    return rows


def _source_contract_reference(source: str, operative: str, claim: Claim, fields: dict[str, Fact]) -> Fact | None:
    basis, category = fields.get("grounds_ocr"), fields.get("grounds")
    if (claim.contract_number or not fields.get("creditor") or not basis or not category
            or category.value not in {"Кредитный договор", "Договор займа"}
            or not basis.quote or basis.quote in operative
            or re.search(r"отказат\w*|прекрат\w*|выделить|план\s+реструктуризац", operative, re.I)):
        return None
    references = req.contract_numbers(basis.quote)
    if len(references) != 1 or references.keys() != req.contract_numbers(source).keys() or basis.quote.count("№") > 1:
        return None
    number = next(iter(references.values()))
    if (claim.cost_reference and claim.cost_reference.value == "prior_cost"
            and req.contract_numbers(claim.cost_reference.quote).keys() == references.keys()):
        return Fact(value=number, quote=claim.cost_reference.quote)
    if not claim.total:
        return None
    start = source.find(basis.quote)
    if start < 0:
        return None
    end = start + len(basis.quote)
    left = max(0, start - 360)
    preceding = source[left:start]
    following = source[end:end + 380]
    amount = AMOUNTS.search(following)
    if amount and money(amount[0]) == money(claim.total.amount):
        lead = following[:amount.start()]
        if GROSS.fullmatch(lead.lstrip(" ,")) or (
                GROSS.search(lead) and re.search(r"неисполнени\w*.{0,180}образовал\w*\s+задолжен", lead, re.I)):
            return Fact(value=number, quote=source[start:end + amount.end()])
    if re.match(r"основанн\w*\s+на\b", basis.quote, re.I):
        attached = re.search(rf"(?:в\s+размере|на\s+сумму)\s+(?P<amount>{AMOUNT})\s*,\s*$", preceding, re.I)
        if attached and money(attached["amount"]) == money(claim.total.amount):
            return Fact(value=number, quote=source[left + attached.start():end])
    application = re.search(
        r"\b(?:об\s+установлении\s+размера\s+требован\w*\s+кредитора\s+в\s+деле\s+о\s+банкротстве"
        r"\s+должник\w*[^.;]{0,200}|о\s+включении\s+в\s+реестр\s+требован\w*\s+кредиторов"
        r"\s+должника\s+)$", preceding, re.I,
    )
    if application and (application[0].casefold().startswith("об")
                        or re.match(r"требован\w*\s+по\b", basis.quote, re.I)):
        return Fact(value=number, quote=source[left + application.start():end])
    return None


def _filing_court_order(body: str, claim: Claim) -> Fact | None:
    """Tie an earlier order to the exact sum filed and admitted in this act."""
    if not claim.total:
        return None
    filings = list(re.finditer(
        rf"\bо\s+включении\s+требован\w*\s+(?:в\s+(?:сумме|размере)|на\s+сумму)\s+(?P<amount>{AMOUNT})",
        body, re.I,
    ))
    if len(filings) != 1 or money(filings[0]["amount"]) != money(claim.total.amount):
        return None
    filing = filings[0]
    following = body[filing.end():filing.end() + 820]
    order = re.search(
        rf"\b(?:на\s+основании|требован\w*\s+основан\w*\s+на)\s+судебн\w*\s+приказ\w*"
        rf"[^;]{{0,180}}?{BASIS_DATE}(?:\s+по\s+делу)?{BASIS_NUMBER}",
        following, re.I,
    )
    if not order or AMOUNTS.search(following[:order.start()]):
        return None
    return Fact(value=order[0], quote=body[filing.start():filing.end() + order.end()])


def _filing_contract_nature(body: str, claim: Claim, basis: Fact | None) -> Salary | None:
    if (not claim.total or claim.total.scope != "without_duty" or not basis or not basis.quote
            or not CONTRACT_BASIS.search(basis.quote)):
        return None
    match = re.search(
        rf"{re.escape(basis.quote)}\s+(?:в\s+(?:общем\s+)?размере|на\s+сумму)\s+(?P<amount>{AMOUNT})",
        body, re.I,
    )
    if match and money(match["amount"]) == money(claim.total.amount):
        return Salary(value=False, quote=match[0])
    return None


def _recover_embedded_cost(body: str, claim: Claim, order: Fact | None) -> None:
    if "pau_only_amount" in claim.fields or len(re.findall(r"пошлин", claim.quote, re.I)) != 1:
        return
    label = r"(?:государственн\w*\s+пошлин\w*|госпошлин\w*)"
    item = re.search(rf"{label}\s*[-–—]\s*(?P<amount>{AMOUNT})", claim.quote, re.I) or re.search(
        rf"(?P<amount>{AMOUNT})\s*[-–—]?\s*(?:расход\w*\s+по\s+(?:уплате|оплате)\s+)?{label}", claim.quote, re.I,
    )
    if not item and order:
        item = re.search(rf"(?:задолжен\w*\s+по\s+уплате\s+)?{label}\s+(?P<amount>{AMOUNT})", claim.quote, re.I)
    if not item or excluded_component(item[0], claim.quote):
        return
    historical = re.search(
        rf"(?P<amount>{AMOUNT})\s+{label},\s*основанн\w*\s+на\s+неисполнени\w*\s+"
        rf"договор\w*\s+(?:микро)?займ\w*[^;()]{{0,150}}\(судебн\w*\s+приказ\w*\s*№[^()]{{3,150}}\)",
        body, re.I,
    )
    current = re.search(
        r"\bТаким\s+образом,\s+государственн\w*\s+пошлин\w*\s+подлеж\w*\s+включению\s+в\s+"
        r"\w+\s+очередь\s+реестра\s+требований\s+кредиторов\s+с\s+отдельным\s+уч[её]том,\s*"
        r"для\s+удовлетворения\s+после\s+погашения\s+основн\w*\s+сумм\w*[^;]{0,110}?"
        r"(?:пункт\w*\s*3\s*стать\w*|п\.\s*3\s*ст\.)\s*137\b",
        body, re.I,
    )
    references = req.contract_numbers(historical[0]) if historical else {}
    if (historical and money(historical["amount"]) == money(item["amount"])
            and len(references) == 1 and references.keys() == req.contract_numbers(body + " " + claim.quote).keys()):
        proof = Fact(value="prior_cost", quote=historical[0])
    elif current and not historical:
        proof = Fact(value="duty", quote=current[0])
    elif order and not historical and not current:
        proof = Fact(value="prior_cost", quote=order.quote)
    else:
        return
    component = Component(kind=proof.value, amount=item["amount"], quote=item[0])
    amounts, total = claim_amounts(claim.quote, None, embedded_cost=component)
    if (order and proof.quote == order.quote and total and total.scope == "with_duty" and "principal_debt" not in amounts
            and re.fullmatch(rf"{AMOUNT}\s*[-–—]\s*расход\w*\s+по\s+уплате\s+{label}", item[0], re.I)):
        # The two printed items reconcile, but «задолженность» has no internal breakdown.
        total.scope = "without_duty"
        claim.total = total
        claim.fields["pau_only_amount"] = Components(items=[], complete=True, quote=claim.quote)
        claim.cost_reference = proof
        return
    if not all(name in amounts for name in CATEGORY_FIELDS) or not any(
        component in amount.items for amount in amounts.values()
    ):
        return
    claim.fields.update(amounts)
    claim.total = total
    claim.cost_reference = proof
    if "is_salary_or_severance" not in claim.fields and not SALARY_WORDS.search(claim.quote):
        claim.fields["is_salary_or_severance"] = Salary(value=False, quote=claim.quote)


def extract_candidates(text: str) -> Candidates:
    source = re.sub(r"\s+", " ", text).strip()
    operative = re.sub(r"\s+", " ", operative_part(text))
    fields = {}
    document, main, quote = source_case(text)
    if document:
        fields.update(document_case_number=Fact(value=document, quote=quote), case_number=Fact(value=main, quote=quote))
    creditor = creditor_candidate(source, operative)
    creditor_subject = creditor.value if creditor else None
    tax_subject = None
    if creditor:
        if req.tax_identity(creditor.value):
            tax_subject, creditor_quote = tax_creditor_subject(creditor, source, operative)
            creditor_subject = tax_subject
            fields["creditor"] = Fact(value=PUBLIC_TAX_CREDITOR, quote=creditor_quote)
        else:
            fields["creditor"] = creditor
    debtor = req.source_inn(text, "debtor_inn")
    if not debtor.value and not req.debtor_anchors(text):
        debtor = operative_debtor_inn(operative)
    for name, result in (
        ("inclusion_date", req.ruling_date(text)),
        ("debtor_inn", debtor),
        ("creditor_inn", req.source_inn(text, "creditor_inn", creditor_subject)),
    ):
        if result.value:
            fields[name] = Fact(value=result.value, quote=result.quote)
    claims = claim_candidates(operative, creditor)
    fields.update(grounds_candidates(source, operative, claims))
    if len(claims) == 1:
        claim = claims[0]
        body_end = source.rfind(operative)
        if body_end >= 0:
            body = source[:body_end]
            order = _filing_court_order(body, claim)
            if order:
                fields.setdefault("grounds_ocr", Fact(value=order.value, quote=order.value))
            if claim.total and "grounds_ocr" not in fields:
                cited_order = re.search(
                    rf"(?P<amount>{AMOUNT}),\s*в\s+отсутствие\s+возражений\s+должника\s+и\s+"
                    rf"финансового\s+управляющего,\s*(?P<basis>с\s+уч[её]том\s+судебн\w*\s+приказ\w*{BASIS_REFERENCE})",
                    body, re.I,
                )
                if cited_order and money(cited_order["amount"]) == money(claim.total.amount):
                    basis = cited_order["basis"]
                    fields["grounds_ocr"] = Fact(value=basis, quote=basis)
            _recover_embedded_cost(body, claim, order)
            if "is_salary_or_severance" not in claim.fields:
                nature = _filing_contract_nature(body, claim, fields.get("grounds_ocr"))
                if nature:
                    claim.fields["is_salary_or_severance"] = nature
        reference = _source_contract_reference(source, operative, claim, fields)
        if reference:
            claim.contract_number = reference.value
            claim.contract_reference = reference
    return Candidates(fields=fields, claims=claims, creditor_tax_subject=tax_subject)

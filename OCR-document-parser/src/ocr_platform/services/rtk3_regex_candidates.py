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


@dataclass
class Candidates:
    fields: dict[str, Fact]
    claims: list[Claim]


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
TAX_KIND = r"(?:транспортн\w*|земельн\w*|имущественн\w*)\s+налог\w*"
CONTRACTS = {
    "Кредитный договор": (
        r"(?:кредитн\w+\s+(?:договор\w*|соглашени\w*|карт\w*)|"
        r"договор\w*\s+потребительск\w*\s+кредит\w*\b(?!\s*\(\s*займ\w*\s*\))|"
        r"договор\w*\s+.{0,45}?\s+кредитн\w*\s+лини\w*|"
        r"предоставлени\w*\s+(?:возобновляем\w*\s+)?кредитн\w*\s+лини\w*|"
        r"договор\w*\s+о\s+предоставлении\s+(?:потребительск\w+\s+)?кредит\w*\b"
        r"(?!\s*\(\s*займ\w*\s*\)))"
    ),
    "Договор займа": (
        r"(?:договор\w*\s+(?:(?:потребительск\w*|целев\w*|жилищн\w*)\s+){0,2}(?:микро)?займ\w*|"
        r"договор\w*\s+о\s+предоставлении\s+(?:потребительск\w+\s+)?"
        r"(?:кредит\w*\s*\(\s*займ\w*\s*\)|(?:микро)?займ\w*)|"
        r"процент\w*\s+за\s+пользование\s+займом)"
    ),
    "Налоговая задолженность": (
        r"(?:налогов\w+\s+задолжен\w*|задолжен\w*\s+по\s+(?:уплате\s+)?налог\w*|"
        r"недоимк\w*\s+по\s+налог\w*|основн\w+\s+долг\s*\(налог\w*\)|"
        r"задолжен\w*\s+в\s+бюджет(?:\s+и\s+внебюджетн\w*\s+фонд\w*)?|"
        r"страхов\w+\s+взнос\w*|задолжен\w*\s+по\s+обязательн\w*\s+платеж\w*|"
        rf"долг\w*\s+по\s+{TAX_KIND})"
    ),
    "Оплата коммунальных услуг": r"(?:(?:по\s+)?оплат\w*\s+(?:жилищно[- ]?)?коммунальн\w+\s+услуг\w*)",
}
LEGAL_NAME = (
    r"(?:(?:" + "|".join(pattern for pattern, _ in req.LEGAL_FORMS[:4])
    + r"|\b(?:ПАО|АО|ООО|НАО|ПКО|ФГКУ|ФГБУ)\b)\s+(?:(?:КБ|ПКО|МКК|МФК|Банк|"
    r"микрофинансов\w*\s+компани\w*|микрокредитн\w*\s+компани\w*|"
    r"профессиональн\w*\s+коллекторск\w*\s+организаци\w*|коллекторск\w*\s+бюро)\s+)?"
    r'(?:["«](?:ПКО|профессиональн\w*\s+коллекторск\w*\s+организаци\w*)\s*["«][^"»]{2,100}["»]["»]?'
    r'|«[^»]{2,120}»|"[^"]{2,120}"|(?-i:[А-ЯЁA-Z][А-ЯЁа-яёA-Za-z0-9-]{2,})(?=\s*(?:\(|,|[0-9]|\b(?:расходы|удовлетворить|обоснованн\w*|о\s+включении|включить|в\s+(?:реестр|третью|размере))|$)))'
    r"|Банк(?:а|у|ом)?\s+ВТБ(?:\s*\(ПАО\))?"
    r'|Банк(?:а|у|ом)?\s+[«"][^»"]{2,100}[»"]\s*\((?:ПАО|АО)\)'
    r"|Банк(?:а|у|ом)?\s+[А-ЯЁA-Z][А-ЯЁа-яёA-Za-z-]{1,60}\s*\((?:ПАО|АО)\)"
    r"|УФНС\s+России\s+по\s+[а-яё-]+\s+(?:област\w*|кра[юяй])"
    r"|(?:Межрайонн\w*\s+)?(?:МИФНС|ИФНС)(?:\s+России)?\s*№\s*\d+"
    r"(?:\s+по\s+[^()\n,;]{2,65}?(?:\s+област\w*|\s+кра[юяй]|\s+Республик\w*\s+[А-ЯЁ][а-яё-]+))?"
    r"|(?:межрайонн\w*\s+)?инспекци\w*\s+Федеральн\w*\s+налогов\w*\s+служб\w*"
    r"\s*№\s*\d+\s+по\s+(?:[а-яё-]+\s+област\w*|Республик\w*\s+[а-яё-]+)"
    r"|ИФНС\s+по\s+г\.\s+[а-яё-]+\s+[а-яё-]+\s+област\w*"
    r"|ФНС(?:\s+России(?:\s*№\s*\d+(?:\s+по\s+[а-яё-]+\s+област\w*)?)?)?"
    r"|Федеральн\w*\s+налогов\w*\s+служб\w*(?:\s*№\s*\d+\s+по\s+[а-яё-]+\s+област\w*)?)"
)
CREDITOR = re.compile(r"(?:\bтребован\w*|\bзаявлени\w*)\s+(?:(?:кредитор|уполномоченн\w*\s+орган)\w*"
                      r"(?:\s+Российск\w*\s+Федераци\w*)?(?:\s+в\s+лице)?\s*[-–—]?\s+)?(?P<name>" + LEGAL_NAME + ")", re.I)
PAYEE = re.compile(r"\bв\s+пользу\s+(?:кредитора\s+)?(?P<name>" + LEGAL_NAME + ")", re.I)
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
CREDIT_CONTRACT_NUMBER = re.compile(r"\bпо\s+кредитн\w*\s+договор\w*\s+№\s*([\w./-]+)", re.I)
EXPIRED_REGISTRY_ORDER = re.compile(
    r"\bв\s+(?:удовлетворении\s+заявлени\w*\s+кредитора\s+о\s+)?восстановлении\s+срока"
    r"\b.{0,250}\bотказать\b.{0,1300}\bв\s+порядке\s*,?\s*установленном\s+"
    r"(?:пунктом\s+4\s+статьи|п\.\s*4\s+ст\.)\s*142\b", re.I,
)
CONTRACT_BASIS = re.compile(
    r"\b(?:по|на\s+основании|в\s+рамках)\s+(?:" + CONTRACTS["Кредитный договор"]
    + "|" + CONTRACTS["Договор займа"] + ")", re.I,
)


def operative_part(text: str) -> str:
    markers = list(OPERATIVE.finditer(text))
    if len(markers) != 1:
        return ""
    return ENDING.split(text[markers[0].end():].strip(), maxsplit=1)[0].strip()


def queue_candidates(text: str) -> list[Fact]:
    found = {}
    for match in QUEUE.finditer(text):
        word = match["num"] or match["reverse"]
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


def creditor_candidate(text: str, operative: str) -> Fact | None:
    introduction = text[:min(len(text), 4500)]
    for part in (operative, introduction):
        found = {}
        for match in CREDITOR.finditer(part):
            preceding = part[:match.start()]
            explanations = list(re.finditer(r"\bРазъяснить\b", preceding, re.I))
            decisions = list(COMMAND.finditer(preceding))
            if explanations and (not decisions or explanations[-1].start() > decisions[-1].start()):
                continue
            name = re.sub(r"^банка\s+втб", "Банк ВТБ", match["name"], flags=re.I)
            if re.fullmatch(r"Федеральн\w*\s+налогов\w*\s+служб\w*", name, re.I):
                # Preserve the named federal authority, independently of its representative.
                name = "Федеральная налоговая служба"
            name = re.sub(
                r'["«](ПКО|профессиональн\w*\s+коллекторск\w*\s+организаци\w*)\s*["«]([^"»]+)["»]["»]?',
                r'ПКО «\2»', name, flags=re.I,
            )
            value = req.normalize_creditor(name)
            found.setdefault(req.identity(value), Fact(value=value, quote=match[0]))
        if len(found) > 1:
            return None
        if found:
            return next(iter(found.values()))
    return None


def grounds_candidates(text: str, operative: str) -> dict[str, Fact]:
    for source in (operative, text):
        matches = [(category, m) for category, pattern in CONTRACTS.items()
                   for m in re.finditer(pattern, source, re.I)]
        if source == operative:
            matches += [("Налоговая задолженность", m) for m in re.finditer(
                r"\bпо\s+(?:обязательн\w*\s+платеж\w*|налогам\s+и\s+пеням)\b", source, re.I,
            )]
        if not matches and source == operative:
            matches = [("Налоговая задолженность", m) for m in re.finditer(
                rf"\b{TAX_KIND}\b|\bпо\s+уплате\s+обязательных\s+платежей(?=\s+в\s+общем\s+размере)",
                source, re.I,
            )]
        if not matches:
            continue
        categories = {category for category, _ in matches}
        if len(categories) != 1:
            return {}
        category, first = matches[0]
        number = r"\s*:?\s*(?:№|N)\s*[\w./-]+"
        date = r'\s+от\s+(?:\d{1,2}\.\d{1,2}\.\d{4}|[«"]?\d{1,2}[»"]?\s+[а-яё]+\s+\d{4}(?:\s+года)?)'
        references = {}
        for _, match in matches:
            suffix = re.match(rf"(?:(?:{number})(?:{date})?|(?:{date})(?:{number})?)?", source[match.end():], re.I)[0]
            if suffix:
                continuation = re.match(
                    rf"(?:(?:\s*[;,]\s*(?:и\s+)?|\s+и\s+)(?:{number})(?:{date})?)*",
                    source[match.end() + len(suffix):], re.I,
                )[0]
                suffix += continuation
            prefix = re.search(
                r"\b(?:(?:задолжен\w*|требован\w*|долг\w*)\s+)?"
                r"(?:по|из|на\s+основании|в\s+рамках)\s*$",
                source[max(0, match.start() - 80):match.start()], re.I,
            )
            key = compact(match[0] + suffix)
            wording = (prefix[0] if prefix else "") + match[0] + suffix
            if key not in references or len(wording) > len(references[key]):
                references[key] = wording
        evidence = Fact(value=category, quote=first[0])
        detailed = [phrase for phrase in references.values() if re.search(r"\d", phrase)]
        wording = detailed or list(references.values())
        if category == "Налоговая задолженность":
            general = [phrase for phrase in wording if re.search(r"обязательн\w*\s+платеж", phrase, re.I)]
            if general:
                wording = [max(general, key=len)]
        result = {"grounds": evidence}
        if len(wording) == 1:
            result["grounds_ocr"] = Fact(value=wording[0], quote=wording[0])
        return result
    # A literal contract reference is useful independently of its category.
    # Do not infer a credit/loan type merely from the creditor's name.
    number = r"\s*(?:№|N)\s*[\w./-]+"
    date = r'\s+от\s+(?:\d{1,2}\.\d{1,2}\.\d{4}|[«"]?\d{1,2}[»"]?\s+[а-яё]+\s+\d{4}(?:\s+года)?)'
    references = {}
    for match in re.finditer(
        rf"\b(?:на\s+основании|в\s+рамках|по)\s+договор\w*\s*:?(?:(?:{number})(?:{date})?|(?:{date})(?:{number})?)",
        operative, re.I,
    ):
        prefix = operative[max(0, match.start() - 90):match.start()]
        if re.search(r"залог|поручител|уступк", prefix, re.I):
            continue
        references.setdefault(compact(match[0]), match[0])
    if len(references) == 1:
        quote = next(iter(references.values()))
        return {"grounds_ocr": Fact(value=quote, quote=quote)}
    return {}


def operative_debtor_inn(operative: str) -> req.Requisite:
    """Read a unique INN from an explicitly named debtor's own operative record."""
    role = (
        r"(?i:\b(?:реестр\w*\s+требован\w*(?:\s+кредитор\w*)?\s+"
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
        if re.search(r"пошлин", quote, re.I) and CURRENT_DUTY.search(quote):
            return "duty"
        return None
    if re.search(r"процент", quote, re.I):
        return "interest"
    if re.search(r"основн\w+\s+(?:долг|задолжен|сумм|требован)|ссудн\w*\s+задолжен|"
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
    preceding = re.split(r";|,(?![0-9])|(?<!\d)\.(?!\d)", text[lower:match.start()])[-1]
    following = re.split(r";|,(?![0-9])|(?<!\d)\.(?!\d)|\)", text[match.end():upper], maxsplit=1)[0]
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


def claim_amounts(quote: str, fee: Component | None) -> tuple[dict[str, Components], CourtTotal | None]:
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
    alternatives = []
    for amount_first in (True, False):
        found = []
        for i, match in enumerate(parts):
            lower = parts[i - 1].end() if i else (split.end() if split and split.end() < match.start() else 0)
            upper = parts[i + 1].start() if i + 1 < len(parts) else len(quote)
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
        covered = declared_total in {actual, actual - current}
    if fee and not same_fee:
        components.append(fee)
    if not covered:
        # A missed token can change any component sum. Preserve explicitly
        # separate current duty only if nothing else refers to duty in this row.
        partial = ({"pau_only_amount": Components(items=[fee], complete=True, quote=fee.quote)}
                   if fee and not re.search(r"пошлин", quote, re.I) else {})
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
        if direction is not None and split and declared_total is not None and declared_total == sum(
                (money(part[0]) for part in parts), Decimal(0)):
            unknown = []
            for i, (part, component) in enumerate(zip(parts, chosen)):
                if component is not None:
                    continue
                lower = parts[i - 1].end() if i else split.end()
                upper = parts[i + 1].start() if i + 1 < len(parts) else len(quote)
                label = _component_quote(quote, part, lower, upper, amount_first=direction)
                unknown.append(bool(re.search(r"пошлин|судебн\w*\s+расход", label, re.I))
                               and not excluded_component(label, quote))
            if unknown and all(unknown):
                partial["financial_sanctions"] = Components(
                    items=[c for c in components if c.kind == "sanction"], complete=True, quote=quote,
                )
                if total and fee is None:
                    # The exact sum of the printed list includes these costs
                    # regardless of their classification as prior or current.
                    total.scope = "with_duty"
        return partial, total
    return {name: Components(items=[c for c in components if c.kind in kinds], complete=True, quote=quote)
            for name, kinds in CATEGORY_FIELDS.items()}, total


def nested_contract_amounts(quote: str, fee: Component | None):
    """Flatten explicit contract subtotals only after each subtotal balances."""
    listed_contracts = re.search(r"\bкредитн\w*\s+договор\w*\s*:\s*(?=[-–—]\s+№)", quote, re.I)
    if listed_contracts:
        pattern = r"(?:^|\s)[-–—]\s+№\s*[\w./-]+\s+от\s+\d{1,2}\.\d{1,2}\.\d{4}"
    else:
        pattern = r"(?:[-–—]\s+|(?:в\s+том\s+числе\s*:|;)\s*)по\s+кредитн\w*\s+договор\w*"
    starts = list(re.finditer(pattern, quote, re.I))
    if len(starts) < 2:
        return None
    heading = quote[:starts[0].start()]
    gross = list(AMOUNTS.finditer(heading))
    if len(gross) > 1 or (not gross and not listed_contracts):
        return None
    grouped = {name: [] for name in CATEGORY_FIELDS}
    subtotal = Decimal(0)
    all_with_costs = True
    for index, start in enumerate(starts):
        part = quote[start.end():starts[index + 1].start() if index + 1 < len(starts) else len(quote)]
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
    starts = list(COMMAND.finditer(operative))
    if not starts or operative[:starts[0].start()].strip(" \n.0123456789)"):
        return []
    blocks = [operative[m.start():(starts[i + 1].start() if i + 1 < len(starts) else len(operative))].strip()
              for i, m in enumerate(starts)]
    admitted, fees, separate_pledges, unknown_fees = [], [], [], []
    allocated = None
    collateral_confirmation = None
    restoration_refusal = None
    registry_refusal = False
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
            continue
        elif command == "учесть" and re.search(r"отдельно.*основн", block, re.I) and not AMOUNTS.search(block):
            continue
        else:
            return []
    if registry_refusal or not admitted or len(admitted) > 12 or (len(admitted) != 1 and (fees or separate_pledges or unknown_fees)):
        return []
    if allocated:
        return [allocated] if len(admitted) == 1 and not (fees or separate_pledges or unknown_fees) else []
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
    partitions = []
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
            partitions.extend(split)
        else:
            partitions.append((quote, queues))
    rows = []
    for quote, queues in partitions:
        if not AMOUNTS.search(quote):
            return []
        fields, total = claim_amounts(quote, fees[0] if fees else None)
        if queues:
            fields["priority_queue"] = queues[0]
        if len(separate_pledges) <= 1:
            own = pledge_candidate(quote)
            external = separate_pledges[0] if separate_pledges else None
            if (own or external) and not (own and external and own.value != external.value):
                fields["pledge_resolution"] = own or external
        salary = SALARY_WORDS.search(quote)
        if salary:
            fields["is_salary_or_severance"] = Salary(value=True, quote=salary[0])
        elif all(name in fields for name in CATEGORY_FIELDS) or (
                total and total.scope == "with_duty" and CONTRACT_BASIS.search(quote)):
            fields["is_salary_or_severance"] = Salary(value=False, quote=quote)
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
        rows.append(Claim(quote, fields, total))
    return rows


def extract_candidates(text: str) -> Candidates:
    source = re.sub(r"\s+", " ", text).strip()
    operative = re.sub(r"\s+", " ", operative_part(text))
    fields = {}
    document, main, quote = source_case(text)
    if document:
        fields.update(document_case_number=Fact(value=document, quote=quote), case_number=Fact(value=main, quote=quote))
    creditor = creditor_candidate(source, operative)
    if creditor:
        fields["creditor"] = creditor
    debtor = req.source_inn(text, "debtor_inn")
    if not debtor.value and not req.debtor_anchors(text):
        debtor = operative_debtor_inn(operative)
    for name, result in (
        ("inclusion_date", req.ruling_date(text)),
        ("debtor_inn", debtor),
        ("creditor_inn", req.source_inn(text, "creditor_inn", creditor.value if creditor else None)),
    ):
        if result.value:
            fields[name] = Fact(value=result.value, quote=result.quote)
    fields.update(grounds_candidates(source, operative))
    return Candidates(fields=fields, claims=claim_candidates(operative, creditor))

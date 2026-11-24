"""RTK3 v2: quoted facts -> exact decimal ledger -> EMU-compatible fields.

The confirmed EMU convention is intentional: total_amount includes duty;
claimed_amount and total_claimed_amount contain the included amount without duty.
No case metadata or operator targets are inputs to this module.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
import json
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, create_model
import httpx

from ocr_platform.config.settings import get_settings
from ocr_platform.services import rtk3_requisites as requisites
from ocr_platform.services.llm_gateway import call_llm_json_with_fallback
from ocr_platform.observability.logging import get_logger
from ocr_platform.services.field_retries import (
    MAX_EMPTY_FIELD_RETRIES, empty_value, has_empty_value, merge_missing, preserves_values, required_names,
)
from ocr_platform.services.rtk3_requisites import date_value as date_value
from ocr_platform.services.rtk3_gemini_prompts import SCALAR_RULES, build_gemini_prompt

VERSION = "rtk3_evidence_v2"
logger = get_logger(__name__)
GROUNDS = {"Кредитный договор", "Договор займа", "Налоговая задолженность", "Оплата коммунальных услуг"}
QUEUES = {"1 очередь", "2 очередь", "3 очередь", "4 очередь", "За реестром", "Иное"}
PLEDGE_STATUSES = {"yes", "no", "separated"}
CATEGORY_FIELDS = {
    "principal_debt": {"principal", "interest", "prior_cost", "contract_cost"},
    "financial_sanctions": {"sanction"},
    "pau_only_amount": {"duty"},
}


def is_duty_only(row: dict) -> bool:
    fee = money(row.get("pau_only_amount"))
    return fee is not None and fee > 0 and money(row.get("claimed_amount")) == 0


def row_pledges(rows: list[dict]) -> set[str | None]:
    return {row.get("pledge_resolution") for row in rows if not is_duty_only(row)}


def publication_date_field() -> dict:
    return {"value": None, "confidence": None, "reasoning": "Поле всегда равно null по правилам РТК-3.",
            "source": VERSION, "extraction_method": "fixed"}


class Fact(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: str | None
    quote: str


class PriorityFact(Fact):
    value: Literal["1 очередь", "2 очередь", "3 очередь", "4 очередь", "За реестром", "Иное"] | None


class PledgeFact(Fact):
    value: Literal["yes", "no", "separated"] | None


class Component(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["principal", "interest", "sanction", "duty", "prior_cost", "contract_cost"]
    amount: str = Field(description="Exact monetary wording from the quote, including any spaces, rubles and kopecks")
    quote: str


class ClaimEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    priority_queue: PriorityFact
    decision_quote: str = Field(description=(
        "Contiguous operative quote from the court's admission command through this contract's item, "
        "including the shared admission heading when there are several contracts."
    ))
    breakdown_quote: str = Field(description=(
        "For several contracts under one admission command, required exact quote of this single contract's item "
        "with its number, subtotal and all components, even if decision_quote already includes the item. "
        "For a separate admission command, an exact earlier breakdown of the same admitted total or empty."
    ))
    components: list[Component]
    components_complete: bool
    complete_categories: list[Literal["principal_debt", "financial_sanctions", "pau_only_amount"]] = Field(default_factory=list)
    included_total: Fact = Field(description="Explicit court total for this claim; not the originally requested sum")
    total_scope: Literal["with_duty", "without_duty", "unknown"]
    principal_by_remainder: bool = False
    pledge_resolution: Literal["yes", "no", "separated", "unknown"]
    pledge_quote: str
    is_salary_or_severance: bool | None = Field(description="True for explicit salary/severance, false for a confirmed different nature, null if unknown")
    salary_quote: str
    uncertainty: str


class DocumentEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_case_number: Fact
    debtor_inn: Fact
    creditor: Fact
    creditor_inn: Fact
    inclusion_date: Fact
    kad_publication_date: Fact
    grounds: Fact
    claims: list[ClaimEvidence]
    claims_complete: bool
    uncertainty: str


# Compatibility entry point uses the same field rules as regex_jev.
INSTRUCTION = build_gemini_prompt(set(SCALAR_RULES) | {"claims"}, full_claims=True)


def compact(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold()


# Fiscal principal may be itemized by payment type without the words "основной долг".
_PUBLIC_PAYMENT = re.compile(
    r"\b(?:страхов(?:ые|ых|ым|ыми|ый|ого|ому)\s+взнос\w*|недоимк\w*|"
    r"(?:транспортн|земельн|имущественн)\w*\s+налог(?:а|у|ом)?|"
    r"сумм\w*\s+налога|налог(?:а|у|ом)?\s+на\s+(?:имущество|доходы|прибыль|добавленную\s+стоимость))\b", re.I,
)
_PUBLIC_PAYMENT_DEDUCTIONS = re.compile(
    r"\b(?:пен(?:и|я|ей|ям|ями|ях|ю)|штраф\w*|неустойк\w*|санкци\w*|процент\w*|"
    r"(?:гос)?пошлин\w*|судебн\w*\s+расход\w*)\b", re.I,
)


def public_payment_principal(quote: str) -> bool | None:
    """Classify explicit fiscal labels; leave other debt to the existing checks."""
    if _PUBLIC_PAYMENT.search(quote):
        return not _PUBLIC_PAYMENT_DEDUCTIONS.search(quote)
    if re.search(r"\bналог(?:а|у|ом|ов|ам|ами|ах)?\b", quote, re.I) and _PUBLIC_PAYMENT_DEDUCTIONS.search(quote):
        return False
    return None


def remove_currency_page_marker(text: str) -> str:
    """A stand-alone page number can split a decimal amount from its currency."""
    return re.sub(
        r"(?<=\d[.,]\d{2})[ \t]*\r?\n[ \t]*\d{1,2}[ \t]*\r?\n(?=[ \t]*руб(?:лей|ля|ль)?\b)",
        " ", text, flags=re.I,
    )


def quoted(quote: str, text: str) -> bool:
    if not quote.strip():
        return False
    value = compact(quote)
    return value in compact(text) or "\n" in text and value in compact(remove_currency_page_marker(text))


OPERATIVE = re.compile(r"\bо\s*п\s*р\s*е\s*д\s*[еи]\s*л\s*и\s*л[аи]?\s*(?:[:：]|(?=\r?\n))", re.I)


def ordinary_admission_without_pledge(quote: str, decision: str, text: str) -> bool:
    """Validate Argon's 'no' for an ordinary final admission in a complete ruling."""
    if not quoted(quote, decision) or not quoted(decision, text):
        return False
    source = compact(text)
    if re.search(r"залог|ипотек|закладн", source):
        return False
    citation = compact(quote)
    if (not re.search(r"\b(?:включить|признать|установить)\b", citation)
            or not re.search(r"требован|задолжен", citation)
            or re.search(r"необоснован|\bне\s+включ|отказат|отложить", citation)):
        return False
    # A cropped operative paragraph cannot establish silence in the rest of the ruling.
    markers = list(OPERATIVE.finditer(text))
    if not markers:
        return False
    _, found, tail = compact(text[markers[-1].end():]).partition(citation)
    return bool(found and re.search(r"\bобжалован\w*\b|\bсудья\b|электронная подпись", tail))


SEPARATE_SANCTIONS = re.compile(
    r"учитывающ\w*\s+отдельно(?:\s+в\s+реестре\s+требований\s+кредиторов)?\s+и\s+"
    r"подлежащ\w*\s+удовлетворению\s+после\s+погашения\s+основной\s+суммы", re.I,
)


def separately_accounted_sanction(item: Component, decision: str) -> bool:
    """A priced third-priority allocation must carry the separate-payment wording itself."""
    if not quoted(item.quote, decision) or re.search(r"пошлин|судебн\w*\s+расход|издержк", decision, re.I):
        return False
    match = re.search(
        r"(?<![\d.,])(?P<amount>\d+(?: \d{3})*(?:[.,]\d{1,2})?\s*руб(?:лей|ля|ль)?\.?)"
        r"\s*[-–—]?\s*требован\w*\s+кредитор\w*\s+третьей\s+очереди\s*,?\s*"
        + SEPARATE_SANCTIONS.pattern + r"\s+(?:долга|задолженности)\s+и\s+причитающихся\s+процентов",
        compact(item.quote),
    )
    return bool(match and rtk3_money(match["amount"]) == rtk3_money(item.amount))


def money(value: object) -> Decimal | None:
    """Parse exact rubles; never round, infer missing digits, or accept bool/NaN."""
    if isinstance(value, bool) or value is None:
        return None
    s = re.sub(r"\s+", "", str(value)).casefold()
    grouped = re.fullmatch(r"([0-9]{1,3}(?:\.[0-9]{3})+(?:,[0-9]{1,2})?)(?:руб(?:лей|ля|ль)?\.?|р\.)", s)
    if grouped:
        s = grouped[1].replace(".", "")
    s = re.sub(r"(?<=\d)\([а-яё-]{3,200}\)(?=руб)", "", s)
    m = re.fullmatch(r"([0-9]+)(?:руб(?:лей|ля|ль)?\.?|р\.)([0-9]{1,2})(?:коп(?:еек|ейки|ейка)?\.?|к\.)", s)
    if m:
        s = m[1] + "." + m[2].zfill(2)
    else:
        s = re.sub(r"(?:руб(?:лей|ля|ль)?\.?|р\.)$", "", s).replace(",", ".")
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]{1,2})?", s):
        return None
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def rtk3_money(value: object) -> Decimal | None:
    """Read a redundant comma between two printed kopeck digits and the ruble unit."""
    if isinstance(value, str):
        value = re.sub(r"([.,]\s*[0-9]{2})\s*,(?=\s*(?:руб(?:лей|ля|ль)?\.?|р\.)\s*$)", r"\1", value, flags=re.I)
    return money(value)


def principal_from_award(printed: Decimal | None, scope: str,
                         sanctions: Decimal | None, current_fee: Decimal | None) -> Decimal | None:
    if printed is None or sanctions is None or current_fee is None or scope not in {"with_duty", "without_duty"}:
        return None
    balance = printed - sanctions
    if scope == "with_duty":
        balance -= current_fee
    return balance if balance >= 0 else None


def amount_supported(value: str | None, quote: str) -> bool:
    if not value:
        return False
    # Require the literal monetary token, not just a numerically matching
    # unrelated case number or date elsewhere in the document.
    def normalize(s):
        return re.sub(r"\s+", "", s).casefold().replace(",", ".")
    token, source = normalize(value), normalize(quote)
    if not token:
        return False
    currency_ended = re.search(r"[а-яё]\.?$", token) is not None
    for match in re.finditer(re.escape(token), source):
        if match.start() and source[match.start() - 1].isdigit():
            continue
        tail = source[match.end():]
        if tail and tail[0].isdigit() and (not currency_ended or re.match(r"\d{1,2}(?:коп|к\.)", tail)):
            continue
        return True
    return False


def single_contract_salary_evidence(quote: str, grounds: Fact | None, text: str) -> bool:
    """Bind an Argon non-wage decision to the sole claim's documented contract."""
    if not grounds or grounds.value not in {"Кредитный договор", "Договор займа"}:
        return False
    if not quoted(quote, grounds.quote) or not quoted(grounds.quote, text):
        return False
    if requisites.explicit_ground_category(quote) != grounds.value or re.search(
            r"зарплат|заработн\w*\s+плат|выходн\w*\s+пособ|оплат\w*\s+труда", text, re.I):
        return False
    contracts = requisites.contract_numbers(quote)
    return len(contracts) == 1 and contracts.keys() == requisites.contract_numbers(text).keys()


def itemized_debt_excludes_salary(quote: str, components: list[Component]) -> bool:
    """Require affirmative principal and interest evidence within this claim."""
    if re.search(r"заработн|зарплат|выходн\w*\s+пособ|оплат\w*\s+труд|трудов\w*\s+договор|работник",
                 compact(quote), re.I):
        return False
    labels = {"principal": r"\bосновн\w*\s+долг", "interest": r"\bпроцент"}
    supported = {
        item.kind for item in components
        if item.kind in labels and rtk3_money(item.amount) and quoted(item.quote, quote)
        and re.search(labels[item.kind], compact(item.quote), re.I)
    }
    return supported == labels.keys()


def excluded_component(quote: str, text: str) -> bool:
    """Reject facts quoted from an operative exclusion, not all mentions of a sum.

    The same number may legitimately occur in two different obligations. Match
    the component's actual excerpt to the exclusion paragraph, not just its amount.
    """
    source = compact(text)
    stop = (r"(?=\s+(?:включить|признать|взыскать|вернуть|возвратить|"
            r"производство\s+по|прекратить\s+производство|определение\s)|"
            r"(?<=\.)\s+(?:государственная\s+пошлина|судебные\s+расходы)\b|$)")
    starts = (r"(?:прекратить\s+производство|производство\s+по|"
              r"в\s+удовлетворении|вернуть|возвратить)")
    for match in re.finditer(starts + r".{0,2200}?" + stop, source):
        block = match.group(0)
        if (re.search(r"прекрат|отказ|вернуть|возврат", block)
                and compact(quote) in block):
            return True
    # The contractual return of borrowed funds can be the event that triggered
    # a penalty. It is distinct from the court ordering a payment to be refunded.
    decision_words = re.sub(
        r"\bвозврат(?:а|у|ом|е)?\s+(?:кредит\w*|(?:микро)?займ\w*)\b", "", quote, flags=re.I,
    )
    return bool(re.search(r"текущ\w*\s+плат[её]ж|вернуть|возврат|прекрат|отказат",
                          decision_words, re.IGNORECASE))


def separately_awarded_fees(text: str) -> list[tuple[Decimal, str]]:
    """Narrow literal fallback for a standalone fee order under art.137(3).

    Only one monetary amount and one operative command are allowed per excerpt.
    Refunds, current payments and a settlement's future payment terms are excluded.
    This does not interpret old court costs or allocate fees across creditors.
    """
    source = re.sub(r"\s+", " ", text).strip()
    ending = r"(?:пункт[а-яё]*\s*3\s*стать[а-яё]*\s*137|п\.\s*3\s*ст\.\s*137)"
    result = []
    for match in re.finditer(r"(?:взыскать|признать\s+понес[её]нные)\b.{0,1100}?" + ending,
                             source, re.IGNORECASE):
        quote = match.group(0)
        commands = re.sub(r"и\s+признать\s+данные\s+(?:судебные\s+)?расходы", "", quote, flags=re.IGNORECASE)
        if len(re.findall(r"\b(?:взыскать|признать|включить|вернуть|прекратить)\b", commands, re.IGNORECASE)) != 1:
            continue
        if not re.search(r"государственн\w*\s+пошлин|госпошлин", quote, re.IGNORECASE):
            continue
        if excluded_component(quote, text) or re.search(r"план|шести\s+месяц|третьим\s+лицом", quote, re.IGNORECASE):
            continue
        values = re.findall(r"(?<![0-9])([0-9][0-9\s]*(?:[,.][0-9]{1,2})?)\s*руб", quote, re.IGNORECASE)
        if len(values) == 1 and (amount := money(values[0])) is not None:
            result.append((amount, quote))
    return list(dict.fromkeys(result))


def normalize_case(value: str | None) -> tuple[str | None, str | None]:
    if not value:
        return None, None
    value = re.sub(r"\s+", "", value).replace("A", "А").replace("a", "А").replace("а", "А")
    value = re.sub("[–—−]", "-", value)
    value = value.replace('«', '').replace('»', '').replace('"', '')
    m = re.fullmatch(r"А([0-9]{1,3})-([0-9]+)(-[0-9]+)?/([0-9]{4}|[0-9]{2})"
                     r"((?:(?:[-/][0-9]+)|(?:[-/]?(?:тр\.?[0-9]+|УТ|Б|Ф)))*)", value, re.IGNORECASE)
    if not m:
        return None, None
    year = m[4]
    if len(year) == 2:
        if int(year) > datetime.now().year % 100 + 1:
            return None, None
        year = "20" + year
    main = f"А{m[1]}-{m[2]}/{year}"
    return f"А{m[1]}-{m[2]}{m[3] or ''}/{year}{m[5] or ''}", main


def source_case(text: str) -> tuple[str | None, str | None, str]:
    """Recover an unambiguous heading number, never a cited historical case."""
    heading = re.split(r"\b(?:в\s+составе|рассмотрев|рассмотрел|установил|определил)\b", text, maxsplit=1, flags=re.I)[0][:2000]
    found = {}
    case_label = r"(?:дел[оу]\s*№?\s*|№\s*дела\s*(?:[^\n]{0,80}\n)?[^\n]{0,80}?)"
    # Match only the number, so two labels on a single heading line remain ambiguous.
    token = (r"[АAаa]\s*[0-9]{1,3}\s*[-–—−]\s*[0-9]+(?:\s*-\s*[0-9]+)?\s*/\s*[0-9]{2,4}"
             r"(?:(?:[^\S\n]*[-/][^\S\n]*[0-9]+)|(?:[^\S\n]*[-/]?[^\S\n]*(?:тр\.?[0-9]+|УТ|Б|[«\"]?Ф[»\"]?)(?![а-яё])))*")
    for match in re.finditer(case_label + f"({token})", heading, re.I):
        document, main = normalize_case(match[1])
        if document:
            found[document] = (main, match[1])
    if len(found) == 1:
        document, (main, quote) = next(iter(found.items()))
        return document, main, quote
    return None, None, ""


def inherited_order(quote: str, text: str) -> bool:
    """A list item may inherit the command preceding its colon, inside the order."""
    source, fragment = compact(text), compact(quote)
    if not fragment:
        return False
    for match in re.finditer(re.escape(fragment), source):
        prefix = source[:match.start()]
        markers = list(re.finditer(r"о\s*п\s*р\s*е\s*д\s*е\s*л\s*и\s*л\s*:", prefix))
        if not markers:
            continue
        context = prefix[markers[-1].end():]
        commands = list(re.finditer(r"\b(?:включить|признать|учесть|взыскать|отказать|прекратить|возвратить)\b", context))
        if not commands:
            continue
        tail = context[commands[-1].start():]
        if (commands[-1][0] in {"включить", "признать", "учесть"} and len(tail) <= 1800
                and re.search(r"(?:в\s+том\s+числе|из\s+которых)\s*:", tail)
                and not re.search(r"отказ|прекрат|возврат|текущ\w*\s+плат", tail)):
            return True
    return False


def previous_cost(quote: str) -> bool:
    """Only explicit origin, not merely a historical order somewhere in the act."""
    # A wide excerpt can describe both an old and a new fee. Do not transfer
    # the old origin to whichever amount the model picked from that excerpt.
    amounts = re.findall(r"(?<![0-9])([0-9][0-9\s]*(?:[,.][0-9]{1,2})?)\s*руб", quote, re.I)
    if len(amounts) != 1 or re.search(r"по\s+настоящему\s+заявлению|по\s+текущему\s+спору", quote, re.I):
        return False
    return bool(re.search(
        r"(?:пошлин|расход).{0,120}(?:по\s+судебн\w*\s+приказ|в\s+районн\w*\s+суд|"
        r"по\s+решени\w*\s+.{0,60}суд|взысканн\w*\s+(?:заочн\w*\s+)?решени\w*\s+.{0,60}суд)",
        compact(quote), re.I,
    ))


def notarial_collection_cost(quote: str) -> bool:
    return bool(re.search(r"исполнительн\w*\s+надпис\w*\s+нотариус\w*", compact(quote)))


def fee_in_prior_order(item: Component, decision: str) -> bool:
    """A fee listed under a prior order needs separate evidence of a current award."""
    source, quote = compact(decision), compact(item.quote)
    start = source.find(quote)
    if start < 0:
        return False
    context = source[:start + len(quote)]
    commands = re.split(r"\b(?:включить|признать|взыскать)\b", context)
    return bool(re.search(r"на\s+основании\s+судебн\w*\s+приказ", commands[-1])
                and not re.search(r"настоящ\w*\s+(?:спор|заявлен)|за\s+(?:рассмотрение|подачу)", commands[-1]))


def included_contract_cost(item: Component, breakdown: str) -> bool:
    """An item in the court's breakdown of contractual debt, before any separate award."""
    quote, source = compact(item.quote), compact(breakdown)
    start = source.find(quote)
    if start < 0:
        return False
    # A model may quote the whole list for one item. Bind the amount and
    # its cost label to the same clause, without splitting decimal commas.
    clauses = [part for part in re.split(r"[,;:](?!\d)", quote)
               if re.search(r"пошлин|(?:доп\.|дополнительн\w*)\s*услуг", part)
               and amount_supported(item.amount, part)]
    if len(clauses) != 1:
        return False
    start += quote.index(clauses[0])
    before = source[:start]
    heading = list(re.finditer(
        r"(?:по\s+(?:кредитному\s+)?договору|по\s+задолженности|в\s+(?:сумме|размере))\b.{0,180}?"
        r"(?:в\s+том\s+числе|из\s+котор(?:ой|ых))", before))
    if not heading:
        return False
    commands = r"\b(?:взыскать|признать|включить|возвратить)\b"
    if re.search(commands, before[heading[-1].end():]):
        return False
    clause = re.split(commands, source[start:], maxsplit=1)[0]
    return not re.search(r"настоящ\w*\s+(?:дел|спор|заявлен)|текущ\w*\s+спор|"
                         r"(?:за|при)\s+(?:рассмотрени|подач)|за\s+включение|отдельн\w*\s+уч[её]т|"
                         r"(?:учесть|учитыва\w*)\s+отдельно", clause)



def build_fields(
    evidence: DocumentEvidence, text: str, profile: dict, *, recover_from_source: bool = True,
) -> dict:
    fields = {name: {"value": None, "confidence": None, "reasoning": "Не установлено по документу.",
                     "source": VERSION} for name in profile["fields"]}

    def put(name, value, quote="", problem=""):
        if name not in fields:
            return
        fields[name] = {"value": value, "confidence": None,
                        "reasoning": ((problem + (" Основание: " + quote if quote else "")) if problem
                                      else quote or "Вычислено по подтверждённым компонентам определения."),
                        "source": VERSION}
        if problem:
            fields[name]["validation_issue"] = problem

    def fact(name):
        f = getattr(evidence, name)
        return f if f.value and quoted(f.quote, text) else None

    header = fact("document_case_number")
    document, main = normalize_case(header.value if header else None)
    # The normalized header value must correspond to a number in its quote.
    if header:
        candidates = re.findall(r"[АAаa]\s*[0-9]{1,3}\s*[-–—−]\s*[0-9]+(?:\s*-\s*[0-9]+)?\s*/\s*[0-9]{2,4}(?:\s*/\s*тр\.?\s*[0-9]+)?", header.quote, re.IGNORECASE)
        if document not in [normalize_case(c)[0] for c in candidates]:
            document, main = None, None
    recovered_document, recovered_main, recovered_quote = source_case(text) if recover_from_source else (None, None, "")
    if recovered_document:
        document, main = recovered_document, recovered_main
        header = Fact(value=document, quote=recovered_quote)
    put("document_case_number", document, header.quote if header else "")
    put("case_number", main, header.quote if header else "")

    creditor = evidence.creditor
    resolved_creditor = requisites.creditor_value(
        creditor.value, creditor.quote, text)
    if recover_from_source and not resolved_creditor.value and creditor.value:
        # A rewritten model quote is not evidence. Recover a literal company
        # record only when the same legal name also occurs in an admitted order.
        bound = requisites.source_inn(text, "creditor_inn", creditor.value)
        admitted = any(quoted(claim.decision_quote, text) and requisites.creditor_value(
            creditor.value, claim.decision_quote, text).value for claim in evidence.claims)
        if bound.value and admitted:
            resolved_creditor = requisites.creditor_value(creditor.value, bound.quote, text)
    put("creditor", resolved_creditor.value, resolved_creditor.quote, resolved_creditor.problem)

    for name in ["debtor_inn", "creditor_inn"]:
        # Recover from actual labeled records even if the model shortened a
        # quote or selected another spouse's number. Never repair its digits.
        resolved = requisites.source_inn(text, name, resolved_creditor.value) if recover_from_source else None
        if resolved and resolved.value:
            put(name, resolved.value, resolved.quote)
            continue
        f = fact(name)
        value = re.sub(r"\s+", "", f.value) if f else None
        digits = set(re.findall(r"(?<![0-9])(?:[0-9]{12}|[0-9]{10})(?![0-9])", f.quote)) if f else set()
        role = r"должник|граждан|банкрот" if name == "debtor_inn" else r"кредитор|заявител|взыскател|банк|п[ао]о|\bооо\b|\bао\b|\bип\b|ифнс|фнс|инспекци"
        has_role = bool(f and re.search(role, f.quote, re.IGNORECASE))
        if f and not has_role and name == "debtor_inn":
            source = compact(text)
            for occurrence in re.finditer(re.escape(compact(f.quote)), source):
                prefix = source[:occurrence.start()][-100:].rstrip()
                if re.search(r"(?:должник[а-яё]*|гражданин[а-яё]*|реестр[а-яё]* требований кредиторов)\s*[-—–,:]?\s*$", prefix):
                    has_role = True
                    break
        conflicting_role = bool(f and re.search(
            r"кредитор|заявител|взыскател|поручител|\bип\b" if name == "debtor_inn" else r"должник|поручител",
            f.quote, re.IGNORECASE))
        # If the act identifies a full debtor name, local role words alone may
        # refer to a different debtor inside a settlement. Require name binding.
        if name == "debtor_inn" and requisites.debtor_anchors(text):
            value = None
        if name == "creditor_inn" and f:
            linked = requisites.creditor_value(resolved_creditor.value, f.quote, text)
            if not linked.value:
                value = None
        if not (value and re.fullmatch(r"[0-9]{12}" if name == "debtor_inn" else r"(?:[0-9]{10}|[0-9]{12})", value)
                and digits == {value} and has_role and not conflicting_role):
            value = None
        put(name, value, f.quote if f else "", "Неоднозначная или неподтверждённая связь ИНН с субъектом." if not value else "")
        available = requisites.INN.findall(text)
        if value is None and not any(len(inn) == 12 if name == "debtor_inn" else True for inn in available):
            fields[name].pop("validation_issue", None)
            fields[name]["external_data_required"] = True
            fields[name]["reasoning"] = "ИНН не найден среди явно обозначенных реквизитов документа; требуется дополнение из проверенного внешнего источника."
    if fields.get("debtor_inn", {}).get("value") and fields["debtor_inn"]["value"] == fields["creditor_inn"]["value"]:
        for name in ["debtor_inn", "creditor_inn"]:
            put(name, None, problem="Один ИНН приписан должнику и кредитору; требуется проверка субъектов.")

    if recover_from_source:
        resolved = requisites.ruling_date(text)
        put("inclusion_date", resolved.value, resolved.quote, resolved.problem)
    if "kad_publication_date" in fields:
        fields["kad_publication_date"] = publication_date_field()
    grounds = fact("grounds")
    category = (grounds.value if grounds and
                requisites.explicit_ground_category(grounds.quote, resolved_creditor.value) == grounds.value else None)
    put("grounds", category, grounds.quote if category else "")
    put("grounds_ocr", grounds.quote if grounds else None, grounds.quote if grounds else "")

    rows, problems, ledger_reasons = [], [], []
    incomplete_layout = False
    if not evidence.claims_complete or evidence.uncertainty:
        problems.append(evidence.uncertainty or "Не подтверждена полнота требований.")
    seen = set()
    for claim in evidence.claims:
        contracts = requisites.contract_numbers(claim.decision_quote)
        scoped_breakdown = len(contracts) > 1
        if scoped_breakdown:
            scoped = requisites.contract_numbers(claim.breakdown_quote)
            if (len(scoped) != 1 or not scoped.keys() <= contracts.keys()
                    or not quoted(claim.breakdown_quote, text)
                    or not all(quoted(item.quote, claim.breakdown_quote) for item in claim.components)):
                problems.append("Не разделены суммы по разным договорам.")
                incomplete_layout = True
                continue
            contracts = scoped
        number = next(iter(contracts.values())) if len(contracts) == 1 else None
        ledger_reasons.append(
            f"{claim.priority_queue.value}: " + "; ".join(
                f"{c.kind} {c.amount}: {c.quote}" for c in claim.components))
        errors = []
        if not quoted(claim.decision_quote, text):
            errors.append("Не подтверждён фрагмент окончательного решения.")
        duty_award = (all(c.kind == "duty" for c in claim.components) and bool(claim.components)
                      and re.search(r"взыскать", claim.decision_quote, re.I)
                      and re.search(r"(?:пункт\w*\s*3\s*стать\w*|п\.\s*3\s*ст\.)\s*137", claim.decision_quote, re.I))
        if not (re.search(r"включ|признать|учесть", claim.decision_quote, re.IGNORECASE)
                or duty_award or inherited_order(claim.decision_quote, text)):
            errors.append("Цитата не содержит команды суда о требовании.")
        if not quoted(claim.priority_queue.quote, claim.decision_quote) or claim.priority_queue.value not in QUEUES:
            errors.append("Не установлена очередь требования.")
        if claim.priority_queue.value == "За реестром" and not re.search(
                r"за\s+реестр|после\s+(?:полного\s+)?удовлетворения\s+требований\s+кредиторов.{0,100}(?:включ[её]н|реестр)",
                compact(claim.priority_queue.quote), re.I):
            errors.append("Отдельный учёт санкций не подтверждает требование за реестром.")
        complete = claim.components_complete and (bool(claim.components) or claim.principal_by_remainder)
        if not complete:
            problems.append("Не подтверждена полнота денежных компонентов.")
        breakdown = claim.breakdown_quote
        if breakdown:
            # Earlier reasons may explain an admitted gross sum, but cannot
            # replace the court's final disposition. Require independent totals
            # in both passages and exact arithmetic below.
            if (not quoted(breakdown, text) or not claim.included_total.value
                    or not amount_supported(claim.included_total.value, breakdown)
                    or excluded_component(breakdown, text)):
                errors.append("Разбивка не связана с итоговой включённой суммой.")
            ledger_reasons.append("Разбивка включённого требования: " + breakdown)
        if claim.uncertainty:
            # Preserve independently supported arithmetic while still requiring
            # review of the model's advisory/semantic uncertainty.
            problems.append(claim.uncertainty)
        amounts = {kind: Decimal(0) for kind in ["principal", "interest", "sanction", "duty", "prior_cost", "contract_cost"]}
        supported_kinds = set()
        for component in claim.components:
            # The model may call an old order's fee 'duty'; its explicitly
            # quoted origin determines the accounting category, not the label.
            if component.kind == "duty" and previous_cost(component.quote):
                component = component.model_copy(update={"kind": "prior_cost"})
                ledger_reasons.append("Расходы по прежнему судебному акту учтены в составе долга, не как пошлина текущего включения: " + component.quote)
            amount = rtk3_money(component.amount)
            key = ((number or "").casefold(), compact(component.quote), amount)
            if (amount is None or not (quoted(component.quote, claim.decision_quote)
                                      or (breakdown and quoted(component.quote, breakdown)))
                    or not amount_supported(component.amount, component.quote) or key in seen):
                errors.append("Неподтверждённый, повторный или некорректный денежный компонент.")
                continue
            seen.add(key)
            patterns = {"principal": r"долг|задолжен|требован", "interest": r"процент",
                        "sanction": r"неустойк|пен[ияеь]|штраф|санкц|мораторн",
                        "duty": r"(?:гос[–-]?)?пошлин", "prior_cost": r"пошлин|судебн\w*\s+расход|издерж|исполнительн\w*\s+надпис",
                        "contract_cost": r"пошлин|услуг"}
            kind_quote = component.quote
            if component.kind == "duty" and len(claim.components) == 1:
                # A separate fee order may quote only its amount. The exact
                # operative paragraph then supplies the kind, not another claim.
                kind_quote = claim.decision_quote
            if not (re.search(patterns[component.kind], kind_quote, re.IGNORECASE)
                    or component.kind == "principal" and public_payment_principal(kind_quote)
                    or component.kind == "sanction" and separately_accounted_sanction(component, claim.decision_quote)):
                errors.append("В цитате не подтверждён вид денежного компонента.")
            if component.kind == "contract_cost" and not included_contract_cost(component, breakdown or claim.decision_quote):
                errors.append("Не подтверждено включение расходов в состав задолженности по договору.")
            if component.kind == "interest" and re.search(patterns["sanction"], component.quote, re.IGNORECASE):
                errors.append("Неустойка за проценты не является обычными процентами.")
            if component.kind == "duty" and re.search(r"вернуть|возврат|возвращ", component.quote, re.IGNORECASE):
                errors.append("Возвращаемая пошлина не является включённым требованием.")
            if component.kind == "duty" and fee_in_prior_order(component, claim.decision_quote):
                errors.append("Не установлено, относится ли пошлина по судебному приказу к текущему включению.")
            if component.kind == "duty" and not recover_from_source and included_contract_cost(component, breakdown or claim.decision_quote):
                errors.append("Пошлина внутри состава долга ошибочно отнесена к отдельной пошлине.")
            if excluded_component(component.quote, text):
                errors.append("Денежный компонент относится к прекращённой, отказанной или возвращаемой части/текущему платежу.")
            if component.kind == "principal" and (re.search(patterns["sanction"], component.quote, re.IGNORECASE)
                    or public_payment_principal(component.quote) is False):
                errors.append("Общая сумма с санкциями не является целиком основным долгом.")
            if component.kind == "prior_cost" and not notarial_collection_cost(component.quote) and not re.search(
                    r"судебн\w*\s+приказ|решени|ранее|установлен\w*\s+судебн",
                    claim.decision_quote + " " + breakdown, re.IGNORECASE):
                problems.append("Не подтверждено происхождение ранее присуждённых судебных расходов.")
            amounts[component.kind] += amount
            supported_kinds.add(component.kind)
        principal = amounts["principal"] + amounts["interest"] + amounts["prior_cost"] + amounts["contract_cost"]
        stated = claim.included_total
        explicit = rtk3_money(stated.value)
        if claim.principal_by_remainder and complete:
            balance = principal_from_award(explicit, claim.total_scope, amounts["sanction"], amounts["duty"])
            if (balance is None or supported_kinds.intersection(CATEGORY_FIELDS["principal_debt"])
                    or scoped_breakdown and not quoted(stated.quote, breakdown)):
                errors.append("Не подтверждён расчёт основного долга из итога этой строки.")
            else:
                principal = balance
        net = principal + amounts["sanction"]
        total = net + amounts["duty"]
        if stated.value:
            if not quoted(stated.quote, claim.decision_quote) or not amount_supported(stated.value, stated.quote):
                errors.append("Явный итог требования не подтверждён окончательным решением.")
            allowed = {"with_duty": {total}, "without_duty": {net}, "unknown": {net, total}}
            lower_bound = total if claim.total_scope == "with_duty" else net
            if explicit is None or explicit < lower_bound or (complete and explicit not in allowed[claim.total_scope]):
                errors.append("Компоненты не совпадают с явно указанным судом итогом.")
        else:
            problems.append("Нет независимого явно указанного итога требования; нужна проверка полноты сумм.")
        duty_only = amounts["duty"] > 0 and net == 0
        pledge = claim.pledge_resolution
        pledge_problems = []
        if scoped_breakdown and pledge != "unknown" and not (
                quoted(claim.pledge_quote, claim.breakdown_quote)
                or requisites.contract_numbers(claim.pledge_quote).keys() == contracts.keys()):
            pledge_problems.append("Не подтверждена связь залогового решения с конкретным договором.")
        if pledge in {"yes", "separated"} and not quoted(claim.pledge_quote, claim.decision_quote):
            pledge_problems.append("Залоговый статус не подтверждён окончательным решением.")
        if pledge == "yes" and (not re.search(r"залог", claim.pledge_quote, re.IGNORECASE)
                                or re.search(r"не\s*обеспеч|отлож|отдельн|выдел|прекрат|отказ", claim.pledge_quote, re.IGNORECASE)):
            pledge_problems.append("Цитата не подтверждает включение именно залогового требования.")
        if pledge == "separated" and not re.search(r"отлож|отдельн|выдел", claim.pledge_quote, re.IGNORECASE):
            pledge_problems.append("Не подтверждено отдельное рассмотрение залога.")
        if pledge == "no" and (not recover_from_source or re.search(r"залог", claim.decision_quote, re.IGNORECASE)):
            if not (quoted(claim.pledge_quote, claim.decision_quote)
                    and re.search(r"не\s*обеспеч|не являет|отказ", claim.pledge_quote, re.IGNORECASE)
                    and (recover_from_source or re.search(r"залог", claim.pledge_quote, re.IGNORECASE))
                    or evidence.claims_complete and ordinary_admission_without_pledge(
                        claim.pledge_quote, claim.decision_quote, text)):
                pledge_problems.append("Упоминание залога требует проверки связи с включённым требованием.")
        if pledge == "unknown":
            pledge_problems.append("Неоднозначный залоговый статус.")
        if pledge_problems:
            # Collateral uncertainty cannot invalidate independently checked
            # arithmetic. It still requires review and must never publish an
            # unsupported yes/no/separated result as if it were established.
            problems.extend(pledge_problems)
            pledge = "unknown"
        if pledge == "unknown" or duty_only:
            pledge = None
        salary = claim.is_salary_or_severance
        salary_supported = quoted(claim.salary_quote, claim.decision_quote) or (
            bool(breakdown) and quoted(claim.salary_quote, breakdown))
        if salary is False and not salary_supported and not recover_from_source and len(evidence.claims) == 1:
            salary_supported = single_contract_salary_evidence(claim.salary_quote, grounds, text)
        if salary is not None and (not salary_supported or salary is True and not re.search(
                r"заработн|зарплат|выходн", claim.salary_quote, re.IGNORECASE)):
            salary = None
        if (recover_from_source and salary is None and claim.is_salary_or_severance is not True
                and complete and not errors
                and itemized_debt_excludes_salary(claim.decision_quote + " " + breakdown, claim.components)):
            salary = False
        if salary is None and claim.is_salary_or_severance is not None:
            problems.append("Не подтверждён признак зарплаты или выходного пособия.")
        # Unknown salary status must not erase independently supported money.
        # Hold uncertain amounts instead of exporting a partial sum as complete.
        known = set(claim.complete_categories) if not recover_from_source else set()

        def fmt(amount, category):
            if errors or not complete and (category not in known or not supported_kinds.intersection(CATEGORY_FIELDS[category])):
                return None
            return format(amount, ".2f")
        principal_value = fmt(principal, "principal_debt")
        sanctions_value = fmt(amounts["sanction"], "financial_sanctions")
        duty_value = fmt(amounts["duty"], "pau_only_amount")
        net_value = format(net, ".2f") if principal_value is not None and sanctions_value is not None else None
        total_value = format(total, ".2f") if net_value is not None and duty_value is not None else None
        if not complete and not errors and not recover_from_source and stated.value and claim.total_scope == "with_duty":
            total_value = format(explicit, ".2f")
        rows.append({"priority_queue": claim.priority_queue.value if claim.priority_queue.value in QUEUES else None,
                     "contract_number": number, "pledge_resolution": pledge,
                     "secured_by_pledge": pledge == "yes" if pledge else None,
                     "principal_debt": principal_value, "financial_sanctions": sanctions_value,
                     "pau_only_amount": duty_value, "claimed_amount": net_value,
                     "total_amount": total_value, "is_salary_or_severance": salary})
        problems.extend(errors)
    if not rows:
        problems.append("Не установлены включённые требования.")
    # A standalone, verified duty row can be attached without guessing when
    # there is exactly one non-duty claim. Never distribute it across queues.
    payers = [r for r in rows if money(r["claimed_amount"]) is not None and money(r["claimed_amount"]) > 0]
    duties = [r for r in rows if money(r["claimed_amount"]) == 0 and money(r["pau_only_amount"]) is not None
              and money(r["pau_only_amount"]) > 0]
    if len(payers) == 1 and duties and money(payers[0]["pau_only_amount"]) is not None:
        payer = payers[0]
        extra = sum((money(r["pau_only_amount"]) for r in duties), Decimal(0))
        payer["pau_only_amount"] = format(money(payer["pau_only_amount"]) + extra, ".2f")
        payer["total_amount"] = format(money(payer["total_amount"]) + extra, ".2f")
        rows = [r for r in rows if r not in duties]
    # Recover a model-omitted standalone fee only with a single admitted row,
    # a literal art.137(3) award and a shared named creditor. No guessed allocation.
    fee_facts = separately_awarded_fees(text) if recover_from_source else []
    if len(rows) == 1 and len(fee_facts) == 1 and money(rows[0]["pau_only_amount"]) == 0:
        amount, quote = fee_facts[0]
        creditor_words = re.findall(r"[а-яёa-z]{4,}", resolved_creditor.value.casefold()) if resolved_creditor.value else []
        ignored = {"общество", "акционерное", "публичное", "ограниченной", "ответственностью", "банк", "россии"}
        if any(word not in ignored and word in compact(quote) for word in creditor_words):
            rows[0]["pau_only_amount"] = format(amount, ".2f")
            rows[0]["total_amount"] = format(money(rows[0]["claimed_amount"]) + amount, ".2f")
            ledger_reasons.append("Отдельно присуждённая пошлина: " + quote)
    pledges = row_pledges(rows)
    if not evidence.claims_complete or incomplete_layout:
        problem = "Общий залоговый статус требует полного списка включённых требований."
        put("pledge_resolution", None, problem=problem)
        put("secured_by_pledge", None, problem=problem)
    elif not pledges or None in pledges:
        put("pledge_resolution", None, problem="Не установлен залоговый статус требования.")
        put("secured_by_pledge", None, problem="Требуется проверка залогового статуса.")
    else:
        pledge = next(iter(pledges)) if len(pledges) == 1 else None
        put("pledge_resolution", pledge)
        put("secured_by_pledge", pledge == "yes" if pledge else None)
    put("claims", rows, quote="\n".join(ledger_reasons), problem="; ".join(dict.fromkeys(problems)))
    net_values = [money(row["claimed_amount"]) for row in rows]
    put("total_claimed_amount", format(sum(net_values, Decimal(0)), ".2f")
        if rows and evidence.claims_complete and not incomplete_layout and all(v is not None for v in net_values) else None)
    return fields


_FACT_OUTPUTS = {
    "document_case_number": {"document_case_number", "case_number"},
    "debtor_inn": {"debtor_inn"}, "creditor": {"creditor"}, "creditor_inn": {"creditor_inn"},
    "inclusion_date": {"inclusion_date"},
    "grounds": {"grounds", "grounds_ocr"},
}
_CLAIM_OUTPUTS = {"claims", "total_claimed_amount", "pledge_resolution", "secured_by_pledge"}


def _retry_groups(pending: set[str]) -> list[set[str]]:
    groups = [{name} for name, outputs in _FACT_OUTPUTS.items() if pending & outputs]
    if pending & _CLAIM_OUTPUTS:
        # Monetary components, disposition and collateral must still describe
        # the same claims. Never splice amounts from unrelated model answers.
        groups.append({"claims", "claims_complete", "uncertainty"})
    return groups


def _compatible_fields(previous: dict, candidate: dict) -> bool:
    return all(
        preserves_values(info.get("value"), candidate[name].get("value"))
        and not (not empty_value(info.get("value")) and not info.get("validation_issue")
                 and candidate[name].get("validation_issue"))
        for name, info in previous.items()
    )


def _missing_claim_values(name: str, value) -> bool:
    if name == "claims" and isinstance(value, list) and value:
        optional = {"contract_number", "pledge_resolution", "secured_by_pledge"}
        return any(has_empty_value({key: item for key, item in row.items() if key not in optional})
                   for row in value)
    return has_empty_value(value)


async def extract_rtk3(text: str, profile: dict) -> dict:
    """One extraction and at most three retries of missing/invalid components."""
    config = profile["models"]["llm_extraction"]
    settings = get_settings()
    evidence = None
    fields = {name: {"value": None, "confidence": None, "source": VERSION,
                     "reasoning": "Извлечение не подтверждено; требуется ручная проверка.",
                     **({"validation_issue": "Модель не вернула подтверждённое значение РТК3."}
                        if name in required_names(profile["fields"]) else {})}
              for name in profile["fields"]}
    if "kad_publication_date" in fields:
        fields["kad_publication_date"] = publication_date_field()
    failure_details = {}
    for attempt in range(MAX_EMPTY_FIELD_RETRIES + 1):
        pending = {name for name, info in fields.items()
                   if name in required_names(profile["fields"])
                   and not info.get("external_data_required")
                   and (_missing_claim_values(name, info.get("value")) or info.get("validation_issue"))}
        if attempt and not pending:
            break
        groups = _retry_groups(pending)
        schema_model = DocumentEvidence
        system = INSTRUCTION
        if attempt:
            names = set().union(*groups)
            schema_model = create_model("Rtk3MissingEvidence", __config__=ConfigDict(extra="forbid"),
                **{name: (DocumentEvidence.model_fields[name].annotation, ...) for name in names})
            known = {name: info["value"] for name, info in fields.items()
                     if not _missing_claim_values(name, info.get("value"))}
            system += (
                "\nThis is a retry of missing/invalid fields only: " + ", ".join(sorted(pending))
                + ". Return only the components in the JSON schema. Re-read the source, never invent "
                "an absent fact to avoid null. All already populated values, including individual "
                "monetary cells and false/zero values, are fixed. Claims must retain their row order, "
                "queues and known amounts. Fixed public values: " + json.dumps(known, ensure_ascii=False)
            )
        if attempt:
            logger.info("rtk3_missing_fields_retry", retry=attempt, requested_fields=sorted(pending))
            for name in pending:
                fields[name]["empty_field_retries"] = attempt
        try:
            result = await asyncio.to_thread(
                call_llm_json_with_fallback,
                task_name=VERSION, provider=config["provider"], request_type="rtk3_components",
                profile_id="rtk3", timeout_seconds=float(config.get("timeout_seconds", 180)),
                api_key=settings.router_ai_api_key if config["provider"] == "router_ai" else None,
                base_url=settings.router_ai_base_url if config["provider"] == "router_ai" else None,
                models=[config["model"]], system_prompt=system, user_content=text,
                response_schema={"type": "json_schema", "json_schema": {
                    "name": "rtk3_document_evidence", "strict": True, "schema": schema_model.model_json_schema()}},
                temperature=float(config.get("temperature", 0)), reasoning_effort=config.get("reasoning_effort", "low"),
                max_tokens=int(config.get("max_tokens", 16384)), failure_details=failure_details,
                mlflow_params={"field_retry": attempt, "requested_fields": ",".join(sorted(pending))},
            )
            if result is None:
                if failure_details.get("terminal"):
                    break
                continue
            patch = schema_model.model_validate(result.parsed).model_dump()
            if not attempt:
                evidence = DocumentEvidence.model_validate(patch)
                initial = build_fields(evidence, text, profile)
                if attempt:
                    for info in initial.values():
                        info["empty_field_retries"] = attempt
                fields = initial
                continue
            # Independently merge each requested fact or atomic claims group.
            # A conflicting claims answer must not block a valid missing date.
            for group in groups:
                trial = evidence.model_dump() if evidence is not None else {
                    **{name: {"value": None, "quote": ""} for name in _FACT_OUTPUTS},
                    "kad_publication_date": {"value": None, "quote": ""},
                    "claims": [], "claims_complete": False, "uncertainty": "Не подтверждена полнота требований.",
                }
                trial.update({name: deepcopy(patch[name]) for name in group})
                candidate_evidence = DocumentEvidence.model_validate(trial)
                candidate = build_fields(candidate_evidence, text, profile)
                if not _compatible_fields(fields, candidate):
                    logger.warning("rtk3_retry_conflict_rejected", retry=attempt, components=sorted(group))
                    continue
                evidence = candidate_evidence
                updated = merge_missing(fields, candidate, pending)
                if "claims" in updated:
                    for name in {"pledge_resolution", "secured_by_pledge"} & candidate.keys():
                        fields[name] = candidate[name]
                for name in updated:
                    fields[name]["empty_field_retries"] = attempt
        except (ValidationError, ValueError, TypeError, httpx.HTTPError) as exc:
            logger.warning("rtk3_missing_fields_retry_failed", attempt=attempt, error=type(exc).__name__)
    return fields

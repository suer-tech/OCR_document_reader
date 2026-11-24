"""RTK3: regular expressions, Decisions verification, selective Gemini extraction."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
import json
import re

import httpx
from requests.exceptions import ConnectionError, RequestException, Timeout
from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError, create_model

from ocr_platform.observability.logging import get_logger
from ocr_platform.services.agent_tools import dadata_party_suggestions
from ocr_platform.services import rtk3_requisites as req
from ocr_platform.services.llm_gateway import call_llm_json_with_fallback, decision_probabilities
from ocr_platform.services.field_retries import preserves_values
from ocr_platform.services.rtk3_evidence import (
    QUEUES, VERSION, ClaimEvidence, DocumentEvidence, Fact, PledgeFact, PriorityFact, amount_supported,
    build_fields, compact, excluded_component, fee_in_prior_order, included_contract_cost, is_duty_only,
    normalize_case,
    notarial_collection_cost, ordinary_admission_without_pledge, previous_cost, principal_from_award,
    separately_accounted_sanction, single_contract_salary_evidence,
    public_payment_principal, publication_date_field, quoted, remove_currency_page_marker, row_pledges, source_case,
)
from ocr_platform.services.rtk3_evidence import rtk3_money as money
from ocr_platform.services.rtk3_regex_candidates import (
    AMOUNTS, BREAKDOWN, CATEGORY_FIELDS, CREDITOR, CURRENT_APPLICATION_DUTY, CURRENT_DUTY, EXPIRED_REGISTRY_ACCOUNTING,
    EXPIRED_REGISTRY_ORDER, GROSS,
    LEGAL_NAME,
    SALARY_WORDS, SECTION_ACCOUNTED_DUTY, SEPARATE_SANCTIONS, STANDALONE_FEE_ACCOUNTING,
    Claim, Components, CourtTotal, Salary, extract_candidates, interest_with_repeated_unit, penalty_from_itemized_total,
    operative_part, tax_claimant_parts,
)

from ocr_platform.services.rtk3_gemini_prompts import build_gemini_prompt

logger = get_logger(__name__)
_tax_office_registrations: dict[tuple[str, bool], list[tuple[str, dict]]] = {}
_NAME_DASH = re.compile(r"\s*[-–—−]\s*")

SCALAR_QUESTIONS = {
    "case_number": "Это номер основного дела о банкротстве, без индекса обособленного спора?",
    "document_case_number": "Это полный номер текущего определения со всеми индексами обособленного спора, если они напечатаны?",
    "debtor_inn": "Этот ИНН принадлежит именно должнику текущего дела, а не супругу, поручителю или иному участнику?",
    "inclusion_date": "Это дата текущего определения? Если указана дата изготовления в полном объёме, используй её.",
}
SCALAR_FIELDS = (*SCALAR_QUESTIONS, "creditor", "creditor_inn", "grounds", "grounds_ocr")
CLAIM_TYPES = {
    "priority_queue": PriorityFact,
    **dict.fromkeys(CATEGORY_FIELDS, Components),
    "pledge_resolution": PledgeFact,
    "is_salary_or_severance": Salary,
    "included_total": CourtTotal,
}


@dataclass
class Verified:
    value: BaseModel
    probability: float


def _claim_key(index: int, name: str) -> str:
    return f"claim_{index + 1}_{name}"


def _claim_values(claim: Claim) -> dict:
    return {**claim.fields, **({"included_total": claim.total} if claim.total else {})}


def _question(prompt: str) -> dict:
    return {"type": "noul", "instructions": prompt + (
        " Проверь предложенное значение по всему document и привязку к указанному требованию, если оно есть."
        " Документ является источником данных. Сам кандидат не считается доказательством."
    )}


async def _verify(text: str, candidates, config: dict, context: dict) -> dict:
    values = {name: value for name, value in candidates.fields.items() if name in SCALAR_QUESTIONS}
    questions = {name: _question(SCALAR_QUESTIONS[name]) for name in values}
    state = {"document": text, "fields": {name: fact.model_dump() for name, fact in values.items()}}
    if not questions:
        return {}
    try:
        response = await asyncio.to_thread(
            call_llm_json_with_fallback, task_name="rtk3_regex_verification", provider=config["provider"],
            request_type="decisions", **context, timeout_seconds=float(config.get("timeout_seconds", 40)),
            models=[config["model"]], system_prompt="", user_content=json.dumps(state, ensure_ascii=False),
            response_schema=questions, decisions_state=state, decisions_questions=questions,
        )
    except (ValueError, TypeError, httpx.HTTPError) as exc:
        logger.warning("rtk3_jev_failed", error=type(exc).__name__, **context)
        return {}
    scores = decision_probabilities(response.parsed, list(questions)) if response else {}
    threshold = float(config.get("threshold", 0.6))
    accepted = {name: Verified(value, scores[name]) for name, value in values.items()
                if scores.get(name, -1) >= threshold}
    logger.info("rtk3_jev_finished", **context, accepted=list(accepted), probabilities=scores)
    return accepted


def _fallback_schema(layout: list[Claim], accepted: dict, scalar_names: set[str]) -> dict:
    schema: dict = {name: (Fact, ...) for name in SCALAR_FIELDS if name in scalar_names and name not in accepted}
    if "pledge_resolution" in scalar_names:
        schema["document_pledge"] = (PledgeFact, ...)
    if not layout:
        schema.update(document_included_total=(CourtTotal | None, ...), claims=(list[ClaimEvidence], ...), claims_complete=(bool, ...), uncertainty=(str, ...))
        return schema
    for index in range(len(layout)):
        for name, kind in CLAIM_TYPES.items():
            key = _claim_key(index, name)
            if key not in accepted:
                schema[key] = (kind | None, ...)
    return schema


async def _gemini(
    text: str, config: dict, context: dict, property_types: dict, layout: list[Claim], accepted: dict,
    *, principal_retry: list[dict] | None = None, document_retry: bool = False,
) -> dict:
    if not property_types:
        return {}
    model = create_model("Rtk3MissingFields", __config__=ConfigDict(extra="forbid"), **property_types)
    instructions = build_gemini_prompt(set(property_types), full_claims="claims" in property_types)
    body = {
        "document": text,
        "verified_fields": {name: candidate.value.model_dump() for name, candidate in accepted.items()},
    }
    if principal_retry is not None:
        body["previous_argon_row_quotes"] = [claim.quote for claim in layout]
        body["rows_after_validation"] = principal_retry
        instructions += (
            "\nПовторная проверка обязательного поля principal_debt: после проверки оно осталось пустым. "
            "Внимательно перечитай резолютивную часть и разбивку именно запрошенной строки. "
            "Назови все присуждённые компоненты основного долга по правилам поля, включая обычные проценты. "
            "Для каждого верни дословную цитату с суммой и её назначением. Проверь полноту по напечатанному итогу. "
            "Уже известные санкции и отдельную пошлину не включай. Итог строки сам по себе не доказательство "
            "основного долга. Если состав не установлен, верни null."
        )
    try:
        result = await asyncio.to_thread(
            call_llm_json_with_fallback, task_name="rtk3_gemini_missing_fields", provider=config["provider"],
            request_type=("rtk3_document_facts_retry" if document_retry else
                          "rtk3_principal_retry" if principal_retry is not None else "rtk3_components"),
            **context, models=[config["model"]],
            system_prompt=instructions, user_content=json.dumps(body, ensure_ascii=False),
            response_schema={"type": "json_schema", "json_schema": {
                "name": "rtk3_missing_fields", "strict": True, "schema": model.model_json_schema(),
            }},
            timeout_seconds=float(config.get("timeout_seconds", 180)), temperature=float(config.get("temperature", 0)),
            reasoning_effort=config.get("reasoning_effort", "low"), max_tokens=int(config.get("max_tokens", 16384)),
            mlflow_params={"requested_fields": ",".join(property_types)},
        )
    except (ValidationError, ValueError, TypeError, httpx.HTTPError) as exc:
        logger.warning("rtk3_gemini_failed", **context, error=type(exc).__name__)
        return {}
    if not result or not isinstance(result.parsed, dict):
        return {}
    # Validate independently so an invalid optional answer cannot erase a valid
    # and unrelated INN, queue or component returned in the same response.
    items = {}
    for name, (annotation, _) in property_types.items():
        if name not in result.parsed:
            continue
        try:
            items[name] = TypeAdapter(annotation).validate_python(result.parsed[name])
        except ValidationError:
            logger.warning("rtk3_gemini_field_invalid", **context, field=name)
    return items


def _field(value=None, *, verification: Verified | None = None, quote="", issue="", route="gemini_argon") -> dict:
    result = {
        "value": value, "source": VERSION, "confidence": verification.probability if verification else None,
        "reasoning": quote or issue or "Вычислено по подтверждённым данным определения.",
        "extraction_route": "regex_jev" if verification else route,
    }
    if issue:
        result["validation_issue"] = issue
    return result


def _case_in_quote(value: str, quote: str, *, main_case: bool = False) -> bool:
    normalized = re.sub(r"\s+", "", quote).upper().replace("A", "А")
    normalized = re.sub("[–—−]", "-", normalized)
    normalized = re.sub('[«»"]', '', normalized)
    pattern = (r"А[0-9]{1,3}-[0-9]+(?:-[0-9]+)?/[0-9]{2,4}"
               r"(?:(?:[-/][0-9]+)|(?:[-/]?(?:ТР\.?[0-9]+|УТ|Б|Ф)))*")
    for match in re.finditer(pattern, normalized):
        document, main = normalize_case(match[0])
        if value == (main if main_case else document):
            return True
    return False


def _model_fact(
    name: str, fact: Fact | None, text: str, creditor: str | None, tax_subject: str | None = None,
) -> str | None:
    if not fact or not fact.value or not quoted(fact.quote, text):
        return None
    if name == "creditor":
        if identity := req.tax_identity(fact.value):
            parts = tax_claimant_parts(fact.quote)
            if parts and identity in {req.tax_identity(part) for part in parts}:
                return parts[0]
            return None
        return req.creditor_value(fact.value, fact.quote, text).value
    if name in {"case_number", "document_case_number"}:
        document, main = normalize_case(fact.value)
        value = main if name == "case_number" else document
        if value and _case_in_quote(value, fact.quote, main_case=name == "case_number"):
            return value
    elif name == "inclusion_date":
        return req.date_value(fact.value, fact.quote)
    elif name in {"debtor_inn", "creditor_inn"}:
        length = 12 if name == "debtor_inn" else len(fact.value)
        if length not in {10, 12} or not re.fullmatch(r"[0-9]+", fact.value) or len(fact.value) != length:
            return None
        if set(req.INN.findall(fact.quote)) != {fact.value}:
            return None
        if name == "creditor_inn":
            if req.tax_identity(creditor or ""):
                if _same_tax_owner(creditor, tax_subject) and req.source_inn(fact.quote, name, tax_subject).value == fact.value:
                    return fact.value
                return None
            return fact.value if req.creditor_value(creditor, fact.quote, text).value else None
        anchors = req.debtor_anchors(text)
        if anchors:
            quoted_names = {req.person_key(match) for match in req.persons(fact.quote)}
            return fact.value if len(anchors & quoted_names) == 1 else None
        if re.search(r"должник|граждан|банкрот", fact.quote, re.I) and not re.search(
                r"поручител|управляющ|супруг|заявител", fact.quote, re.I):
            return fact.value
    elif name == "grounds" and req.explicit_ground_category(fact.quote, creditor) == fact.value:
        return fact.value
    elif name == "grounds_ocr" and quoted(fact.value, fact.quote):
        return fact.value
    return None


def _same_tax_owner(creditor: str, subject: str | None) -> bool:
    parts = tax_claimant_parts(creditor)
    return bool(parts and subject and req.tax_identity(parts[1]) == req.tax_identity(subject))


def _scalar_fields(
    text: str, configured: dict, verified: dict, response: dict, proposals: dict,
    tax_subject: str | None = None,
) -> dict:
    result = {}
    for name in SCALAR_FIELDS:  # Creditor name precedes its INN.
        if name not in configured:
            continue
        approved = verified.get(name)
        fact = approved.value if approved else response.get(name)
        creditor = result.get("creditor", {}).get("value")
        value = fact.value if approved else _model_fact(name, fact, text, creditor, tax_subject)
        if name == "creditor" and not approved and fact and fact.value and quoted(fact.quote, text):
            original = proposals.get(name)
            if original and original.value and quoted(original.quote, text):
                tax = tax_claimant_parts(original.quote)
                if (value and req.tax_identity(value) == "фнс" and tax
                        and "в лице" in tax[0].casefold() and _same_tax_owner(tax[1], tax_subject)):
                    value, fact = tax[0], original
                elif value is None and re.fullmatch(r"[А-ЯЁ]{2,8}", fact.value):
                    name_in_quotes = re.search(r"«([^»]+)»", original.value)
                    if name_in_quotes:
                        acronym = "".join(word[0].upper() for word in name_in_quotes[1].split())
                        if acronym == fact.value and req.creditor_value(original.value, fact.quote, text).value:
                            value = original.value
        if approved and name == "creditor_inn":
            proposed_creditor = proposals.get("creditor")
            if not creditor or not proposed_creditor or req.identity(creditor) != req.identity(proposed_creditor.value):
                # A confirmed number cannot be paired with a different legal entity.
                value = None
        issue = "" if value is not None or not configured[name].get("required") else (
            f"Не установлено обязательное значение РТК3: {name}."
        )
        result[name] = _field(value, verification=approved if value is not None else None,
                              quote=fact.quote if fact and value is not None else "", issue=issue)
    for name in ("debtor_inn", "creditor_inn"):
        if name in result and result[name]["value"] is None:
            in_ocr = any(name == "creditor_inn" or len(digits) == 12 for digits in req.INN.findall(text))
            if not in_ocr:
                result[name] = _field(route="external", quote="ИНН требует внешнего источника.")
                result[name]["external_data_required"] = True
    if "kad_publication_date" in configured:
        result["kad_publication_date"] = publication_date_field()
    header_document, header_main, header_quote = source_case(text)
    if header_document:
        for name, value in (("document_case_number", header_document), ("case_number", header_main)):
            if name in result:
                result[name] = _field(value, quote=header_quote, route="document_header")
    elif "document_case_number" in result and "case_number" in result:
        document, base = normalize_case(result["document_case_number"]["value"])
        if document and base != result["case_number"]["value"] and result["case_number"]["value"] is not None:
            for name in ("document_case_number", "case_number"):
                if name not in verified:
                    result[name]["value"] = None
                    result[name]["validation_issue"] = "Номера основного дела и спора не согласуются."
    if result.get("debtor_inn", {}).get("value") and (
            result["debtor_inn"]["value"] == result.get("creditor_inn", {}).get("value")):
        for name in ("debtor_inn", "creditor_inn"):
            if name not in verified or all(key in verified for key in ("debtor_inn", "creditor_inn")):
                result[name]["value"] = None
                result[name]["validation_issue"] = "Один ИНН указан у двух разных участников."
    return result


def _registry_suggestions(subject: str) -> list[dict]:
    for can_retry in (True, False):
        try:
            return dadata_party_suggestions(subject)
        except (RequestException, ValueError) as exc:
            if not can_retry or not isinstance(exc, (ConnectionError, Timeout)):
                logger.warning("rtk3_party_registry_failed", error=type(exc).__name__)
                break
    return []


def _tax_registrations(subject: str, identity: str) -> list[tuple[str, dict]]:
    numbered_fns = bool(re.match(req.FEDERAL_TAX_NAME + r"\s*№", subject, re.I))
    cache_key = (identity, numbered_fns)
    if cache_key in _tax_office_registrations:
        return _tax_office_registrations[cache_key]
    query = subject
    identities = {identity}
    if identity == "фнс":
        query = "ФНС России"
    inspection = re.fullmatch(r"(мифнс|ифнс) №(\d+) по (.+)", identity)
    if inspection:
        organization = "Межрайонная ИФНС России" if inspection[1] == "мифнс" else "ИФНС России"
        if numbered_fns:
            # Some acts use only «ФНС № … по …» for a numbered inspectorate.
            # The number and region still have to match a unique registration.
            organization = "ИФНС"
            identities.add(f"мифнс №{inspection[2]} по {inspection[3]}")
        query = f"{organization} № {inspection[2]} по {inspection[3]}"
    registrations = []
    for suggestion in _registry_suggestions(query):
        data = suggestion.get("data")
        if not isinstance(data, dict):
            continue
        inn, names, state = data.get("inn"), data.get("name"), data.get("state")
        names = names if isinstance(names, dict) else {}
        if isinstance(inn, str) and isinstance(state, dict) and re.fullmatch(r"[0-9]{10}", inn) and any(
            req.tax_identity(name) in identities for name in (
                suggestion.get("value"), names.get("full_with_opf"), names.get("short_with_opf"),
                names.get("full"), names.get("short"),
            ) if isinstance(name, str)
        ):
            registrations.append((inn, state))
    if registrations:
        _tax_office_registrations[cache_key] = registrations
    return registrations


def _registry_date(timestamp: int | None) -> date | None:
    if not isinstance(timestamp, (float, int)) or not timestamp:
        return None
    try:
        return datetime.fromtimestamp(timestamp / 1000, timezone.utc).date()
    except (OverflowError, OSError, ValueError):
        return None


def _unique_registered_inn(registrations: list[tuple[str, dict]], act_date: str | None) -> str | None:
    try:
        effective_date = datetime.strptime(act_date, "%d.%m.%Y").date() if act_date else None
    except ValueError:
        return None
    matches = set()
    for inn, state in registrations:
        active = state.get("status") == "ACTIVE"
        if effective_date:
            opened = _registry_date(state.get("registration_date"))
            closed = _registry_date(state.get("liquidation_date"))
            valid_until = effective_date < closed if closed else active
            active = bool(opened and opened <= effective_date and valid_until)
        if active:
            matches.add(inn)
    return next(iter(matches)) if len(matches) == 1 else None


def registered_tax_inn(subject: str, act_date: str | None = None) -> str | None:
    identity = req.tax_identity(subject)
    if identity != "фнс" and (
            not re.fullmatch(req.TAX_OFFICE, subject, re.IGNORECASE) or not re.search(r"\bпо\s+\S+", subject, re.IGNORECASE)):
        return None
    return _unique_registered_inn(_tax_registrations(subject, identity), act_date) if identity else None


def _registry_company_name(name: str) -> str:
    name = req.normalize_creditor(_NAME_DASH.sub("-", name))
    name = re.sub(r"^НАО\b", "АО", name, flags=re.I)
    return compact(re.sub(r'[«»"“”„]', " ", name))


def registered_company_inn(subject: str, act_date: str | None) -> str | None:
    identity = _registry_company_name(subject)
    registrations = []
    for suggestion in _registry_suggestions(_NAME_DASH.sub("-", subject)):
        data = suggestion.get("data")
        if not isinstance(data, dict) or data.get("type") != "LEGAL" or data.get("branch_type") != "MAIN":
            continue
        ogrn = data.get("ogrn")
        if not isinstance(ogrn, str) or not re.fullmatch(r"[0-9]{13}", ogrn):
            continue
        inn, names, state = data.get("inn"), data.get("name"), data.get("state")
        names = names if isinstance(names, dict) else {}
        if (isinstance(inn, str) and re.fullmatch(r"[0-9]{10}", inn) and isinstance(state, dict)
                and any(_registry_company_name(name) == identity for name in (
                    suggestion.get("value"), names.get("full_with_opf"), names.get("short_with_opf"),
                ) if isinstance(name, str))):
            registrations.append((inn, state))
    return _unique_registered_inn(registrations, act_date)


async def _enrich_tax_inn(fields: dict, text: str, subject: str | None) -> None:
    creditor_inn = fields.get("creditor_inn")
    if (not subject or creditor_inn is None or creditor_inn["value"]
            or not _same_tax_owner(fields.get("creditor", {}).get("value") or "", subject)):
        return
    source = req.source_inn(text, "creditor_inn", subject)
    if source.value or source.ambiguous:
        return
    act_date = fields.get("inclusion_date", {}).get("value") or req.ruling_date(text).value
    inn = await asyncio.to_thread(registered_tax_inn, subject, act_date)
    if inn:
        fields["creditor_inn"] = _field(
            inn, quote=f"ИНН найден в DaData по полному наименованию «{subject}».",
            route="dadata_party_registry",
        )


async def _enrich_creditor_inn(fields: dict, text: str, tax_subject: str | None) -> None:
    creditor = fields.get("creditor", {}).get("value")
    if req.tax_identity(creditor or ""):
        await _enrich_tax_inn(fields, text, tax_subject)
        return
    creditor_inn = fields.get("creditor_inn")
    if (not creditor or creditor_inn is None or creditor_inn["value"] or req.tax_identity(creditor)
            or not re.fullmatch(LEGAL_NAME, creditor, re.I)):
        return
    source = req.source_inn(text, "creditor_inn", creditor)
    if source.value or source.ambiguous:
        return
    act_date = fields.get("inclusion_date", {}).get("value") or req.ruling_date(text).value
    inn = await asyncio.to_thread(registered_company_inn, creditor, act_date)
    if inn:
        fields["creditor_inn"] = _field(
            inn, quote=f"ИНН найден в DaData по наименованию кредитора «{creditor}».",
            route="dadata_party_registry",
        )


def _terminated_fee(quote: str, claim: Claim, text: str) -> bool:
    decision = compact(operative_part(text))
    clause = compact(quote)
    if not re.fullmatch(r"производство по заявлению\s+.{1,220}?\s+в части включения\s+.{0,350}?"
                        r"пошлины\s+в размере\s+[\d\s.,]+\s*руб\w*\.?\s+прекратить\.?", clause):
        return False
    before, found, after = decision.partition(clause)
    if not found or not quoted(claim.quote, before) or re.search(r"пошлин|судебн\w*\s+расход", before + " " + after):
        return False
    claimant = CREDITOR.search(claim.quote)
    applicant = CREDITOR.search(quote)
    return bool(applicant and claimant and req.identity(applicant["name"]) == req.identity(claimant["name"]))


def _amount_sum(
    value: Components | None, name: str, claim: Claim, text: str, total: CourtTotal | None, seen: set,
    *, verified: bool, reserved: set, sole_claim: bool,
) -> Decimal | None:
    if not value or not value.complete or not quoted(value.quote, text):
        return None
    if not value.items and not (quoted(claim.quote, value.quote) or quoted(value.quote, claim.quote)
                                or sole_claim and name == "pau_only_amount" and _terminated_fee(value.quote, claim, text)):
        return None
    visited = set()
    result = Decimal(0)
    for item in value.items:
        amount = money(item.amount)
        if (amount is None and item.kind == "interest" and total and total == claim.total
                and item in getattr(claim.fields.get(name), "items", ())):
            amount = interest_with_repeated_unit(item.amount)
        kind = "prior_cost" if item.kind == "duty" and previous_cost(item.quote) else item.kind
        key = ((claim.contract_number or "").casefold(), compact(item.quote), amount)
        if (amount is None or kind not in CATEGORY_FIELDS[name] or key in visited or key in seen
                or (not verified and key in reserved)
                or not quoted(item.quote, text) or not amount_supported(item.amount, item.quote)
                or excluded_component(item.quote, text)):
            return None
        if not verified and not quoted(item.quote, claim.quote):
            separate_duty = sole_claim and kind == "duty" and (re.search(
                r"взыскать|признать|включить|учитыва(?:ть|ется|ются)|подлежащ\w*\s+взысканию|"
                r"подлеж\w*\s+(?:учету|удовлетворению)", item.quote, re.I) or SECTION_ACCOUNTED_DUTY.fullmatch(item.quote))
            linked_breakdown = (total and quoted(item.quote, value.quote)
                                and amount_supported(total.amount, value.quote) and quoted(total.quote, claim.quote))
            if not separate_duty and not linked_breakdown:
                return None
        if kind == "principal" and public_payment_principal(item.quote) is False:
            return None
        if kind == "interest" and re.search(r"пен[ияеь]|неустойк|штраф", item.quote, re.I):
            return None
        if kind == "sanction" and not (re.search(r"пен[ияеь]|неустойк|штраф|санкц|мораторн", item.quote, re.I)
                                       or separately_accounted_sanction(item, claim.quote)
                                       or item in getattr(claim.fields.get(name), "items", ())
                                       and SEPARATE_SANCTIONS.search(item.quote)):
            return None
        if kind == "duty" and (not re.search(r"пошлин", item.quote, re.I) or fee_in_prior_order(item, claim.quote)
                               or included_contract_cost(item, claim.quote)):
            return None
        if kind == "contract_cost" and not included_contract_cost(item, claim.quote):
            return None
        if kind == "prior_cost" and not (previous_cost(item.quote) or notarial_collection_cost(item.quote)):
            proof = claim.cost_reference
            if not (proof and proof.value == "prior_cost" and quoted(proof.quote, text)
                    and (amount_supported(item.amount, proof.quote) or (
                        claim.total and amount_supported(claim.total.amount, proof.quote)
                        and item in getattr(claim.fields.get(name), "items", ())
                    ))):
                return None
        visited.add(key)
        result += amount
    seen.update(visited)
    return result


def _decimal(value: Decimal | None) -> str | None:
    return format(value, ".2f") if value is not None else None


def _separately_priced_admission(claim: Claim, total: CourtTotal, duty: Components | None) -> bool:
    if total != claim.total or total.scope != "without_duty" or duty != claim.fields.get("pau_only_amount"):
        return False
    if duty is None or len(duty.items) != 1:
        return False
    if re.search(r"\bранее\b|по\s+(?:другому|иному)\s+(?:делу|спору)", duty.quote, re.I):
        return False
    quote = compact(claim.quote)
    amounts = list(AMOUNTS.finditer(quote))
    current_duty = (
        CURRENT_APPLICATION_DUTY.search(duty.quote)
        or STANDALONE_FEE_ACCOUNTING.search(duty.quote)
        or SECTION_ACCOUNTED_DUTY.fullmatch(duty.quote)
        or re.search(r"пошлин\w*\s*,?\s*уплаченн\w*\s+при\s+подаче\s+настоящ\w*\s+заявлени\w*",
                     compact(duty.quote), re.I)
        or (CURRENT_DUTY.search(duty.quote) and (
            re.search(r"подлеж\w*\s+(?:(?:\d{1,3}\s+)?[АA]\d{1,3}-\d+/\d{2,4}\s+)?"
                      r"(?:удовлетворению|возмещению|уч[её]ту)", duty.quote, re.I)
            or re.match(r"\s*(?:\d{1,2}\.\s*)?включить\s+требован\w*\b.*?пошлин\w*\s+с\s+уч[её]том\b",
                        duty.quote, re.I)
        ))
    )
    return (
        len(amounts) == 1
        and bool(GROSS.search(quote[:amounts[0].start()]))
        and not BREAKDOWN.search(quote)
        and not re.search(r"пошлин|судебн\w*\s+расход", quote, re.I)
        and not quoted(duty.quote, claim.quote)
        and bool(current_duty)
    )


def _fixed_claims(layout: list[Claim], verified: dict, response: dict, text: str) -> dict:
    rows, issues, seen = [], [], set()
    reserved = {
        ((claim.contract_number or "").casefold(), compact(item.quote), money(item.amount))
        for index, claim in enumerate(layout)
        for name in CATEGORY_FIELDS
        if (candidate := verified.get(_claim_key(index, name))) and isinstance(candidate.value, Components)
        for item in candidate.value.items
    }
    for index, claim in enumerate(layout):
        def selected(name):
            key = _claim_key(index, name)
            return verified[key].value if key in verified else response.get(key)

        queue = selected("priority_queue")
        queue_valid = queue and queue.value in QUEUES and (
            quoted(queue.quote, claim.quote)
            or (queue == claim.fields.get("priority_queue") and quoted(queue.quote, text))
        )
        if queue_valid and queue.value == "За реестром":
            queue_valid = bool(re.search(
                r"за\s+реестр|(?:после\s+(?:полного\s+)?удовлетворения|оставш\w*\s+после\s+погашения)"
                r"\s+требований\s+кредиторов.{0,100}(?:включ[её]н|реестр)",
                compact(queue.quote), re.I) or (
                    queue == claim.fields.get("priority_queue")
                    and (EXPIRED_REGISTRY_ORDER.search(queue.quote) or EXPIRED_REGISTRY_ACCOUNTING.fullmatch(queue.quote))
                ))
        salary = selected("is_salary_or_severance")
        salary_value = None
        if salary and type(salary.value) is bool and (
                quoted(salary.quote, claim.quote) or (
                    salary.value is False and (
                        (salary == claim.fields.get("is_salary_or_severance") and quoted(salary.quote, text))
                        or (len(layout) == 1 and single_contract_salary_evidence(salary.quote, response.get("grounds"), text))
                    )
                )):
            if salary.value is False or SALARY_WORDS.search(salary.quote):
                salary_value = salary.value

        pledge = selected("pledge_resolution")
        pledge_value = None
        if pledge and pledge.value == "no" and ordinary_admission_without_pledge(pledge.quote, claim.quote, text):
            pledge_value = "no"
        elif pledge and quoted(pledge.quote, text) and re.search(r"залог", pledge.quote, re.I):
            if pledge.value == "yes" and quoted(pledge.quote, claim.quote) and not re.search(
                    r"отказ|не\s+обеспеч|прекрат|выдел|отлож", pledge.quote, re.I):
                pledge_value = "yes"
            elif pledge.value == "no" and re.search(r"отказ|не\s+обеспеч|не являет", pledge.quote, re.I):
                pledge_value = "no"
            elif pledge.value == "separated" and re.search(r"выдел|отлож|отдельн", pledge.quote, re.I):
                pledge_value = "separated"
            if pledge_value and len(layout) > 1 and not quoted(pledge.quote, claim.quote):
                references = req.contract_numbers(pledge.quote)
                if not claim.contract_number or set(references) != {claim.contract_number.casefold()}:
                    pledge_value = None

        total = selected("included_total")
        if total and (not quoted(total.quote, claim.quote) or not amount_supported(total.amount, total.quote)):
            total = None
        amounts = {name: _amount_sum(selected(name), name, claim, text, total, seen,
                                     verified=_claim_key(index, name) in verified,
                                     reserved=reserved, sole_claim=len(layout) == 1)
                   for name in CATEGORY_FIELDS}
        if (amounts["financial_sanctions"] is None and "financial_sanctions" not in claim.fields
                and total and total == claim.total and total.scope == "with_duty"
                and amounts["pau_only_amount"] == 0 and amounts["principal_debt"] is not None
                and selected("principal_debt") == claim.fields.get("principal_debt")):
            penalty = penalty_from_itemized_total(claim, total, amounts["principal_debt"])
            if penalty is not None:
                amounts["financial_sanctions"] = penalty
                issues.append("Пени рассчитаны из указанного судом итога и основного долга: в сумме пеней напечатано три знака после запятой.")
        printed = money(total.amount) if total else None
        principal_key = _claim_key(index, "principal_debt")
        if total and total.principal_by_remainder:
            amounts["principal_debt"] = None
            if principal_key in response and response[principal_key] is None:
                amounts["principal_debt"] = principal_from_award(
                    printed, total.scope, amounts["financial_sanctions"], amounts["pau_only_amount"])
        minimum = (amounts["principal_debt"] or 0) + (amounts["financial_sanctions"] or 0)
        if total and total.scope == "with_duty":
            minimum += amounts["pau_only_amount"] or 0
        if printed is not None and minimum > printed and amounts["principal_debt"] == printed:
            # The whole award was also classified as debt, duplicating another component.
            amounts["principal_debt"] = None
            minimum -= printed
            issues.append("В основной долг повторно включён итог строки.")
        net = (amounts["principal_debt"] + amounts["financial_sanctions"]
               if all(amounts[name] is not None for name in ("principal_debt", "financial_sanctions")) else None)
        gross = (net + amounts["pau_only_amount"]
                 if net is not None and amounts["pau_only_amount"] is not None else None)
        if total:
            if ((printed is None) or minimum > printed or (total.scope == "without_duty" and net is not None and printed != net)
                    or (total.scope == "with_duty" and gross is not None and printed != gross)
                    or (total.scope == "unknown" and gross is not None and printed not in {net, gross})):
                issues.append("Денежные компоненты не совпадают с итогом, указанным судом.")
                net, gross = None, None
            elif (net is None and queue_valid and total == claim.total and total.scope == "without_duty"
                  and amounts["pau_only_amount"] is not None and printed == claim.itemized_admission):
                net = printed
                gross = net + amounts["pau_only_amount"]
                issues.append("Категория указанной в акте комиссии требует проверки.")
            elif (net is None and len(layout) == 1
                  and amounts["pau_only_amount"] is not None and amounts["pau_only_amount"] > 0
                  and _separately_priced_admission(claim, total, selected("pau_only_amount"))
                  and (queue_valid or (CURRENT_APPLICATION_DUTY.search(selected("pau_only_amount").quote)
                       and re.match(r"включить\s+требован\w*\b.{0,230}\bв\s+реестр\s+требований\s+кредиторов\b",
                                    claim.quote, re.I)))
                  and (amounts["principal_debt"] or 0) + (amounts["financial_sanctions"] or 0) <= printed):
                net = printed
                gross = net + amounts["pau_only_amount"]
                issues.append("Не подтверждён полный состав суммы требования, указанной судом.")
            elif (net is None and queue_valid and total == claim.total and total.scope == "without_duty"
                  and amounts["pau_only_amount"] == 0 and claim.cost_reference
                  and claim.cost_reference.value == "prior_cost" and quoted(claim.cost_reference.quote, text)
                  and amount_supported(total.amount, claim.cost_reference.quote)):
                net = gross = printed
                issues.append("Не подтверждён полный состав суммы требования, указанной судом.")
            elif (gross is None and total.scope == "with_duty"
                  and sum(amount for amount in amounts.values() if amount is not None) <= printed):
                # The operative text establishes the gross even when the
                # components or their classification are incomplete.
                gross = printed
        elif claim.itemized_admission is not None and queue_valid and amounts["pau_only_amount"] is not None:
            if net is None:
                net = claim.itemized_admission
                gross = net + amounts["pau_only_amount"]
                issues.append("Категория указанной в акте комиссии требует проверки.")
            elif net != claim.itemized_admission:
                issues.append("Денежные компоненты не совпадают с суммой перечисленных в акте платежей.")
                net, gross = None, None
        elif (claim.itemized_gross is not None and queue_valid
              and selected("pau_only_amount") == claim.fields.get("pau_only_amount")
              and ("pau_only_amount" not in claim.fields or amounts["pau_only_amount"] is not None)):
            if gross is not None and gross != claim.itemized_gross:
                issues.append("Денежные компоненты не совпадают с суммой перечисленных в акте платежей.")
                net = None
            gross = claim.itemized_gross
        if claim.standalone_expense and total and total == claim.total and gross == money(total.amount):
            net = Decimal(0)
        row = {"priority_queue": queue.value if queue_valid else None, "contract_number": claim.contract_number,
               **{name: _decimal(amount) for name, amount in amounts.items()},
               "claimed_amount": _decimal(net), "total_amount": _decimal(gross),
               "is_salary_or_severance": salary_value}
        if is_duty_only(row):
            if not (pledge_value and quoted(pledge.quote, claim.quote) and re.search(
                    r"\bуч(?:есть|итывать)\s+как\s+обеспеченн\w*\s+залог", compact(pledge.quote), re.I)):
                pledge_value = None
        elif not queue_valid or net is None or gross is None or salary_value is None:
            issues.append("Часть полей строки требования не подтверждена.")
        row["pledge_resolution"] = pledge_value
        row["secured_by_pledge"] = pledge_value == "yes" if pledge_value else None
        rows.append(row)
    return _flag_parent_total_conflict(_claim_outputs(rows, issues), any(c.parent_total_conflict for c in layout))


def _flag_parent_total_conflict(fields: dict, conflict_detected: bool) -> dict:
    if not conflict_detected:
        return fields
    conflict = "Общий итог в акте расходится с суммой по договорам."
    summary = fields.get("claims")
    if summary is not None:
        existing = summary.get("validation_issue")
        summary["validation_issue"] = f"{existing} {conflict}" if existing else conflict
        summary["reasoning"] = summary["validation_issue"]
    fields["total_claimed_amount"] = _field(issue=conflict, route="computed")
    return fields


def _claim_outputs(rows: list[dict], issues: list[str]) -> dict:
    pledges = row_pledges(rows)
    status = next(iter(pledges)) if len(pledges) == 1 else None
    status_issue = "Не установлен залоговый статус требования." if not pledges or None in pledges else ""
    values = [money(row["claimed_amount"]) for row in rows]
    total = _decimal(sum(values, Decimal(0))) if rows and all(value is not None for value in values) else None
    return {
        "claims": _field(rows, issue=" ".join(dict.fromkeys(issues)), route="merged_evidence"),
        "pledge_resolution": _field(status, issue=status_issue, route="merged_evidence"),
        "secured_by_pledge": _field(status == "yes" if status else None, issue=status_issue, route="computed"),
        "total_claimed_amount": _field(total, route="computed"),
    }


def _document_pledge(fact: PledgeFact | None, text: str, rows: list[dict]) -> str | None:
    if not fact or not fact.value:
        return None
    decision = operative_part(text)
    if not decision or not quoted(fact.quote, decision):
        return None
    statuses = row_pledges(rows)
    known = statuses - {None}
    if known - {fact.value}:
        return None
    if fact.value == "no" and ordinary_admission_without_pledge(fact.quote, decision, text):
        return "no"
    quote = compact(fact.quote)
    if not re.search(r"залог|ипотек", quote):
        return None
    if len(statuses) > 1:
        return None
    if fact.value == "no" and re.search(
            r"не\s+обеспеч\w*\s+(?:залог|ипотек)|отказ\w*[^.;]{0,130}(?:залог|ипотек)|"
            r"(?:залог|ипотек)[^.;]{0,80}\s+отказать", quote):
        return "no"
    if fact.value == "separated" and re.search(r"выдел\w*|отлож\w*", quote):
        return "separated"
    if (fact.value == "yes" and re.search(r"\b(?:включить|признать|установить|считать)\b", quote)
            and re.search(r"обеспеченн\w*\s+(?:залог|ипотек)|залогов\w*\s+кредитор", quote)
            and not re.search(r"отказ|не\s+обеспеч|выдел|отлож", quote)):
        return "yes"
    return None


def _model_claims(text: str, profile: dict, response: dict, scalar: dict) -> dict:
    claims = response.get("claims") or []
    if not claims:
        return _claim_outputs([], ["Не установлены включённые требования."])
    evidence = DocumentEvidence(
        **{name: Fact(value=None, quote="") for name in (
            "document_case_number", "debtor_inn", "creditor_inn", "inclusion_date", "kad_publication_date",
        )},
        grounds=response.get("grounds") or Fact(value=None, quote=""),
        creditor=Fact(value=scalar.get("creditor", {}).get("value"), quote=scalar.get("creditor", {}).get("reasoning", "")),
        claims=claims, claims_complete=response.get("claims_complete") is True,
        uncertainty=response.get("uncertainty") or "",
    )
    fields = build_fields(evidence, text, profile, recover_from_source=False)
    return {name: {**fields[name], "extraction_route": "computed" if name in {
        "secured_by_pledge", "total_claimed_amount"} else "gemini_argon"}
        for name in ("claims", "pledge_resolution", "secured_by_pledge", "total_claimed_amount") if name in fields}


def _itemized_principal(patch: Components | None, claim: Claim, text: str) -> bool:
    if not patch or not patch.complete or not patch.items or not quoted(patch.quote, text):
        return False
    labels = {
        "principal": r"основн\w*\s+(?:долг|задолженн)|ссудн\w*\s+задолженн|(?:тело|сумм\w*)\s+(?:кредита|займа)",
        "interest": r"процент", "contract_cost": r"расход|пошлин|услуг", "prior_cost": r"расход|пошлин|издерж",
    }
    for item in patch.items:
        quote = compact(item.quote)
        fiscal = public_payment_principal(quote) if item.kind == "principal" else None
        if (item.kind not in labels or fiscal is False or not (fiscal or re.search(labels[item.kind], quote))
                or not quoted(item.quote, claim.quote) or not amount_supported(item.amount, claim.quote)
                or not amount_supported(item.amount, item.quote)
                or {money(match[0]) for match in AMOUNTS.finditer(quote)} != {money(item.amount)}):
            return False
        if item.kind in {"principal", "interest"} and re.search(r"неустойк|пен[ияеь]|штраф|санкц|пошлин", quote):
            return False
    return True


def _fits_award(row: dict, total: CourtTotal | None, text: str) -> bool:
    principal = money(row["principal_debt"])
    if principal is None:
        return False
    if not total or not quoted(total.quote, text) or not amount_supported(total.amount, total.quote):
        return True
    printed = money(total.amount)
    sanctions, fee = money(row["financial_sanctions"]), money(row["pau_only_amount"])
    net = principal + (sanctions or 0)
    if printed is None or net > printed:
        return False
    if total.scope == "without_duty":
        return sanctions is None or net == printed
    if total.scope == "with_duty":
        gross = net + (fee or 0)
        return gross <= printed and (sanctions is None or fee is None or gross == printed)
    return sanctions is None or fee is None or printed in (net, net + fee)


async def extract_fields(
    text: str, profile: dict, *, document_id: str | None = None, pipeline_run_id: str | None = None,
) -> dict:
    text = remove_currency_page_marker(text)
    candidates = extract_candidates(text, include_claims=False)
    context = {"profile_id": "rtk3", "document_id": document_id, "pipeline_run_id": pipeline_run_id}
    verified = await _verify(text, candidates, profile["models"]["verification"], context)
    types = _fallback_schema([], verified, set(profile["fields"]))
    response = await _gemini(text, profile["models"]["llm_extraction"], context, types, [], verified) if text.strip() else {}
    if text.strip() and not response:
        # A truncated list of contracts must not lose the independent document facts.
        compact_types = {k: v for k, v in types.items() if k in {"creditor", "creditor_inn", "document_pledge"}}
        if compact_types:
            response = await _gemini(text, profile["models"]["llm_extraction"], context, compact_types, [], verified,
                                     document_retry=True)
    fields = _scalar_fields(
        text, profile["fields"], verified, response, candidates.fields, candidates.creditor_tax_subject,
    )
    await _enrich_creditor_inn(fields, text, candidates.creditor_tax_subject)

    claims = _model_claims(text, profile, response, fields)
    rows = claims["claims"]["value"]
    # Keep the original row indices. A full-schema answer can contain discarded or merged rows.
    evidence = response.get("claims") or []
    anchors = [Claim(c.breakdown_quote or c.decision_quote, {}, None) for c in evidence]
    missing = [i for i, row in enumerate(rows) if row["principal_debt"] is None]
    if missing and len(anchors) == len(rows):
        retry_types = {_claim_key(i, "principal_debt"): (Components | None, ...) for i in missing}
        patches = await _gemini(text, profile["models"]["llm_extraction"], context, retry_types, anchors, {},
                                principal_retry=rows)
        for i in missing:
            key = _claim_key(i, "principal_debt")
            patch = patches.get(key)
            if not _itemized_principal(patch, anchors[i], text):
                continue
            updated = dict(response)
            current = response["claims"][i]
            total = (CourtTotal(amount=current.included_total.value, quote=current.included_total.quote,
                               scope=current.total_scope) if current.included_total.value else None)
            kept = [c for c in current.components if c.kind not in CATEGORY_FIELDS["principal_debt"]]
            updated["claims"] = list(response["claims"])
            updated["claims"][i] = current.model_copy(update={
                "components": [*kept, *patch.items], "principal_by_remainder": False,
                "complete_categories": list(dict.fromkeys([*current.complete_categories, "principal_debt"])),
            })
            candidate = _model_claims(text, profile, updated, fields)
            if (all(preserves_values(info["value"], candidate[k]["value"]) for k, info in claims.items())
                    and _fits_award(candidate["claims"]["value"][i], total, anchors[i].quote)):
                response, claims = updated, candidate
    printed = response.get("document_included_total")
    final_rows = claims["claims"]["value"]
    if (printed and len(final_rows) > 1 and response.get("claims_complete") is True
            and printed.scope != "unknown" and quoted(printed.quote, text) and amount_supported(printed.amount, printed.quote)):
        column = "total_amount" if printed.scope == "with_duty" else "claimed_amount"
        values = [money(row[column]) for row in final_rows]
        if all(n is not None for n in values):
            _flag_parent_total_conflict(claims, sum(values) != money(printed.amount))
    fields.update(claims)
    if fields.get("pledge_resolution", {}).get("value") is None:
        pledge = response.get("document_pledge")
        status = _document_pledge(pledge, text, claims["claims"]["value"])
        if status is not None:
            fields["pledge_resolution"] = _field(status, quote=pledge.quote)
            fields["secured_by_pledge"] = _field(status == "yes", quote=pledge.quote, route="computed")
    return {name: fields[name] for name in profile["fields"] if name in fields}

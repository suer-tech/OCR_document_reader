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
from ocr_platform.services.rtk3_evidence import (
    QUEUES, VERSION, ClaimEvidence, DocumentEvidence, Fact, PledgeFact, PriorityFact, amount_supported,
    build_fields, compact, excluded_component, is_duty_only, money, normalize_case, previous_cost,
    publication_date_field, quoted, remove_currency_page_marker, row_pledges,
)
from ocr_platform.services.rtk3_regex_candidates import (
    AMOUNTS, BREAKDOWN, CATEGORY_FIELDS, CURRENT_APPLICATION_DUTY, CURRENT_DUTY, EXPIRED_REGISTRY_ORDER, GROSS,
    LEGAL_NAME, PUBLIC_TAX_CREDITOR,
    SALARY_WORDS, SECTION_ACCOUNTED_DUTY, SEPARATE_SANCTIONS, STANDALONE_FEE_ACCOUNTING,
    Claim, Components, CourtTotal, Salary, extract_candidates,
)

from ocr_platform.services.rtk3_gemini_prompts import build_gemini_prompt

logger = get_logger(__name__)
_tax_office_registrations: dict[tuple[str, bool], list[tuple[str, dict]]] = {}
_NAME_DASH = re.compile(r"\s*[-–—−]\s*")

SCALAR_QUESTIONS = {
    "case_number": "Это номер основного дела о банкротстве, без индекса обособленного спора?",
    "document_case_number": "Это полный номер текущего определения со всеми индексами обособленного спора, если они напечатаны?",
    "debtor_inn": "Этот ИНН принадлежит именно должнику текущего дела, а не супругу, поручителю или иному участнику?",
    "creditor": "Это наименование кредитора, чьё требование суд включил или признал для удовлетворения за реестром? "
    "Для налогового органа единое выходное наименование — «ФНС России», включая территориальные подразделения.",
    "creditor_inn": "Этот ИНН принадлежит надлежащему субъекту текущего требования? Если кредитор налоговый и "
    "в акте указана инспекция или управление, проверь ИНН именно этого подразделения. Имя «ФНС России» для "
    "налогового кредитора унифицировано. Для остальных кредиторов проверь собственный ИНН кредитора.",
    "inclusion_date": "Это дата текущего определения? Если указана дата изготовления в полном объёме, используй её.",
    "grounds": "Это категория основания задолженности, которую суд признал в текущем акте?",
    "grounds_ocr": "Это дословная формулировка из документа, которая относится к включённой сумме "
    "и охватывает все нужные основания? Реквизиты, включая возможные опечатки, должны совпадать с документом.",
}
CLAIM_QUESTIONS = {
    "priority_queue": "Это очередь именно данного включённого требования? Отдельный учёт санкций сам по себе не означает «За реестром».",
    "principal_debt": "Перечислены ли все и только компоненты основного долга этого требования, включая обычные проценты и явно ранее присуждённые расходы?",
    "financial_sanctions": "Перечислены ли все и только пени, штрафы и неустойки этого требования? Пени за просрочку процентов относятся сюда, обычные проценты — нет.",
    "pau_only_amount": "Перечислена ли вся госпошлина за текущее включение, относящаяся к этому требованию? Проверь отдельно присуждённую пошлину и исключи прежнюю пошлину и возврат.",
    "pledge_resolution": "Это явно установленный судом залоговый статус именно этого включённого требования? Следует ли он из признания, отказа либо выделения вопроса о залоге?",
    "is_salary_or_severance": "Верно ли указан признак зарплаты или выходного пособия для этой суммы? Вторая очередь сама по себе его не доказывает.",
    "included_total": "Это явно напечатанный окончательный итог именно этого требования, с указанным охватом пошлины, если он известен?",
}
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


async def _verify(text: str, candidates, config: dict, context: dict) -> tuple[dict, bool]:
    values = dict(candidates.fields)
    questions = {name: _question(prompt) for name, prompt in SCALAR_QUESTIONS.items() if name in values}
    state = {"document": text, "fields": {name: fact.model_dump() for name, fact in values.items()}}
    if candidates.claims:
        state["claim_layout"] = [claim.quote for claim in candidates.claims]
        questions["claim_layout"] = _question(
            "Каждый фрагмент claim_layout соответствует одному самостоятельному включённому требованию "
            "с однородной очередью и статусом? Каждый договор выделен отдельно, в том числе в одной очереди? "
            "Все ли окончательно признанные требования охвачены без повторов, "
            "и относится ли каждый отдельно приложенный денежный компонент к указанной строке?"
        )
        for index, claim in enumerate(candidates.claims):
            for name, candidate in _claim_values(claim).items():
                key = _claim_key(index, name)
                values[key] = candidate
                state["fields"][key] = {"claim": index + 1, **candidate.model_dump()}
                if claim.cost_reference and name in {"principal_debt", "pau_only_amount"}:
                    state["fields"][key]["cost_reference"] = claim.cost_reference.model_dump()
                questions[key] = _question(CLAIM_QUESTIONS[name])
            if claim.contract_reference:
                key = _claim_key(index, "contract_reference")
                values[key] = claim.contract_reference
                state["fields"][key] = {"claim": index + 1, **claim.contract_reference.model_dump()}
                questions[key] = _question("Подтверждает ли цитата номер договора именно для этой включённой строки?")
    if not questions:
        return {}, False
    try:
        response = await asyncio.to_thread(
            call_llm_json_with_fallback, task_name="rtk3_regex_verification", provider=config["provider"],
            request_type="decisions", **context, timeout_seconds=float(config.get("timeout_seconds", 40)),
            models=[config["model"]], system_prompt="", user_content=json.dumps(state, ensure_ascii=False),
            response_schema=questions, decisions_state=state, decisions_questions=questions,
        )
    except (ValueError, TypeError, httpx.HTTPError) as exc:
        logger.warning("rtk3_jev_failed", error=type(exc).__name__, **context)
        return {}, False
    scores = decision_probabilities(response.parsed, list(questions)) if response else {}
    threshold = float(config.get("threshold", 0.6))
    layout_verified = bool(candidates.claims and scores.get("claim_layout", -1) >= threshold)
    accepted = {name: Verified(value, scores[name]) for name, value in values.items()
                if scores.get(name, -1) >= threshold and (layout_verified or not name.startswith("claim_"))}
    # The proposed creditor INN was inferred from this exact creditor name.
    # If the name is unconfirmed, let Gemini resolve the pair together.
    if "creditor" not in accepted:
        accepted.pop("creditor_inn", None)
    logger.info("rtk3_jev_finished", **context, accepted=list(accepted), layout_verified=layout_verified,
                probabilities=scores)
    return accepted, layout_verified


def _fallback_schema(layout: list[Claim], accepted: dict, scalar_names: set[str]) -> dict:
    schema: dict = {name: (Fact, ...) for name in SCALAR_QUESTIONS if name in scalar_names and name not in accepted}
    if not layout:
        schema.update(claims=(list[ClaimEvidence], ...), claims_complete=(bool, ...), uncertainty=(str, ...))
        return schema
    for index, claim in enumerate(layout):
        for name, kind in CLAIM_TYPES.items():
            key = _claim_key(index, name)
            if key not in accepted and (name != "included_total" or claim.total is not None
                                       or any(_claim_key(index, group) not in accepted for group in CATEGORY_FIELDS)):
                schema[key] = (kind | None, ...)
    return schema


async def _gemini(
    text: str, config: dict, context: dict, property_types: dict, layout: list[Claim], accepted: dict,
) -> dict:
    if not property_types:
        return {}
    model = create_model("Rtk3MissingFields", __config__=ConfigDict(extra="forbid"), **property_types)
    instructions = build_gemini_prompt(set(property_types), full_claims="claims" in property_types)
    body = {
        "document": text,
        "verified_fields": {name: candidate.value.model_dump() for name, candidate in accepted.items()},
        "verified_claims": [claim.quote for claim in layout],
    }
    try:
        result = await asyncio.to_thread(
            call_llm_json_with_fallback, task_name="rtk3_gemini_missing_fields", provider=config["provider"],
            request_type="rtk3_components", **context, models=[config["model"]],
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
        if tax_subject and req.tax_identity(fact.value) and (
                req.tax_name_matches(tax_subject, fact.quote) or req.tax_name_matches(PUBLIC_TAX_CREDITOR, fact.quote)):
            return PUBLIC_TAX_CREDITOR
        value = req.creditor_value(fact.value, fact.quote, text).value
        return PUBLIC_TAX_CREDITOR if value and req.tax_identity(value) else value
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
            if creditor == PUBLIC_TAX_CREDITOR:
                if tax_subject and req.source_inn(fact.quote, name, tax_subject).value == fact.value:
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
    elif name == "grounds" and req.explicit_ground_category(fact.quote) == fact.value:
        return fact.value
    elif name == "grounds_ocr" and quoted(fact.value, fact.quote):
        return fact.value
    return None


def _scalar_fields(
    text: str, configured: dict, verified: dict, response: dict, proposals: dict,
    tax_subject: str | None = None,
) -> dict:
    result = {}
    for name in SCALAR_QUESTIONS:  # Creditor name precedes its INN.
        if name not in configured:
            continue
        approved = verified.get(name)
        fact = approved.value if approved else response.get(name)
        creditor = result.get("creditor", {}).get("value")
        value = fact.value if approved else _model_fact(name, fact, text, creditor, tax_subject)
        if name == "creditor" and not approved and fact and value != PUBLIC_TAX_CREDITOR:
            original = proposals.get(name)
            if (original and original.value and original.value != PUBLIC_TAX_CREDITOR
                    and (value is None or req.identity(value)[1] <= req.identity(original.value)[1])
                    and req.creditor_value(original.value, fact.quote, text).value):
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
    if "document_case_number" in result and "case_number" in result:
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
    if not re.fullmatch(req.TAX_OFFICE, subject, re.I) or not re.search(r"\bпо\s+\S+", subject, re.I):
        return None
    identity = req.tax_identity(subject)
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
            or fields.get("creditor", {}).get("value") != PUBLIC_TAX_CREDITOR
            or req.tax_identity(subject) == "фнс"):
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
    if creditor == PUBLIC_TAX_CREDITOR:
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


def _amount_sum(
    value: Components | None, name: str, claim: Claim, text: str, total: CourtTotal | None, seen: set,
    *, verified: bool, reserved: set, sole_claim: bool,
) -> Decimal | None:
    if not value or not value.complete or not quoted(value.quote, text):
        return None
    visited = set()
    result = Decimal(0)
    for item in value.items:
        amount = money(item.amount)
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
        if kind == "interest" and re.search(r"пен[ияеь]|неустойк|штраф", item.quote, re.I):
            return None
        if kind == "sanction" and not (re.search(r"пен[ияеь]|неустойк|штраф|санкц|мораторн", item.quote, re.I)
                                       or item in getattr(claim.fields.get(name), "items", ())
                                       and SEPARATE_SANCTIONS.search(item.quote)):
            return None
        if kind == "duty" and not re.search(r"пошлин", item.quote, re.I):
            return None
        if kind == "prior_cost" and not previous_cost(item.quote):
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
                r"за\s+реестр|после\s+(?:полного\s+)?удовлетворения\s+требований\s+кредиторов.{0,100}(?:включ[её]н|реестр)",
                compact(queue.quote), re.I) or (
                    queue == claim.fields.get("priority_queue") and EXPIRED_REGISTRY_ORDER.search(queue.quote)
                ))
        salary = selected("is_salary_or_severance")
        salary_value = None
        if salary and type(salary.value) is bool and (
                quoted(salary.quote, claim.quote) or (
                    salary.value is False and salary == claim.fields.get("is_salary_or_severance")
                    and quoted(salary.quote, text)
                )):
            if salary.value is False or SALARY_WORDS.search(salary.quote):
                salary_value = salary.value

        pledge = selected("pledge_resolution")
        pledge_value = None
        if pledge and quoted(pledge.quote, text) and re.search(r"залог", pledge.quote, re.I):
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
        net = (amounts["principal_debt"] + amounts["financial_sanctions"]
               if all(amounts[name] is not None for name in ("principal_debt", "financial_sanctions")) else None)
        gross = (net + amounts["pau_only_amount"]
                 if net is not None and amounts["pau_only_amount"] is not None else None)
        if total:
            printed = money(total.amount)
            if ((printed is None) or (total.scope == "without_duty" and net is not None and printed != net)
                    or (total.scope == "with_duty" and gross is not None and printed != gross)
                    or (total.scope == "unknown" and gross is not None and printed not in {net, gross})):
                issues.append("Денежные компоненты не совпадают с итогом, указанным судом.")
                net, gross = None, None
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
            elif (gross is None and total.scope == "with_duty" and total == claim.total
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
        elif (claim.itemized_gross is not None and queue_valid and amounts["pau_only_amount"] is not None
              and selected("pau_only_amount") == claim.fields.get("pau_only_amount")):
            if gross is not None and gross != claim.itemized_gross:
                issues.append("Денежные компоненты не совпадают с суммой перечисленных в акте платежей.")
                net = None
            gross = claim.itemized_gross
        row = {"priority_queue": queue.value if queue_valid else None, "contract_number": claim.contract_number,
               **{name: _decimal(amount) for name, amount in amounts.items()},
               "claimed_amount": _decimal(net), "total_amount": _decimal(gross),
               "is_salary_or_severance": salary_value}
        if is_duty_only(row):
            if pledge != claim.fields.get("pledge_resolution"):
                pledge_value = None
        elif not queue_valid or net is None or gross is None or salary_value is None:
            issues.append("Часть полей строки требования не подтверждена.")
        row["pledge_resolution"] = pledge_value
        row["secured_by_pledge"] = pledge_value == "yes" if pledge_value else None
        rows.append(row)
    return _flag_parent_total_conflict(_claim_outputs(rows, issues), layout)


def _flag_parent_total_conflict(fields: dict, claims: list[Claim]) -> dict:
    if not any(claim.parent_total_conflict for claim in claims):
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


def _model_claims(text: str, profile: dict, response: dict, scalar: dict) -> dict:
    claims = response.get("claims") or []
    if not claims:
        return _claim_outputs([], ["Не установлены включённые требования."])
    evidence = DocumentEvidence(
        **{name: Fact(value=None, quote="") for name in (
            "document_case_number", "debtor_inn", "creditor_inn", "inclusion_date", "kad_publication_date", "grounds",
        )},
        creditor=Fact(value=scalar.get("creditor", {}).get("value"), quote=scalar.get("creditor", {}).get("reasoning", "")),
        claims=claims, claims_complete=response.get("claims_complete") is True,
        uncertainty=response.get("uncertainty") or "",
    )
    fields = build_fields(evidence, text, profile, recover_from_source=False)
    return {name: {**fields[name], "extraction_route": "computed" if name in {
        "secured_by_pledge", "total_claimed_amount"} else "gemini_argon"}
        for name in ("claims", "pledge_resolution", "secured_by_pledge", "total_claimed_amount") if name in fields}


async def extract_fields(
    text: str, profile: dict, *, document_id: str | None = None, pipeline_run_id: str | None = None,
) -> dict:
    text = remove_currency_page_marker(text)
    candidates = extract_candidates(text)
    context = {"profile_id": "rtk3", "document_id": document_id, "pipeline_run_id": pipeline_run_id}
    verified, layout_verified = await _verify(text, candidates, profile["models"]["verification"], context)
    layout = candidates.claims if layout_verified else []
    for index, claim in enumerate(layout):
        if claim.contract_reference and _claim_key(index, "contract_reference") not in verified:
            claim.contract_number = None
    types = _fallback_schema(layout, verified, set(profile["fields"]))
    response = await _gemini(text, profile["models"]["llm_extraction"], context, types, layout, verified) if text.strip() else {}
    fields = _scalar_fields(
        text, profile["fields"], verified, response, candidates.fields, candidates.creditor_tax_subject,
    )
    await _enrich_creditor_inn(fields, text, candidates.creditor_tax_subject)
    claims = (_fixed_claims(layout, verified, response, text) if layout else
              _flag_parent_total_conflict(_model_claims(text, profile, response, fields), candidates.claims))
    fields.update(claims)
    return {name: fields[name] for name in profile["fields"] if name in fields}

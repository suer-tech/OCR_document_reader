"""RTK3: regular expressions, Decisions verification, selective Gemini extraction."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from decimal import Decimal
import json
import re

import httpx
from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError, create_model

from ocr_platform.observability.logging import get_logger
from ocr_platform.services import rtk3_requisites as req
from ocr_platform.services.llm_gateway import call_llm_json_with_fallback, decision_probabilities
from ocr_platform.services.rtk3_evidence import (
    GROUNDS, QUEUES, VERSION, ClaimEvidence, DocumentEvidence, Fact, PledgeFact, PriorityFact, amount_supported,
    build_fields, compact, excluded_component, money, normalize_case, previous_cost, quoted,
)
from ocr_platform.services.rtk3_regex_candidates import (
    CATEGORY_FIELDS, EXPIRED_REGISTRY_ORDER, PUBLIC_TAX_CREDITOR, SALARY_WORDS, SEPARATE_SANCTIONS,
    Claim, Components, CourtTotal, Salary, extract_candidates,
)

from ocr_platform.services.rtk3_gemini_prompts import build_gemini_prompt

logger = get_logger(__name__)

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
    "grounds_ocr": "Это формулировка основания из документа, которая относится к включённой сумме и охватывает все нужные основания?",
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
            "с однородной очередью и статусом? Все ли окончательно признанные требования охвачены без повторов, "
            "и относится ли каждый отдельно приложенный денежный компонент к указанной строке?"
        )
        for index, claim in enumerate(candidates.claims):
            for name, candidate in _claim_values(claim).items():
                key = _claim_key(index, name)
                values[key] = candidate
                state["fields"][key] = {"claim": index + 1, **candidate.model_dump()}
                questions[key] = _question(CLAIM_QUESTIONS[name])
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
    elif name == "grounds" and fact.value in GROUNDS:
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
        result["kad_publication_date"] = _field(route="external", quote="Дата публикации КАД требует внешнего источника.")
        result["kad_publication_date"]["external_data_required"] = True
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
        key = (compact(item.quote), amount)
        if (amount is None or kind not in CATEGORY_FIELDS[name] or key in visited or key in seen
                or (not verified and key in reserved)
                or not quoted(item.quote, text) or not amount_supported(item.amount, item.quote)
                or excluded_component(item.quote, text)):
            return None
        if not verified and not quoted(item.quote, claim.quote):
            separate_duty = sole_claim and kind == "duty" and re.search(
                r"взыскать|признать|включить|учитыва(?:ть|ется|ются)|подлежащ\w*\s+взысканию|"
                r"подлеж\w*\s+(?:учету|удовлетворению)", item.quote, re.I)
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
            return None
        visited.add(key)
        result += amount
    seen.update(visited)
    return result


def _decimal(value: Decimal | None) -> str | None:
    return format(value, ".2f") if value is not None else None


def _fixed_claims(layout: list[Claim], verified: dict, response: dict, text: str) -> dict:
    rows, status, issues, seen = [], set(), [], set()
    reserved = {
        (compact(item.quote), money(item.amount))
        for candidate in verified.values() if isinstance(candidate.value, Components)
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
        if salary and type(salary.value) is bool and quoted(salary.quote, claim.quote):
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
                pledge_value = None
        status.add(pledge_value)

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
            elif (gross is None and total.scope == "with_duty" and total == claim.total
                  and sum(amount for amount in amounts.values() if amount is not None) <= printed):
                # The regex balanced this printed list before assigning scope.
                # The gross is independent of how its unclassified costs split.
                gross = printed
        if not queue_valid or net is None or gross is None or salary_value is None:
            issues.append("Часть полей строки требования не подтверждена.")
        rows.append({"priority_queue": queue.value if queue_valid else None,
                     **{name: _decimal(amount) for name, amount in amounts.items()},
                     "claimed_amount": _decimal(net), "total_amount": _decimal(gross),
                     "is_salary_or_severance": salary_value})
    return _claim_outputs(rows, status, issues)


def _claim_outputs(rows: list[dict], pledges: set, issues: list[str]) -> dict:
    combined = {}
    money_fields = [*CATEGORY_FIELDS, "claimed_amount", "total_amount"]
    for index, row in enumerate(rows):
        key = row["priority_queue"] or index
        if key not in combined:
            combined[key] = row.copy()
            continue
        existing = combined[key]
        for name in money_fields:
            before, after = money(existing[name]), money(row[name])
            existing[name] = _decimal(before + after) if before is not None and after is not None else None
        if existing["is_salary_or_severance"] != row["is_salary_or_severance"]:
            values = (existing["is_salary_or_severance"], row["is_salary_or_severance"])
            existing["is_salary_or_severance"] = True if True in values else None
            issues.append("Одна очередь содержит разные признаки зарплаты или выходного пособия.")
    rows = list(combined.values())
    status = next(iter(pledges)) if len(pledges) == 1 else None
    status_issue = "" if status else "Не установлен единый залоговый статус включённых требований."
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
        return _claim_outputs([], set(), ["Не установлены включённые требования."])
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
    candidates = extract_candidates(text)
    context = {"profile_id": "rtk3", "document_id": document_id, "pipeline_run_id": pipeline_run_id}
    verified, layout_verified = await _verify(text, candidates, profile["models"]["verification"], context)
    layout = candidates.claims if layout_verified else []
    types = _fallback_schema(layout, verified, set(profile["fields"]))
    response = await _gemini(text, profile["models"]["llm_extraction"], context, types, layout, verified) if text.strip() else {}
    fields = _scalar_fields(
        text, profile["fields"], verified, response, candidates.fields, candidates.creditor_tax_subject,
    )
    fields.update(_fixed_claims(layout, verified, response, text) if layout else _model_claims(text, profile, response, fields))
    return {name: fields[name] for name in profile["fields"] if name in fields}

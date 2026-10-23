"""Court document → regex proposals → JEV → selective LLM fallback."""
from __future__ import annotations

import asyncio
from dataclasses import asdict
from datetime import datetime, timedelta
import json
import math
from time import perf_counter

from ocr_platform.observability.logging import get_logger
from ocr_platform.observability.metrics import COURT_REGEX_JEV_FIELDS_COUNT, COURT_REGEX_JEV_DURATION_SECONDS
from ocr_platform.services.court_field_rules import FIELD_RULES
from ocr_platform.services.court_jev_questions import verification_question
from ocr_platform.services.court_regex_candidates import CourtCandidate, extract_court_candidates
from ocr_platform.services.court_selective_llm import extract_court_llm_fields
from ocr_platform.services.court_field_provenance import finalize_court_fields
from ocr_platform.services.llm_gateway import call_llm_json_with_fallback, decision_probabilities

logger = get_logger(__name__)
_DEPENDENCIES = {"early_report_deadline": ("next_session_date",),
                 "procedure_end_date_is_calculated": ("procedure_end_date",),
                 "next_session_date": ("procedure_end_date",),
                 "next_session_time": ("next_session_date",),
                 "court_hearing_address": ("next_session_date",)}
_ABSENCE_REASONS = {
    "not_found", "no_explicit_acceptance", "appointment_name_not_found",
    "no_explicit_appointment", "no_explicit_time_for_appointment", "no_linked_location",
    "session_date:no_explicit_appointment", "appointment:no_explicit_appointment",
    "missing_procedure_end_date",
    "missing_next_session_date",
    "no_appointment_or_procedure_end",
    "no_explicit_resolutive_date",
}


def _threshold(config: dict, name: str) -> float:
    value = config.get("field_thresholds", {}).get(name, config.get("threshold", .95))
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not .5 < value <= 1:
        raise ValueError(f"JEV threshold for {name} must be greater than 0.5 and at most 1")
    return float(value)


async def extract_court_regex_jev(
    text: str, profile_config: dict, *, document_id: str, pipeline_run_id: str,
) -> dict[str, dict]:
    output = await _extract_court_fields(text, profile_config, document_id=document_id, pipeline_run_id=pipeline_run_id)
    return finalize_court_fields(text, output)


async def _extract_court_fields(
    text: str, profile_config: dict, *, document_id: str, pipeline_run_id: str,
) -> dict[str, dict]:
    """Run explicit direct-LLM fields in parallel with the verified regex path."""
    fields = profile_config.get("fields", {})
    if any(field.get("regex_jev_mode", "regex_jev") not in {"regex_jev", "direct_llm"}
           for field in fields.values()):
        raise ValueError("Unsupported court field regex_jev_mode")
    if any(name not in FIELD_RULES for name in fields):
        raise ValueError("Court regex/JEV profile contains an unsupported field")
    direct = {name: field for name, field in fields.items() if field.get("regex_jev_mode") == "direct_llm"}
    if not direct:
        return await _extract_verified_court_fields(
            text, profile_config, document_id=document_id, pipeline_run_id=pipeline_run_id,
        )
    regular = {name: field for name, field in fields.items() if name not in direct}
    started = perf_counter()

    async def extract_direct():
        direct_started = perf_counter()
        extracted = await extract_court_llm_fields(
            text, direct, profile_config, document_id=document_id, pipeline_run_id=pipeline_run_id,
        )
        output = {}
        for name, field in direct.items():
            item = extracted.get(name)
            if not isinstance(item, dict):
                item = {"value": None, "confidence": 0, "source": "court_llm_fallback",
                        "validation_issue": "LLM не вернула поле; требуется ручная проверка"}
            item["extraction_route"] = "direct_llm"
            if field.get("required", False) and item.get("value") in (None, "", []):
                item.setdefault("validation_issue", "Обязательное поле не найдено; требуется ручная проверка")
            output[name] = item
            COURT_REGEX_JEV_FIELDS_COUNT.labels(field=name, status="direct_llm").inc()
        COURT_REGEX_JEV_DURATION_SECONDS.labels(stage="direct_llm").observe(perf_counter() - direct_started)
        logger.info("court_direct_llm_finished", document_id=document_id, pipeline_run_id=pipeline_run_id,
                    requested_fields=list(direct), duration_seconds=perf_counter() - direct_started)
        return output

    tasks = [asyncio.create_task(extract_direct()), asyncio.create_task(_extract_verified_court_fields(
        text, {**profile_config, "fields": regular}, document_id=document_id, pipeline_run_id=pipeline_run_id,
    ))]
    try:
        direct_output, verified_output = await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    output = {**verified_output, **direct_output}
    COURT_REGEX_JEV_DURATION_SECONDS.labels(stage="total_with_direct_llm").observe(perf_counter() - started)
    return {name: output[name] for name in fields}


async def _extract_verified_court_fields(
    text: str, profile_config: dict, *, document_id: str, pipeline_run_id: str,
) -> dict[str, dict]:
    started = perf_counter()
    fields_config = profile_config.get("fields", {})
    requested = list(fields_config)
    if not requested:
        return {}
    if any(name not in FIELD_RULES for name in requested):
        raise ValueError("Court regex/JEV profile contains an unsupported field")
    verification = profile_config.get("models", {}).get("verification", {})
    thresholds = {name: _threshold(verification, name) for name in requested}
    extraction_names = list(requested)
    for name in extraction_names:
        for dependency in _DEPENDENCIES.get(name, ()):
            if dependency not in extraction_names:
                extraction_names.append(dependency)
    for name in extraction_names:
        thresholds.setdefault(name, _threshold(verification, name))
    extraction_started = perf_counter()
    proposals = extract_court_candidates(text, extraction_names)
    if text:
        for name in requested:
            if (name not in proposals.fields and not fields_config[name].get("required", False)
                    and proposals.problems.get(name) in _ABSENCE_REASONS):
                proposals.fields[name] = CourtCandidate(None, text, 0, len(text), "court_confirm_absence",
                                                        {"absence": True, "reason": proposals.problems[name]},
                                                        ("procedure_end_date",) if (
                                                            name == "procedure_end_date_is_calculated"
                                                            or name == "next_session_date" and "procedure_end_date" in requested
                                                        ) else ())
    COURT_REGEX_JEV_DURATION_SECONDS.labels(stage="regex").observe(perf_counter() - extraction_started)
    logger.info("court_regex_candidates_ready", document_id=document_id, pipeline_run_id=pipeline_run_id,
                requested_fields=requested, candidate_fields=list(proposals.fields), problems=proposals.problems)
    probabilities, result = {}, None
    concrete = verification.get("question_style", "context") == "concrete"
    questions = {name: verification_question(name, candidate, concrete=concrete)
                 for name, candidate in proposals.fields.items()}
    if questions:
        # Full, untruncated document; offsets reference precisely this string.
        # Quotes are represented by source offsets so the document is not
        # duplicated once for every absence question or calculated field.
        state = {"document": text, "fields": {name: {key: value for key, value in asdict(candidate).items()
                                                    if key != "quote" or len(candidate.quote) <= 2000}
                                              for name, candidate in proposals.fields.items()}}
        verification_started = perf_counter()
        result = await asyncio.to_thread(
            call_llm_json_with_fallback, task_name="court_regex_verification", provider=verification.get("provider", "router_ai"),
            request_type="decisions", profile_id="court_decision_ru", document_id=document_id, pipeline_run_id=pipeline_run_id,
            timeout_seconds=float(verification.get("timeout_seconds", 40)),
            models=[verification.get("model", "typesafe/jev-1.14")], system_prompt="",
            user_content=json.dumps(state, ensure_ascii=False), response_schema=questions,
            decisions_questions=questions, decisions_state=state,
            mlflow_params={"question_fields": ",".join(questions), "thresholds": json.dumps(thresholds)},
        )
        COURT_REGEX_JEV_DURATION_SECONDS.labels(stage="jev").observe(perf_counter() - verification_started)
        if result is not None:
            probabilities = decision_probabilities(result.parsed, list(questions))
        logger.info("court_regex_verification_scored", document_id=document_id, pipeline_run_id=pipeline_run_id,
                    probabilities=probabilities, thresholds=thresholds, response_received=result is not None)
    accepted = {name for name in proposals.fields if probabilities.get(name, -1) >= thresholds[name]}
    report_pair = {'early_report_required', 'early_report_deadline'}
    flag = proposals.fields.get('early_report_required')
    due = proposals.fields.get('early_report_deadline')
    if (report_pair <= set(requested) and 'early_report_required' in accepted and flag.value is False
            and (due is not None and due.value is not None
                 or proposals.problems.get('early_report_deadline') == 'unresolved_report_date_reference')):
        # Deterministically contradictory proposals are unconfirmed as a pair.
        # Keep individual field prompts; let one selective request review both.
        accepted.difference_update(report_pair)
    # Calculated fields cannot survive rejection of their own source value.
    changed = True
    while changed:
        invalid = {name for name in accepted if any(dep not in accepted for dep in proposals.fields[name].dependencies)}
        changed = bool(invalid)
        accepted.difference_update(invalid)
    output, fallback_fields = {}, {}
    for name in requested:
        if name in accepted:
            candidate = proposals.fields[name]
            output[name] = {"value": candidate.value, "confidence": probabilities[name], "source": "regex_jev",
                            "reasoning": "Отсутствие значения в документе подтверждено JEV" if candidate.metadata.get("absence")
                            else "Значение извлечено регулярными правилами и подтверждено JEV",
                            "evidence": candidate.quote, "evidence_start": candidate.start, "evidence_end": candidate.end,
                            "verification": {"model": result.winner_model, "returned_model": result.parsed.get("model"),
                                             "probability": probabilities[name], "threshold": thresholds[name]},
                            "regex_rule": candidate.rule}
            if name == "next_session_date" and not candidate.metadata.get("absence"):
                output[name]["selection_basis"] = candidate.metadata.get("selection_basis", "explicit_appointment")
            if name == 'early_report_deadline':
                output[name]['deadline_basis'] = candidate.metadata.get('deadline_basis', 'not_found' if candidate.value is None else None)
            if candidate.metadata.get("absence"):
                for key in ("evidence", "evidence_start", "evidence_end"):
                    output[name].pop(key)
                output[name]["verification"]["absence"] = True
            status = "accepted"
        else:
            fallback_fields[name] = fields_config[name]
            status = "rejected" if name in proposals.fields else "missing"
        COURT_REGEX_JEV_FIELDS_COUNT.labels(field=name, status=status).inc()
    if fallback_fields:
        fallback_started = perf_counter()
        extracted = await extract_court_llm_fields(text, fallback_fields, profile_config,
                                                 document_id=document_id, pipeline_run_id=pipeline_run_id,
                                                 reference_fields={name: {
                                                     "value": proposals.fields[name].value,
                                                     "source": "regex_jev",
                                                 } for name in ("procedure_end_date", "next_session_date", "early_report_required")
                                                     if name in accepted} or None)
        COURT_REGEX_JEV_DURATION_SECONDS.labels(stage="llm").observe(perf_counter() - fallback_started)
        # Merge only requested fallback names; a provider cannot overwrite approval.
        for name in fallback_fields:
            item = extracted.get(name)
            if not isinstance(item, dict):
                reason = "LLM не вернула поле; требуется ручная проверка"
                item = {"value": None, "confidence": 0, "source": "court_llm_fallback", "validation_issue": reason, "reasoning": reason}
            item["regex_verification"] = {"probability": probabilities.get(name),
                                          "problem": proposals.problems.get(name) or "not_confirmed"}
            output[name] = item
            COURT_REGEX_JEV_FIELDS_COUNT.labels(field=name, status="llm_fallback").inc()
    # A model may return an ordinary optional null for a contradiction already
    # established in the source. That must not erase the required review.
    if ("financial_manager_full_name" in output
            and proposals.problems.get("financial_manager_full_name") == "source_conflict_manager_is_debtor"):
        reason = "В назначении управляющего повторено ФИО должника; противоречие в исходном акте требует ручной проверки"
        output["financial_manager_full_name"].update(value=None, confidence=0, validation_issue=reason, reasoning=reason)
        COURT_REGEX_JEV_FIELDS_COUNT.labels(field="financial_manager_full_name", status="source_conflict_review").inc()
        logger.warning("court_manager_source_conflict", document_id=document_id, pipeline_run_id=pipeline_run_id,
                       field="financial_manager_full_name", problem="source_conflict_manager_is_debtor")
    _validate_relationships(output, text)
    for name, item in output.items():
        if fields_config[name].get("required", False) and item.get("value") in (None, "", []):
            item.setdefault("validation_issue", "Обязательное поле не найдено; требуется ручная проверка")
    duration = perf_counter() - started
    COURT_REGEX_JEV_DURATION_SECONDS.labels(stage="total").observe(duration)
    logger.info("court_regex_jev_finished", document_id=document_id, pipeline_run_id=pipeline_run_id,
                approved_fields=[name for name in requested if name in accepted],
                llm_fields=list(fallback_fields), jev_calls=int(bool(questions)), duration_seconds=duration)
    return output


def _validate_relationships(output: dict[str, dict], text: str | None = None) -> None:
    def reject(name: str, reason: str):
        if name in output:
            output[name].update(value=None, confidence=0, validation_issue=reason, reasoning=reason)
    # Contradictory dates can be printed in the source itself. Retain the
    # extracted value and evidence; never silently infer a different year.
    def date_value(name: str):
        value = output.get(name, {}).get("value")
        try:
            return datetime.strptime(value, "%d.%m.%Y").date()
        except (TypeError, ValueError):
            return None
    acceptance = date_value("application_acceptance_date")
    for act_field in ("resolutive_part_date", "decision_date"):
        act_date = date_value(act_field)
        if acceptance is not None and act_date is not None and acceptance > act_date:
            item = output["application_acceptance_date"]
            reason = (f"Дата принятия заявления {item['value']} позже даты судебного акта "
                      f"{output[act_field]['value']} ({act_field}); противоречие требует ручной проверки")
            item["validation_issue"] = reason
            item["reasoning"] = reason
            break
    if (output.get("early_report_required", {}).get("value") is True
            and "early_report_deadline" in output
            and output["early_report_deadline"].get("value") is None):
        reject("early_report_deadline", "Обязанность представить отчёт подтверждена, срок не определён; требуется ручная проверка")
    if "next_session_date" in output and output["next_session_date"].get("value") is None:
        for name in ("next_session_time", "court_hearing_address"):
            if output.get(name, {}).get("value") is not None:
                reject(name, "Значение не связано с однозначной датой следующего заседания")
    if output.get("next_session_date", {}).get("selection_basis") == "procedure_end_fallback":
        for name in ("next_session_time", "court_hearing_address"):
            if output.get(name, {}).get("value") is not None:
                reject(name, "Дата подставлена из окончания процедуры; время и место заседания не установлены")
        end = output.get("procedure_end_date")
        if end is not None and output["next_session_date"].get("value") != end.get("value"):
            reject("next_session_date", "Подставленная дата не совпадает с итоговой датой окончания процедуры")
    if "procedure_end_date" in output and output["procedure_end_date"].get("value") is None:
        for name in ("procedure_end_date_is_calculated",):
            if output.get(name, {}).get("value") is not None:
                reject(name, "Не определена дата окончания процедуры, необходимая для этого поля")
    elif output.get("procedure_end_date", {}).get("value") and "procedure_end_date_is_calculated" in output:
        flag = output["procedure_end_date_is_calculated"]
        if text is not None:
            from ocr_platform.services.court_decision_legacy_rules import extract_procedure_end_date_with_meta

            source_end, calculated = extract_procedure_end_date_with_meta(text)
            if source_end == output["procedure_end_date"]["value"] and calculated is not None:
                if flag.get("value") is not calculated:
                    reject("procedure_end_date_is_calculated", "Признак расчёта не согласован с основанием даты окончания; требуется ручная проверка")
            elif flag.get("value") is None:
                reject("procedure_end_date_is_calculated", "Для установленной даты окончания не определён признак расчёта; требуется ручная проверка")
    report = output.get('early_report_deadline', {})
    if (output.get('early_report_required', {}).get('value') is False and report.get('value') is not None):
        reason = 'Отрицательный флаг обязанности несовместим с непустым сроком отчёта; требуется совместная ручная проверка'
        for name in ('early_report_required', 'early_report_deadline'):
            reject(name, reason)
        return
    if text is not None and report:
        from ocr_platform.services.court_regex_report_deadline import extract_report_deadline_candidates

        source = extract_report_deadline_candidates(text, output.get('next_session_date', {}).get('value'))
        if source.problem in {'unresolved_report_date_reference', 'conflicting_explicit_report_dates'}:
            reason = 'Неоднозначная ссылка или противоречие в сроке отчёта; требуется ручная проверка'
            reject('early_report_deadline', reason)
            reject('early_report_required', reason)
            return
    if report.get('value') is not None:
        if text is not None:
            from ocr_platform.services.court_report_validation import validate_report_deadline

            basis = report.get('deadline_basis') or (source.selected.deadline_basis if source.selected else None)
            try:
                validate_report_deadline(text, report['value'], basis, report.get('evidence') or (source.selected.quote if source.selected else None),
                                         output.get('next_session_date', {}).get('value'))
            except ValueError as exc:
                reject('early_report_deadline', str(exc) + '; требуется ручная проверка')
            return
        if "next_session_date" not in output:
            return
        try:
            expected = (datetime.strptime(output["next_session_date"]["value"], "%d.%m.%Y")
                        - timedelta(days=10)).strftime("%d.%m.%Y")
        except (TypeError, ValueError):
            expected = None
        if output["early_report_deadline"]["value"] != expected:
            reject("early_report_deadline", "Срок отчёта должен равняться следующей дате минус 10 календарных дней")

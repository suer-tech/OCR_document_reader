"""Registry of source-bound court candidates, without automatic acceptance."""
from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass, replace
import importlib
import re
from typing import Any

from ocr_platform.services import court_decision_legacy_rules as legacy
from ocr_platform.services.court_decision_dates import extract_decision_date_candidates
from ocr_platform.services.court_decision_sections import extract_motivating_part, extract_resolutive_part
from ocr_platform.services.court_procedure_duration import extract_procedure_duration
from ocr_platform.services.court_case_number_format import normalize_court_case_number
from ocr_platform.services.court_field_format import normalize_court_value


@dataclass(frozen=True)
class CourtCandidate:
    value: Any
    quote: str
    start: int
    end: int
    rule: str
    metadata: dict[str, Any]
    dependencies: tuple[str, ...] = ()


@dataclass(frozen=True)
class CourtCandidates:
    fields: dict[str, CourtCandidate]
    problems: dict[str, str]


_MODULES = {
    "application_acceptance_date": ("application_acceptance_date", "extract_application_acceptance_date_candidates"),
    "financial_manager_full_name": ("financial_manager_full_name", "extract_financial_manager_full_name"),
    "debtor_full_name": ("debtor_full_name", "extract_debtor_full_name_candidates"),
    "debtor_inn": ("debtor_inn", "extract_debtor_inn_candidates"),
    "debtor_snils": ("debtor_snils", "extract_debtor_snils_candidates"),
    "debtor_birth_date": ("debtor_birth_date", "extract_debtor_birth_date_candidates"),
    "debtor_birth_place": ("debtor_birth_place", "extract_debtor_birth_place_candidates"),
    "debtor_gender": ("debtor_gender", "extract_debtor_gender_candidates"),
    "debtor_registration_address": ("debtor_registration_address", "extract_debtor_registration_address_candidates"),
    "judge_full_name": ("judge_full_name", "extract_judge_full_name_candidates"),
    "court_name": ("court_name", "extract_court_name_candidates"),
    "procedure_type": ("procedure_type", "extract_procedure_type_candidates"),
    "procedure_end_date_source": ("procedure_end_date_source", "extract_procedure_end_date_source_candidates"),
    "document_basis": ("document_basis", "extract_document_basis_candidates"),
    "resolutive_part_date": ("resolutive_part_date", "extract_resolutive_part_date_candidates"),
    "next_session_date": ("next_session_date", "extract_next_session_date_candidates"),
    "next_session_time": ("next_session_time", "extract_next_session_time_candidates"),
    "court_hearing_address": ("hearing_address", "extract_court_hearing_address_candidates"),
}


def _candidate(selected: Any, text: str, rule: str, dependencies=()) -> CourtCandidate | None:
    if selected is None or selected.value is None:
        return None
    data = asdict(selected) if is_dataclass(selected) else vars(selected)
    quote = data.get("quote") or data.get("evidence")
    start, end = data.get("start"), data.get("end")
    if (not isinstance(quote, str) or not quote or type(start) is not int or type(end) is not int
            or not 0 <= start < end <= len(text) or text[start:end] != quote):
        return None
    return CourtCandidate(selected.value, quote, start, end, rule,
                          {key: value for key, value in data.items() if key not in {"value", "quote", "evidence", "start", "end"}},
                          tuple(dependencies))


_SECTION = re.compile(
    r"(?im)(?:^\s*|(?<=\n)\s*)(?P<name>у\s*с\s*т\s*а\s*н\s*о\s*в\s*и\s*л|"
    r"р\s*е\s*ш\s*и\s*л|о\s*п\s*р\s*е\s*д\s*е\s*л\s*и\s*л)\s*(?::|(?=\n|$))"
)
_SIGNATURE = re.compile(r"электронная\s+подпись\s+действительна", re.I)


def _section(text: str, field: str) -> CourtCandidate | None:
    # Preserve the current public boundaries, including inline "установил
    # следующее". Their semantic correctness is now checked by JEV.
    value = extract_motivating_part(text) if field == "motivating_part" else extract_resolutive_part(text)
    if value:
        start = text.index(value)
        return CourtCandidate(value, value, start, start + len(value), "court_existing_section_boundaries", {})
    headings = list(_SECTION.finditer(text))
    operative = next((item for item in headings if re.sub(r"\s", "", item["name"]).casefold() in {"решил", "определил"}), None)
    if operative is None:
        return None
    if field == "motivating_part":
        heading = next((item for item in headings if item.start() < operative.start()
                        and re.sub(r"\s", "", item["name"]).casefold() == "установил"), None)
        if heading is None:
            return None
        start, end = heading.end(), operative.start()
    else:
        start = operative.end()
        signature = _SIGNATURE.search(text, start)
        end = signature.start() if signature else len(text)
    while start < end and text[start] in " \r\n\t:,.–—-()":
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    if start == end:
        return None
    return CourtCandidate(text[start:end], text[start:end], start, end, "court_section_boundaries", {})


def _next_session_candidate(text: str, end_value: str | None, is_calculated: bool):
    from ocr_platform.services.court_regex_next_session_date import extract_next_session_date_candidates
    result = extract_next_session_date_candidates(text)
    candidate = _candidate(result.selected, text, "court_next_session_date")
    if candidate is not None:
        return replace(candidate, metadata={**candidate.metadata, "selection_basis": "explicit_appointment"}), result.problem
    if end_value is not None:
        return CourtCandidate(
            end_value, text, 0, len(text), "court_next_session_from_procedure_end",
            {"selection_basis": "procedure_end_fallback", "is_calculated": is_calculated,
             "explicit_appointment_problem": result.problem}, ("procedure_end_date",),
        ), result.problem
    return None, "no_appointment_or_procedure_end" if result.problem == "no_explicit_appointment" else result.problem


def extract_court_candidates(text: str, field_names: list[str]) -> CourtCandidates:
    fields, problems = {}, {}
    date_result = extract_decision_date_candidates(text)
    end_value, is_calculated = legacy.extract_procedure_end_date_with_meta(text)
    next_candidate = None
    next_problem = None
    if {"early_report_deadline", "next_session_date"}.intersection(field_names):
        next_candidate, next_problem = _next_session_candidate(text, end_value, is_calculated)
    for name in field_names:
        candidate = None
        problem = "not_found"
        if name == "case_number":
            value = legacy.extract_case_number(text)
            if value:
                matches = list(legacy.CASE_NUMBER_RE.finditer(text)) + list(legacy._HEADER_CASE_NUMBER_RE.finditer(text[:2000]))
                for match in sorted(matches, key=lambda item: item.start()):
                    normalized = re.sub(r"\s+", "", match[1])
                    normalized = re.sub(rf"[{legacy._CASE_DASHES}]", "-", normalized)
                    if normalized == value:
                        candidate = CourtCandidate(normalize_court_case_number(value), match[0], match.start(), match.end(), "court_case_number", {})
                        break
        elif name == "decision_date":
            candidate = _candidate(date_result.selected, text, "court_act_date")
            problem = date_result.problem
        elif name in {"procedure_end_date", "procedure_end_date_is_calculated"}:
            if end_value is not None:
                value = end_value if name == "procedure_end_date" else is_calculated
                candidate = CourtCandidate(value, text, 0, len(text), "court_procedure_end",
                                           {"is_calculated": is_calculated, "policy": "preserve_current_act_date_convention"},
                                           ("procedure_end_date",) if name.endswith("is_calculated") else ())
        elif name == "procedure_duration_months":
            result = extract_procedure_duration(text)
            candidate = _candidate(result.selected, text, "court_explicit_duration")
            problem = result.problem
        elif name == "early_report_required":
            from ocr_platform.services.court_regex_early_report_required import extract_early_report_required
            result = extract_early_report_required(text)
            candidate = _candidate(result, text, "court_early_report_order")
            if candidate is None and result.value is False and result.reason == "no_advance_report_order" and text:
                candidate = CourtCandidate(False, text, 0, len(text), "court_no_early_report_order",
                                           {"absence_of_order": True})
            problem = result.reason
        elif name == "early_report_deadline":
            from ocr_platform.services.court_regex_report_deadline import extract_report_deadline_candidates
            result = extract_report_deadline_candidates(text, next_candidate.value if next_candidate else None)
            explicit = result.selected is not None and result.selected.basis == "explicit_report_date"
            candidate = _candidate(result.selected, text,
                                   "court_explicit_report_date" if explicit else "court_report_minus_ten_days",
                                   () if explicit else ("next_session_date",))
            if candidate is not None and not explicit:
                candidate = replace(candidate, metadata={**candidate.metadata, "base_field": "next_session_date",
                                                         "base_value": next_candidate.value})
            problem = result.problem or ("not_found" if result.selected is None else "invalid_source_span")
        elif name == "next_session_date":
            candidate, problem = next_candidate, next_problem
        elif name in {"motivating_part", "resolutive_part"}:
            candidate = _section(text, name)
        elif name in _MODULES:
            suffix, function = _MODULES[name]
            module_name = f"ocr_platform.services.court_regex_{suffix}"
            try:
                module = importlib.import_module(module_name)
            except ModuleNotFoundError as exc:
                if exc.name != module_name:
                    raise
                problems[name] = "extractor_not_implemented"
                continue
            result = getattr(module, function)(text)
            candidate = _candidate(result.selected, text, f"court_{suffix}")
            problem = result.problem
            if candidate is not None and name in {"next_session_time", "court_hearing_address"}:
                candidate = replace(candidate, dependencies=("next_session_date",))
            if name == "debtor_full_name" and candidate is not None:
                parts = candidate.value.split(maxsplit=2)
                if len(parts) != 3:
                    candidate, problem = None, "incomplete_debtor_name"
                else:
                    candidate = replace(candidate, value=dict(zip(
                        ("debtor_last_name", "debtor_first_name", "debtor_patronymic"), parts, strict=True)))
        else:
            problem = "extractor_not_registered"
        if candidate is not None:
            fields[name] = replace(candidate, value=normalize_court_value(name, candidate.value))
        else:
            problems[name] = problem or "invalid_or_missing_source_span"
    return CourtCandidates(fields, problems)

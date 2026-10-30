"""Attach source sections and quoted explanations to new court results."""
from __future__ import annotations

import re
from typing import Any

from ocr_platform.services import court_decision_legacy_rules as legacy
from ocr_platform.services.court_decision_dates import extract_decision_date_candidates
from ocr_platform.services.court_field_format import normalize_court_value
from ocr_platform.services.court_identifier_evidence import identifier_source_issue
from ocr_platform.services.court_regex_candidates import _section
from ocr_platform.services.court_regex_debtor_birth_place import (
    complete_explicit_republic_type,
    normalize_explicit_district_boundary,
)
from ocr_platform.services.court_regex_debtor_full_name import (
    normalize_source_bound_surname,
)
from ocr_platform.services.court_regex_procedure_end_date_source import (
    extract_procedure_end_date_source_candidates,
)

_SECTIONS = {"header", "motivating_part", "resolutive_part", "signature_block", "document"}
_REASONS = {
    "case_number": "Выбран номер текущего дела из шапки; написание нормализовано без удаления частей номера.",
    "decision_date": "Выбрана дата текущего судебного акта, не дата подписи или другого события.",
    "resolutive_part_date": "Выбрана отдельная явно указанная дата резолютивной части.",
    "debtor_full_name": "Выбрано ФИО должника текущего дела; сохранены составные части имени и отчества.",
    "debtor_gender": "Выбран пол должника по однозначному указанию или связанному с должником ФИО.",
    "debtor_birth_date": "Выбрана дата рождения текущего должника, не дата другого лица или судебного события.",
    "debtor_birth_place": "Выбрано явно указанное место рождения должника; оформление нормализовано без географических добавлений.",
    "debtor_registration_address": "Выбран адрес регистрации/проживания текущего должника; отсутствующие части не дополнялись.",
    "debtor_inn": "Выбран ИНН текущего должника; ведущие нули и исходные цифры сохранены.",
    "debtor_snils": "Выбран СНИЛС текущего должника; изменены только разделители, исходные цифры сохранены.",
    "financial_manager_full_name": "Выбран управляющий, утверждённый текущим актом; отсутствующие имена не раскрываются.",
    "judge_full_name": "Выбран судья текущего акта; сохранены имеющиеся полное имя или инициалы.",
    "court_name": "Выбран суд, вынесший текущий акт.",
    "procedure_type": "Выбрана процедура по действующему распоряжению суда; название приведено к формату ТЗ.",
    "procedure_duration_months": "Выбрано прямо назначенное число месяцев, без вычисления из дат.",
    "procedure_end_date_source": "Выбран способ задания срока текущей процедуры по действующему распоряжению суда.",
    "early_report_required": "Найдено действующее поручение управляющему представить отчёт заранее, а не простое упоминание отчёта.",
    "document_basis": "Выбран вид текущего судебного акта по его заголовку и распоряжению.",
    "next_session_date": "Выбрана явно назначенная дата следующего рассмотрения.",
    "next_session_time": "Выбрано время того же явно назначенного следующего рассмотрения.",
    "court_hearing_address": "Выбрано явно указанное место следующего рассмотрения.",
    "application_acceptance_date": "Выбрана дата принятия заявления именно текущего должника.",
    "motivating_part": "Возвращён текст мотивировочной части в установленных границах.",
    "resolutive_part": "Возвращён текст резолютивной части в установленных границах.",
}


class CourtSourceContext:
    def __init__(self, text: str):
        self.text = text
        self.ranges = []
        for name in ("motivating_part", "resolutive_part"):
            candidate = _section(text, name)
            if candidate:
                self.ranges.append((candidate.start, candidate.end, name))
        self.header_end = min((a for a, _, _ in self.ranges), default=len(text))
        signature = re.search(r"электронная\s+подпись\s+действительна", text, re.I)
        self.signature_start = signature.start() if signature else len(text)
        self.end, self.calculated = legacy.extract_procedure_end_date_with_meta(text)
        self.term = extract_procedure_end_date_source_candidates(text).selected

    def section(self, start: int, end: int) -> str:
        while start < end and self.text[start].isspace():
            start += 1
        while end > start and self.text[end - 1].isspace():
            end -= 1
        if start >= self.signature_start:
            return "signature_block"
        for left, right, name in self.ranges:
            if left <= start < end <= right:
                return name
        if end <= self.header_end and self.ranges:
            return "header"
        return "document"

    def evidence(self, item: dict) -> tuple[str, int, int] | None:
        quote = item.get("evidence")
        if not isinstance(quote, str) or not quote.strip():
            return None
        start, end = item.get("evidence_start"), item.get("evidence_end")
        if type(start) is int and type(end) is int and 0 <= start < end <= len(self.text):
            if self.text[start:end] == quote:
                return quote, start, end
        matches = list(re.finditer(re.escape(quote), self.text))
        if len(matches) == 1:
            return quote, matches[0].start(), matches[0].end()
        return None


def _quotation(span: tuple[str, int, int]) -> str:
    quote = span[0]
    if len(quote) <= 900:
        return "Цитата из документа: «" + quote + "»."
    # Exact boundary fragments, clearly identified as excerpts, not a made-up
    # contiguous quote. Full evidence stays in the structured result.
    return "Фрагменты начала и конца исходного текста: «" + quote[:350] + "» … «" + quote[-350:] + "»."


def finalize_court_fields(text: str, fields: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Apply public formats and provenance. No dates, identities or addresses inferred."""
    context = CourtSourceContext(text)
    output = {}
    for name, original in fields.items():
        item = dict(original)
        method = item.get("extraction_method")
        if method is None and item.get("source") not in _SECTIONS:
            method = item.get("source")
        item["extraction_method"] = method
        before = item.get("value")
        try:
            format_input = before
            if name in {"debtor_inn", "debtor_snils"} and type(before) is int and before >= 0:
                literal = str(before)
                lengths = {10, 12} if name == "debtor_inn" else {11}
                if len(literal) in lengths and identifier_source_issue(name, literal, text) is None:
                    # Legacy JSON numbers can be formatted only when every
                    # digit is present verbatim. Never pad or reconstruct zeros.
                    format_input = literal
            item["value"] = normalize_court_value(name, format_input)
            if name == "debtor_full_name" and item["value"] is not None:
                item["value"], name_evidence = normalize_source_bound_surname(text, item["value"])
                if name_evidence is not None:
                    item.update(evidence=name_evidence.quote, evidence_start=name_evidence.start,
                                evidence_end=name_evidence.end)
                    item["reasoning"] = "Фамилия приведена к именительному падежу по однозначному ФИО текущего должника в акте."
            if name in {"debtor_inn", "debtor_snils"} and item["value"] is not None:
                issue = identifier_source_issue(name, item["value"], text)
                if issue is not None:
                    item.update(validation_issue=issue.message, reasoning=issue.message)
                    if issue.reject_value:
                        item.update(value=None, confidence=0)
            if name == "debtor_birth_place" and item["value"] is not None:
                item["value"] = normalize_explicit_district_boundary(item["value"])
                place_evidence = complete_explicit_republic_type(text, item["value"])
                if place_evidence is not None:
                    item.update(value=place_evidence.value, evidence=place_evidence.quote,
                                evidence_start=place_evidence.start, evidence_end=place_evidence.end)
                    item["reasoning"] = "Сохранено полное место рождения с прямо напечатанным историческим типом республики."
        except (ValueError, TypeError) as exc:
            item.update(value=None, confidence=0, validation_issue=str(exc), reasoning=str(exc))
        reason = item.get("reasoning") or _REASONS.get(name) or "Значение выбрано по контексту текущего должника в судебном акте."
        if method == "regex_jev" and not item.get("validation_issue"):
            reason = _REASONS.get(name, reason)
        value = item["value"]
        span = context.evidence(item)
        extra = []
        if value is None:
            item["source"] = None
            if item.get("validation_issue"):
                reason = str(item["validation_issue"])
            elif before is not None:
                reason = "Надёжного значения нет: пустое значение или текстовая заглушка приведены к null. " + reason
            else:
                reason = "Надёжное значение в документе не установлено. " + reason
            span = None
        elif name == "procedure_end_date_source" and value == "not_specified":
            item["source"] = "document"
            reason = "В документе не найдено ни явной даты окончания, ни назначенной продолжительности текущей процедуры."
            span = None
        elif name == "early_report_required" and value is False:
            # Absence has no positive quotation. Never quote the entire PDF as
            # if it contained an explicit printed «нет».
            reason = "При проверке действующих поручений суда обязанность заранее представить отчёт не установлена."
            if span and span[1:] == (0, len(text)):
                span = None
            item["source"] = context.section(*span[1:]) if span else "document"
        else:
            end_field = name in {"procedure_end_date", "procedure_end_date_is_calculated"}
            fallback = name == "next_session_date" and item.get("selection_basis") == "procedure_end_fallback"
            matches_end = (name == "procedure_end_date_is_calculated" and value is context.calculated
                           or name != "procedure_end_date_is_calculated" and value == context.end)
            if (end_field or fallback) and matches_end and context.term and not context.term.absence:
                term = context.term
                span = term.quote, term.start, term.end
                if context.calculated is True:
                    reason = "Окончание рассчитано прибавлением назначенного числа месяцев к дате текущего акта; результат расчёта не напечатан как готовая дата."
                    dates = extract_decision_date_candidates(text, require_act_title=False)
                    base = dates.selected
                    if base and base.role != "act_header":
                        base_value = legacy.extract_decision_date(text) or base.value
                        base = next((candidate for candidate in dates.candidates if candidate.value == base_value), None)
                    if base:
                        extra.append((text[base.start:base.end], base.start, base.end))
                else:
                    reason = "Дата окончания текущей процедуры прямо указана в документе."
                if fallback:
                    reason += " Явная дата следующего рассмотрения не установлена; использована действующая подстановка даты окончания процедуры."
            elif end_field:
                reason += " Признак расчёта и дата окончания сохранены по текущим правилам извлечения."
            if name == "early_report_deadline" and item.get("regex_rule") == "court_report_minus_ten_days":
                base = fields.get("next_session_date", {}).get("value")
                reason = f"Срок отчёта рассчитан по действующему правилу: следующая дата {base} минус 10 календарных дней; это расчёт, а не цитата готовой даты."
            elif name == 'early_report_deadline' and item.get('deadline_basis') == 'date_reference':
                reason = 'Срок отчёта соответствует однозначной ссылке на дату текущего распоряжения; вычитание дней не применяется.'
            elif name == "early_report_deadline" and item.get("regex_rule") == "court_explicit_report_date":
                reason = "Выбран явно напечатанный срок представления отчёта; он приоритетен перед расчётом минус 10 дней."
            item["source"] = context.section(*span[1:]) if span else None
        if span:
            citation = _quotation(span)
            if citation not in reason:
                reason += " " + citation
            item.update(evidence=span[0], evidence_start=span[1], evidence_end=span[2])
            for supporting in extra:
                reason += " Основание даты акта: " + _quotation(supporting)
        item["reasoning"] = reason
        output[name] = item
    return output

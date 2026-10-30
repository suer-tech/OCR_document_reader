"""Grounded fields for RTK2 reviews; no network calls or inferred hearing dates."""
from __future__ import annotations

import re
from datetime import datetime

from ocr_platform.services.court_case_number_format import normalize_court_case_number
from ocr_platform.services.court_decision_legacy_rules import CASE_NUMBER_RE, MONTHS, extract_case_number

REQUIRED_FIELDS = (
    "case_number", "decision_date", "debtor_full_name", "procedure_type",
    "creditor_name", "creditor_application_date", "review_required",
)
DATE = re.compile(
    r'(?<!\d)(?:\d{1,2}\.\d{1,2}\.\d{4}|[«"]?\d{1,2}[»"]?\s+'
    r'(?:' + "|".join(MONTHS) + r')\s+\d{4})(?!\d)', re.I,
)
_BODY = re.compile(r"\b(?:судья|в\s+составе|рассмотрев|рассмотрел|установил[аи]?)\b", re.I)
_OPERATIVE = re.compile(r"\bо\s*п\s*р\s*е\s*д\s*е\s*л\s*и\s*л[аи]?\s*:", re.I)
_SCHEDULE = re.compile(
    r"(?:назначить[^.;]{0,140}?(?:рассмотрение\s+требования|судебное\s+заседание|судебном\s+заседании)|"
    r"(?:судебное\s+заседание|рассмотрение\s+требования)[^.;]{0,120}?"
    r"(?:назначить|назначено|состоится))[^.;]{0,100}?\bна\s*", re.I,
)
_RECIPIENT = re.compile(r"\b(?:должник[ау]?|(?:финансов\w*|арбитражн\w*)\s+управляющ\w*)\b", re.I)
_SUBMIT = re.compile(r"\b(?:представить|предоставить|направить)\b", re.I)
_REVIEW = re.compile(r"\b(?:отзыв\w*|возражени\w*|пояснени\w*)\b", re.I)
_OPTIONAL = re.compile(r"\b(?:вправе|могут|может|при\s+наличии|при\s+необходимости)\b", re.I)
_UNCERTAIN = re.compile(r"\b(?:предложить|при\s+условии|в\s+случае|если|при\s+наличии|при\s+необходимости)\b", re.I)
_NEGATED = re.compile(r"\b(?:не\s+(?:обязан\w*|требуется|представлять|предоставлять|направлять))\b", re.I)
_INCOMING = re.compile(r"\b(?:вх\.?|входящ\w*|зарегистрир\w*|регистрац\w*|поступил\w*|получен\w*)\b", re.I)

# Appended after remotely managed field instructions so stale prompts cannot
# reinstate a calculated hearing date or the old keyword-only review policy.
CONTRACT_INSTRUCTION = """
RTK2 contract (takes precedence over older field instructions):
Keep the existing fields. creditor_name is the fullest name in nominative case
of the creditor whose CURRENT claim is being considered. creditor_application_date
is the filing date of THAT creditor's claim, never the debtor's bankruptcy filing,
publication, current/previous ruling, objection deadline or future review date.
An incoming receipt/registration date ('вх.', 'поступило', 'зарегистрировано')
MUST NOT substitute for a filing date. If only receipt/registration is documented,
return creditor_application_date=null; a human must review it. Quote an explicit
filing statement separately from any receipt/registration statement.
Return null when absent. decision_date is the CURRENT ruling's header date.
hearing_date is ONLY an explicitly scheduled hearing of this claim, otherwise null;
NEVER calculate decision_date + one month or use a deadline for written review.
review_required is true ONLY for a direct court obligation on the debtor or
financial/arbitration manager to submit a review, objections to this claim, or
explanations that replace a review. General rights ('вправе'), service phrases
('отзывы могут быть представлены') and historical obligations are not duties.
Return false when there is no duty, null when ambiguous. For every non-null
creditor_name and creditor_application_date provide a verbatim evidence quote
in <field>_quote; the filing quote must name this creditor and the filing action.
For review_required provide review_required_quote from the CURRENT operative part
and quote that phrase in reasoning. Explain ambiguity instead of guessing.
"""


def compact(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def date_value(value: str) -> str | None:
    try:
        if re.fullmatch(r"\d{1,2}\.\d{1,2}\.\d{4}", value):
            parsed = datetime.strptime(value, "%d.%m.%Y")
        else:
            parts = re.sub(r'[«»"]', "", compact(value)).lower().split()
            parsed = datetime(int(parts[2]), int(MONTHS[parts[1]]), int(parts[0]))
        return parsed.strftime("%d.%m.%Y")
    except (ValueError, KeyError, IndexError, TypeError):
        return None


def header(text: str) -> str:
    text = text[:2000]
    boundary = _BODY.search(text)
    return text[:boundary.start()] if boundary else text


def case_field(text: str) -> dict:
    current_header = header(text)
    numbers = [match[1] for match in CASE_NUMBER_RE.finditer(current_header)]
    first = extract_case_number(current_header)
    if first:
        numbers.append(first)
    bases = set()
    for number in numbers:
        match = re.match(r"А\d{1,3}-\d+/\d{4}(?!\d)", normalize_court_case_number(number))
        if match:
            bases.add(match[0])
    value = bases.pop() if len(bases) == 1 else None
    return {
        "value": value, "confidence": 1.0 if value else 0.0,
        "source": "header", "extraction_method": "regex",
        "reasoning": "Основной номер дела из шапки, без номера обособленного спора."
        if value else "Основной номер дела не установлен по шапке документа.",
    }


def _set(info: dict, value, reason: str, source: str, *, issue: str | None = None) -> None:
    info.update(value=value, reasoning=reason, source=source)
    if issue:
        info.update(confidence=0.0, validation_issue=issue)
    else:
        info.pop("validation_issue", None)


def _quote(info: dict, text: str) -> str:
    quote = compact(info.get("evidence_quote") or "")
    return quote if quote and quote in compact(text) else ""


def _creditor_in_quote(name: str, quote: str) -> bool:
    quoted_name = re.search(r"[«\"]([^»\"]+)[»\"]", name)
    if quoted_name:
        return compact(quoted_name[1]).casefold() in compact(quote).casefold()
    common = {"общество", "общества", "акционерное", "публичное", "банк", "пао", "ао", "ооо",
              "ограниченной", "ответственностью", "индивидуальный", "предприниматель"}
    words = [word for word in re.findall(r"[а-яёa-z]+", name.lower()) if len(word) >= 4 and word not in common]
    return bool(words) and all(word[:-1] in quote.lower() for word in words)


def guard_contract(text: str, fields: dict[str, dict]) -> dict[str, dict]:
    """Validate evidence before storage; preserve unrelated legacy fields."""
    if "case_number" in fields:
        fields["case_number"] = case_field(text)

    if "decision_date" in fields:
        dates = {date_value(m[0]) for m in DATE.finditer(header(text))} - {None}
        info = fields["decision_date"]
        if len(dates) == 1:
            _set(info, dates.pop(), "Дата текущего определения указана в шапке.", "header")
            info["confidence"] = 1.0
        else:
            _set(info, None, "Дата текущего определения не установлена однозначно по шапке.", "header",
                 issue="Неоднозначная или отсутствующая дата текущего определения.")

    creditor = fields.get("creditor_name")
    if creditor and creditor.get("value") is not None:
        quote = _quote(creditor, text)
        name = creditor["value"]
        claim = re.search(r"\b(?:заявлен\w*|требован\w*)\b", quote, re.I)
        if not isinstance(name, str) or not quote or not claim or not _creditor_in_quote(name, quote):
            _set(creditor, None, "Нет подтверждённой цитаты о кредиторе текущего требования.", "rtk2_evidence_guard",
                 issue="Не подтверждено наименование кредитора текущего требования.")
        else:
            # The model may infer a succession/assignment between organizations.
            # A grounded name does not establish those additional facts.
            _set(creditor, compact(name), f'Наименование по тексту рассматриваемого требования. Цитата: «{quote}».',
                 "rtk2_combined")

    filing = fields.get("creditor_application_date")
    if filing:
        quote = _quote(filing, text)
        name = (creditor or {}).get("value")
        dates = {date_value(m[0]) for m in DATE.finditer(quote)} - {None}
        raw_value = filing.get("value")
        value = date_value(raw_value) if isinstance(raw_value, str) else None
        action = re.search(r"\b(?:обратил\w*|подал\w*|подан\w*|направил\w*|направлен\w*)\b", quote, re.I)
        claim = re.search(r"\b(?:заявлен\w*|требован\w*)\b", quote, re.I)
        if _INCOMING.search(quote):
            _set(filing, None, "Указана входящая дата поступления/регистрации заявления; она не заменяет отдельную дату подачи.",
                 "rtk2_evidence_guard", issue="Входящая дата не подтверждает дату подачи заявления; требуется ручная проверка.")
        elif raw_value is None:
            pass
        elif not value or dates != {value} or not name or not _creditor_in_quote(name, quote) or not action or not claim:
            _set(filing, None, "Дата не подтверждена цитатой о подаче заявления именно этим кредитором.", "rtk2_evidence_guard",
                 issue="Не подтверждена дата подачи заявления текущим кредитором.")
        else:
            _set(filing, value, f'Дата подачи заявления кредитором. Цитата: «{quote}».', "rtk2_combined")

    headings = list(_OPERATIVE.finditer(text))
    operative = text[headings[-1].end():] if headings else text
    operative = compact(operative)
    if "hearing_date" in fields:
        dates = set()
        for scheduled in _SCHEDULE.finditer(operative):
            match = DATE.match(operative, scheduled.end())
            if match and date_value(match[0]):
                dates.add(date_value(match[0]))
        info = fields["hearing_date"]
        if len(dates) == 1:
            _set(info, dates.pop(), "Явно назначенная дата заседания в резолютивной части.", "resolutive_part")
            info["confidence"] = 1.0
        else:
            _set(info, None, "Однозначная дата судебного заседания не назначена; сроки отзыва и рассмотрения не являются датой СЗ.",
                 "resolutive_part", issue="Указаны разные даты заседания; требуется проверка." if len(dates) > 1 else None)

    if "review_required" in fields:
        info = fields["review_required"]
        # Keep dates, initials and decimal numbers intact when splitting orders.
        clauses = re.split(r"(?<=[.;])\s+(?=[А-ЯЁ])|(?:^|\s)\d+[.)]\s+(?=[А-ЯЁ])", operative)
        duties, ambiguous, service = [], [], []
        for clause in clauses:
            # A conditional request to the creditor in the same numbered item
            # must not make the preceding unconditional manager duty ambiguous.
            clause = re.split(r",\s*(?:а\s+)?(?=кредитору\b)", clause, flags=re.I)[0]
            if not _REVIEW.search(clause):
                continue
            service.append(clause)
            if re.search(r"\b(?:апелляцион\w*|кассацион\w*)\s+жалоб\w*", clause, re.I):
                continue
            if not (_RECIPIENT.search(clause) and _SUBMIT.search(clause)) or _NEGATED.search(clause):
                continue
            if _UNCERTAIN.search(clause):
                ambiguous.append(clause)
            elif _OPTIONAL.search(clause):
                continue
            elif re.search(r"\bвозражени\w*\b", clause, re.I) and not re.search(r"\b(?:требован\w*|отзыв\w*)\b", clause, re.I):
                ambiguous.append(clause)
            elif re.search(r"\bпояснени\w*\b", clause, re.I) and not re.search(r"\b(?:отзыв\w*|возражени\w*)\b", clause, re.I):
                quote = _quote(info, operative)
                if info.get("value") is True and quote and quote in clause and "требован" in clause.lower():
                    duties.append(clause)
                else:
                    ambiguous.append(clause)
            else:
                duties.append(clause)
        if duties:
            _set(info, True, f'Прямая обязанность представить отзыв: «{duties[0]}».', "resolutive_part")
            info["confidence"] = 1.0
        elif ambiguous or info.get("value") is None:
            phrase = ambiguous[0] if ambiguous else (_quote(info, operative) or operative[:240])
            _set(info, None, f'Обязанность представить отзыв неоднозначна. Цитата: «{phrase}».', "resolutive_part",
                 issue="Неоднозначное требование представить отзыв.")
        else:
            phrase = service[0] if service else operative[:240]
            _set(info, False, f'Прямой обязанности представить отзыв нет. Фрагмент: «{phrase}».', "resolutive_part")
            info["confidence"] = 1.0
    return fields


def rtk2_issues(fields: dict) -> list:
    """Use the same completeness/ambiguity policy in worker, webhook and GET."""
    from ocr_platform.api.schemas import ValidationIssue

    issues = []
    for name in REQUIRED_FIELDS:
        info = fields.get(name)
        value = info.get("value") if isinstance(info, dict) else None
        if value is None or value == "" or value == []:
            issues.append(ValidationIssue(
                code="rtk2_requires_review", field_name=name, severity="error",
                message=f"Не установлено обязательное значение РТК2: {name}.",
            ))
    for name, info in fields.items():
        if not isinstance(info, dict):
            continue
        value = info.get("value")
        problem = info.get("validation_issue")
        if name in {"decision_date", "creditor_application_date", "hearing_date"} and value is not None:
            if not isinstance(value, str) or not re.fullmatch(r"\d{2}\.\d{2}\.\d{4}", value) or date_value(value) != value:
                problem = "Некорректная календарная дата РТК2."
        elif name == "review_required" and value is not None and not isinstance(value, bool):
            problem = "review_required должен быть boolean или null."
        elif name == "case_number" and value is not None:
            if not isinstance(value, str) or not re.fullmatch(r"А\d{1,3}-\d+/\d{4}", value):
                problem = "Требуется основной номер дела без номера обособленного спора."
        elif name == "creditor_name" and value is not None and not isinstance(value, str):
            problem = "Наименование кредитора должно быть строкой или null."
        if problem:
            issues.append(ValidationIssue(code="rtk2_requires_review", field_name=name,
                                          message=str(problem), severity="error"))
    return issues

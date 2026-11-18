"""The same RTK3 checks run before storage, in webhooks and in GET results."""
from __future__ import annotations

import re
from decimal import Decimal

from ocr_platform.api.schemas import ValidationIssue
from ocr_platform.services.rtk3_evidence import (
    GROUNDS, PLEDGE_STATUSES, QUEUES, VERSION, date_value, is_duty_only, money, normalize_case, row_pledges,
)


def is_v2(fields: dict) -> bool:
    return any(name != "kad_publication_date" and isinstance(info, dict) and info.get("source") == VERSION
               for name, info in fields.items())


def rtk3_issues(fields: dict) -> list[ValidationIssue]:
    issues = []
    modern = is_v2(fields)

    def add(field, message):
        issues.append(ValidationIssue(code="rtk3_requires_review", field_name=field,
                                      message=message, severity="error"))

    def value(name):
        info = fields.get(name)
        return info.get("value") if isinstance(info, dict) else None

    if modern:
        required = ["document_case_number", "case_number", "debtor_inn", "creditor", "creditor_inn",
                    "inclusion_date", "claims"]
        for name in required:
            if value(name) in (None, "", []) and not fields.get(name, {}).get("external_data_required"):
                add(name, f"Не установлено обязательное значение РТК3: {name}.")
        for name in ["debtor_inn", "creditor_inn"]:
            if value(name) is None and fields.get(name, {}).get("external_data_required"):
                issues.append(ValidationIssue(
                    code="rtk3_external_data_required", field_name=name, severity="warning",
                    message=f"Поле {name} требует проверенного внешнего источника; это не подтверждённое значение из документа."))
        for name, info in fields.items():
            if name != "kad_publication_date" and isinstance(info, dict) and info.get("validation_issue"):
                add(name, str(info["validation_issue"]))
        for name in ["debtor_inn", "creditor_inn"]:
            inn = value(name)
            if inn is not None and not (isinstance(inn, str) and re.fullmatch(
                    r"[0-9]{12}" if name == "debtor_inn" else r"(?:[0-9]{10}|[0-9]{12})", inn)):
                add(name, "Некорректный строковый формат ИНН.")
        date = value("inclusion_date")
        if date is not None and (not isinstance(date, str) or date_value(date, date) is None):
            add("inclusion_date", "Некорректная календарная дата.")
        document, case = normalize_case(value("document_case_number"))
        if document and case != value("case_number"):
            add("case_number", "Основное дело не соответствует номеру обособленного спора.")
        if value("document_case_number") and document is None:
            add("document_case_number", "Некорректный номер документа.")
        if value("grounds") is not None and value("grounds") not in GROUNDS:
            add("grounds", "Основание не относится к согласованным категориям.")
    rows = value("claims")
    if not isinstance(rows, list):
        return issues
    individual_pledges = modern and any(isinstance(row, dict) and "pledge_resolution" in row for row in rows)
    nets = []
    for i, row in enumerate(rows):
        field = f"claims/{i}"
        if not isinstance(row, dict):
            add(field, "Некорректная строка требований.")
            continue
        names = ["principal_debt", "financial_sanctions", "total_amount"]
        if modern or "pau_only_amount" in row:
            names.append("pau_only_amount")
        parsed = {name: money(row.get(name)) for name in names}
        for name, amount in parsed.items():
            raw = row.get(name)
            if (raw not in (None, "") and amount is None) or (modern and (
                    not isinstance(raw, str) or re.fullmatch(r"[0-9]+\.[0-9]{2}", raw) is None)):
                add(field, f"Некорректная или неизвестная сумма: {name}.")
        if parsed["principal_debt"] is not None and parsed["financial_sanctions"] is not None:
            net = parsed["principal_debt"] + parsed["financial_sanctions"]
            if modern and money(row.get("claimed_amount")) != net:
                add(field, "claimed_amount не соответствует включённой сумме без пошлины.")
        else:
            net = money(row.get("claimed_amount")) if modern else None
        if net is not None:
            nets.append(net)
            # Confirmed EMU convention: total_amount is the PAU-inclusive total.
            duty, total = parsed.get("pau_only_amount", Decimal(0)), parsed["total_amount"]
            if duty is not None and total is not None and net + duty != total:
                add(field, "Сумма долга, санкций и пошлины не совпадает с итогом строки.")
        if modern and row.get("priority_queue") not in QUEUES and not is_duty_only(row):
            add(field, "Не установлена очередь требования.")
        if modern and not isinstance(row.get("is_salary_or_severance"), bool):
            add(field, "Не установлен признак зарплаты/выходного пособия.")
        if individual_pledges and not is_duty_only(row):
            pledge = row.get("pledge_resolution")
            if pledge not in PLEDGE_STATUSES or row.get("secured_by_pledge") is not (pledge == "yes"):
                add(f"{field}/pledge_resolution", "Не установлен залоговый статус отдельного требования.")
    if modern:
        pledge = value("pledge_resolution")
        if individual_pledges:
            statuses = row_pledges([row for row in rows if isinstance(row, dict)])
            expected = next(iter(statuses)) if len(statuses) == 1 else None
            if pledge != expected or value("secured_by_pledge") is not (expected == "yes" if expected else None):
                add("secured_by_pledge", "Общий залоговый статус не согласуется со строками требований.")
        elif pledge not in PLEDGE_STATUSES or value("secured_by_pledge") is not (pledge == "yes"):
            add("secured_by_pledge", "Не установлен согласованный результат рассмотрения залога.")
        raw_total = value("total_claimed_amount")
        if not isinstance(raw_total, str) or not re.fullmatch(r"[0-9]+\.[0-9]{2}", raw_total):
            add("total_claimed_amount", "Не установлена общая сумма требований без пошлины.")
        elif len(nets) != len(rows) or sum(nets, Decimal(0)) != money(raw_total):
            add("total_claimed_amount", "Общий итог не совпадает с включёнными требованиями без пошлины.")
    return issues

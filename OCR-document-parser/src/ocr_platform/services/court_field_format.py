"""Court value presentation only; no date arithmetic or source selection."""
from __future__ import annotations

from datetime import datetime
import re
from typing import Any

from ocr_platform.services.court_case_number_format import normalize_court_case_number

_EMPTY = {"", "null", "none", "не найдено", "не обнаружено", "нет данных", "не указано", "-", "—"}
_NAME_KEYS = ("debtor_last_name", "debtor_first_name", "debtor_patronymic")


def normalize_court_value(name: str, value: Any) -> Any:
    """Normalize accepted spelling, reject wrong types, never invent content."""
    if value is None:
        return None
    if isinstance(value, str):
        if value.strip().casefold() in _EMPTY:
            return None
        if name not in {"motivating_part", "resolutive_part"}:
            value = " ".join(value.split())
    if name in {"early_report_required", "procedure_end_date_is_calculated"}:
        if type(value) is not bool:
            raise ValueError("Признак должен быть JSON boolean или null, не строкой/числом")
    elif name == "procedure_duration_months":
        if type(value) is not int or value <= 0:
            raise ValueError("Продолжительность должна быть положительным целым числом месяцев")
    elif name == "debtor_full_name":
        if isinstance(value, dict):
            if set(value) - set(_NAME_KEYS):
                raise ValueError("Неизвестные компоненты ФИО должника")
            value = {key: normalize_court_value(key, value.get(key)) for key in _NAME_KEYS}
            return value if any(part is not None for part in value.values()) else None
        if not isinstance(value, str):
            raise ValueError("ФИО должно быть строкой или объектом компонентов")
    elif name == "debtor_registration_address" and isinstance(value, dict):
        keys = {"post_index", "region", "city", "street"}
        if set(value) - keys:
            raise ValueError("Неизвестные компоненты адреса регистрации")
        value = {key: normalize_court_value(key, part) for key, part in value.items()}
        return value if any(part is not None for part in value.values()) else None
    else:
        if not isinstance(value, str):
            raise ValueError("Значение должно быть строкой или null; ведущие нули сохраняются")
        if name.endswith("_date") or name == "early_report_deadline":
            if not re.fullmatch(r"[0-9]{1,2}\.[0-9]{1,2}\.[0-9]{4}", value):
                raise ValueError("Дата должна иметь формат DD.MM.YYYY")
            value = datetime.strptime(value, "%d.%m.%Y").strftime("%d.%m.%Y")
        elif name == "next_session_time":
            match = re.fullmatch(r"([0-9]{1,2}):([0-9]{2})", value)
            if not match or int(match[1]) > 23 or int(match[2]) > 59:
                raise ValueError("Время должно иметь формат HH:MM")
            value = f"{int(match[1]):02d}:{int(match[2]):02d}"
        elif name == "case_number":
            value = normalize_court_case_number(value)
        elif name == "debtor_inn":
            value = re.sub(r"\s", "", value)
            if not re.fullmatch(r"(?:[0-9]{10}|[0-9]{12})", value):
                raise ValueError("ИНН должен содержать 10 или 12 цифр")
        elif name == "debtor_snils":
            digits = re.sub(r"[\s‐‑–—-]", "", value)
            if not re.fullmatch(r"[0-9]{11}", digits):
                raise ValueError("СНИЛС должен содержать 11 цифр")
            value = f"{digits[:3]}-{digits[3:6]}-{digits[6:9]} {digits[9:]}"
        elif name == "procedure_type":
            folded = value.casefold()
            if re.fullmatch(r"(?:процедур[аыу]\s+)?реализаци[яи]\s+имущества(?:\s+(?:гражданина|должника))?", folded):
                value = "Реализация имущества"
            elif re.fullmatch(r"(?:процедур[аыу]\s+)?реструктуризаци[яи]\s+долгов(?:\s+гражданина)?", folded):
                value = "Реструктуризация долгов"
            else:
                raise ValueError("Неизвестный тип процедуры")
        elif name == "procedure_end_date_source":
            value = "not_specified" if value == "not_found" else value
            if value not in {"explicit_date", "explicit_duration", "not_specified"}:
                raise ValueError("Неизвестный способ задания срока процедуры")
        elif name == "debtor_gender" and value not in {"Мужской", "Женский"}:
            raise ValueError("Пол должен быть Мужской, Женский или null")
        elif name == "document_basis" and value not in {"Решение", "Определение"}:
            raise ValueError("Вид акта должен быть Решение, Определение или null")
    return value

"""Additional court fields, independent of the legacy date extraction rules."""
from __future__ import annotations

from datetime import datetime
import re
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, Field, StrictInt

T = TypeVar("T")


class CourtField(BaseModel, Generic[T]):
    value: T | None
    reasoning: str
    confidence: float = Field(ge=0, le=1)
    ambiguous: bool = Field(description="Есть неразрешимое противоречие или неоднозначность")
    evidence: str | None = Field(description="Дословный фрагмент документа, подтверждающий значение")


class CourtAdditionalResult(BaseModel):
    application_acceptance_date: CourtField[str]
    next_session_date: CourtField[str]
    next_session_time: CourtField[str]
    court_hearing_address: CourtField[str]
    procedure_duration_months: CourtField[StrictInt]
    procedure_end_date_source: CourtField[Literal["explicit_date", "explicit_duration", "not_found"]]


ADDITIONAL_FIELDS = tuple(CourtAdditionalResult.model_fields)
COURT_ISSUE_CODE = "court_field_requires_review"
JOINT_INSTRUCTION = """
Извлеки дополнительные поля судебного акта, не используя значения старых полей.
Анализируй полный документ, в первую очередь распоряжения после РЕШИЛ и ОПРЕДЕЛИЛ,
в том числе при пробелах между буквами. Исторические события и цитаты закона не
считай новыми распоряжениями суда. Будущее заседание определяется относительно
акта, а не сегодняшней даты. Если назначение изменено, учитывай окончательное
распоряжение; если выбор между несколькими назначениями неоднозначен, не угадывай.
Дата, время и адрес должны относиться к одному и тому же следующему заседанию.
Для каждого поля верни value, reasoning, confidence (0..1), ambiguous и evidence.
evidence — дословная цитата из переданного текста, не пересказ; можно включить
несколько предложений подряд. Для ненайденного значения верни null и ambiguous=false.
Исключение: procedure_end_date_source=not_found, если нет ни даты, ни срока процедуры.
При противоречии или неоднозначности верни value=null, ambiguous=true и объяснение.
Не подставляй текущую дату, 01.01.1970 или 00:00. Даты: DD.MM.YYYY, время: HH:MM.
"""


def _normalized(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold().replace("ё", "е")


def additional_fields_result(data: CourtAdditionalResult, text: str) -> dict[str, dict]:
    result = {}
    for name in ADDITIONAL_FIELDS:
        item = getattr(data, name)
        result[name] = {
            "value": item.value,
            "reasoning": item.reasoning,
            "confidence": item.confidence,
            "source": "court_decision",
        }

        def reject(reason: str) -> None:
            result[name].update(value=None, confidence=0, reasoning=reason, validation_issue=reason)

        if item.ambiguous:
            reject(item.reasoning or "Неоднозначное значение в судебном акте")
            continue
        if item.value is None:
            if name == "procedure_end_date_source":
                result[name]["value"] = "not_found"
            continue
        if item.value != "not_found" and (
            not item.evidence or _normalized(item.evidence) not in _normalized(text)
        ):
            reject("Значение не подтверждено дословным фрагментом документа")
            continue
        if name.endswith("_date"):
            try:
                if not re.fullmatch(r"\d{2}\.\d{2}\.\d{4}", item.value):
                    raise ValueError
                datetime.strptime(item.value, "%d.%m.%Y")
            except ValueError:
                reject("Некорректная календарная дата: " + item.value)
        elif name == "next_session_time":
            match = re.fullmatch(
                r"\s*(\d{1,2})(?::|\s*час(?:\.|ов|а)?\s*)(\d{2})(?:\s*мин(?:\.|ут|уты)?)?\s*",
                item.value,
            )
            if not match or int(match[1]) > 23 or int(match[2]) > 59:
                reject("Некорректное время заседания: " + item.value)
            else:
                result[name]["value"] = f"{int(match[1]):02d}:{int(match[2]):02d}"
        elif name == "procedure_duration_months" and item.value <= 0:
            reject("Продолжительность процедуры должна быть положительным числом месяцев")
        elif name == "court_hearing_address":
            result[name]["value"] = item.value.strip() or None

    if result["next_session_date"]["value"] is None:
        for name in ("next_session_time", "court_hearing_address"):
            if result[name]["value"] is not None or result["next_session_date"].get("validation_issue"):
                reason = "Нельзя связать значение с однозначно определённой датой следующего заседания"
                result[name].update(value=None, confidence=0, reasoning=reason, validation_issue=reason)
    source = result["procedure_end_date_source"]
    duration = result["procedure_duration_months"]
    if (source["value"] == "explicit_duration" and duration["value"] is None) or (
        source["value"] == "not_found" and duration["value"] is not None
    ):
        reason = "Способ задания срока противоречит извлечённой продолжительности процедуры"
        source.update(value=None, confidence=0, reasoning=reason, validation_issue=reason)
    return result


def additional_fields_failure() -> dict[str, dict]:
    reason = "Не удалось извлечь дополнительные поля судебного акта; требуется ручная проверка"
    return {
        name: dict(value=None, confidence=0, reasoning=reason,
                   source="court_decision", validation_issue=reason)
        for name in ADDITIONAL_FIELDS
    }

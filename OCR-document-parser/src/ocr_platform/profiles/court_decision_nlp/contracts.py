"""Контракты для NLP-экстракции судебных решений."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class FioComponents:
    last_name: str | None = None
    first_name: str | None = None
    patronymic: str | None = None

    @property
    def normalized(self) -> str | None:
        parts = [self.last_name, self.first_name, self.patronymic]
        result = " ".join(part for part in parts if part)
        return result or None


@dataclass(slots=True)
class CourtDecisionFields:
    applicant_fio: FioComponents
    judge_fio: FioComponents
    court_name: str | None
    case_number: str | None
    inn: str | None
    decision_date: str | None
    procedure_end_date: str | None
    procedure_type: str | None
    early_report_deadline: str | None = None
    early_report_deadline_source: str | None = None
    motivating_part: str | None = None
    resolutive_part: str | None = None
    procedure_end_date_is_calculated: bool | None = None


@dataclass(slots=True)
class PredictionResult:
    fields: CourtDecisionFields
    confidence: float
    source_text_span: str | None
    source_text_preview: str | None
    model_version: str

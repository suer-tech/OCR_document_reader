from __future__ import annotations

from pydantic import BaseModel, Field, constr


class ExtractRequest(BaseModel):
    pdf_base64: constr(min_length=1) = Field(
        ...,
        description="Base64-encoded PDF bytes without transport metadata.",
    )
    document_id: str | None = Field(default=None, description="Client-supplied identifier.")


class FioPayload(BaseModel):
    last_name: str | None = None
    first_name: str | None = None
    patronymic: str | None = None
    normalized: str | None = None


class ExtractResponse(BaseModel):
    applicant_fio: FioPayload
    judge_fio: FioPayload
    court_name: str | None = None
    case_number: str | None = None
    inn: str | None = None
    decision_date: str | None = None
    procedure_end_date: str | None = None
    procedure_type: str | None = None
    confidence: float = Field(..., ge=0.0, le=1.0)
    source_text_span: str | None = None
    source_text_preview: str | None = None
    model_version: str
    document_id: str | None = None


class HealthResponse(BaseModel):
    status: str
    model_version: str
    backend: str
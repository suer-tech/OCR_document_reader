from __future__ import annotations

from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, Field


SourceType = Literal["crm", "email", "portal", "external", "other"]
ContentType = Literal["pdf", "image", "text"]


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"


class IngestDocumentRequest(BaseModel):
    source_type: SourceType = Field(..., description="Источник документа (CRM, email, личный кабинет и т.д.).")
    document_type: Optional[str] = Field(
        default=None,
        description="Явно переданный тип документа. Если не передан, система определяет тип автоматически.",
    )
    document_type_hint: Optional[str] = Field(
        default=None,
        description="Устаревающее поле-подсказка типа документа (например, 'court_decision').",
    )
    content_type: ContentType = Field(..., description="Тип содержимого: pdf, image или text.")
    content_base64: str = Field(..., description="Содержимое документа, закодированное в base64.")
    idempotency_key: Optional[str] = Field(
        default=None,
        description="Ключ идемпотентности. При повторе возвращается существующий pipeline_run_id.",
    )
    external_id: Optional[str] = Field(
        default=None,
        description="Внешний идентификатор документа в исходной системе.",
    )
    meta: Dict[str, Any] = Field(default_factory=dict, description="Произвольные метаданные о документе.")


class IngestDocumentResponse(BaseModel):
    document_id: str
    pipeline_run_id: str
    resolved_document_type: Optional[str] = None
    resolved_profile_id: Optional[str] = None
    detection_source: Optional[str] = None
    detection_model: Optional[str] = None
    idempotency_key: str
    status: Literal["queued", "processing", "retrying", "done", "failed"]


class FieldValue(BaseModel):
    name: str
    value: Any
    reasoning: Optional[str] = None
    confidence: Optional[float] = None
    source: Optional[str] = None


class ValidationIssue(BaseModel):
    code: str
    message: str
    field_name: Optional[str] = None
    severity: Literal["info", "warning", "error"] = "error"


class DocumentResultResponse(BaseModel):
    document_id: str
    pipeline_run_id: str
    raw_text_version_id: Optional[str] = None
    structured_version_id: Optional[str] = None

    raw_text: Optional[str] = None
    fields: Dict[str, FieldValue] = Field(default_factory=dict)

    technical_quality_score: Optional[float] = None
    semantic_confidence_score: Optional[float] = None
    overall_quality_score: Optional[float] = None

    validation_status: Literal["ok", "warnings", "errors"]
    validation_issues: list[ValidationIssue] = Field(default_factory=list)

    human_review_required: bool
    human_review_reason: Optional[str] = None


class PipelineRunStatusResponse(BaseModel):
    pipeline_run_id: str
    document_id: str
    profile_id: Optional[str] = None
    status: Literal["queued", "processing", "retrying", "done", "failed"]
    retry_count: int = 0
    created_at: str
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    last_error: Optional[str] = None


class HumanReviewTaskField(BaseModel):
    field_id: str
    field_name: str
    value_system: Any
    value_human: Optional[Any] = None
    source: Optional[str] = None
    error_type: Optional[str] = None
    correction_reason: Optional[str] = None


class HumanReviewTask(BaseModel):
    task_id: str
    document_id: str
    pipeline_run_id: str
    profile_id: str
    fields: list[HumanReviewTaskField]
    overall_quality_score: Optional[float] = None
    created_at: str


class HumanReviewTaskListResponse(BaseModel):
    tasks: list[HumanReviewTask]


class SubmitHumanReviewRequest(BaseModel):
    fields: list[HumanReviewTaskField]


class SubmitHumanReviewResponse(BaseModel):
    task_id: str
    document_id: str
    structured_version_id: str


class MlflowBackfillRequest(BaseModel):
    limit: int = Field(default=100, ge=1, le=1000, description="Максимум run-ов для backfill за один вызов.")
    force: bool = Field(
        default=False,
        description="Если true, отправляет в MLflow даже если run уже найден по tag pipeline_run_id.",
    )


class MlflowBackfillFailedItem(BaseModel):
    pipeline_run_id: str
    error: str


class MlflowBackfillResponse(BaseModel):
    total_candidates: int
    processed: int
    logged: int
    skipped_existing: int
    skipped_incomplete: int
    failed: int
    logged_run_ids: list[str] = Field(default_factory=list)
    failed_items: list[MlflowBackfillFailedItem] = Field(default_factory=list)


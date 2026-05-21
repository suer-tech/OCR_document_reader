from __future__ import annotations

import base64
import hashlib
from datetime import datetime

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from sqlalchemy.exc import IntegrityError

from ocr_platform.api import schemas
from ocr_platform.config.settings import get_settings
from ocr_platform.observability.logging import configure_logging, get_logger
from ocr_platform.observability.metrics import inc_request
from ocr_platform.orchestration.mlflow_backfill import backfill_pipeline_runs_to_mlflow
from ocr_platform.queueing.rabbitmq import IngestJob, publish_ingest_job
from ocr_platform.storage import file_storage, models, repository

logger = get_logger(__name__)


def _infer_content_type(filename: str, content_type: str) -> str:
    """Определяет content_type по имени файла или MIME."""
    ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    if ext in ("pdf",) or "pdf" in content_type.lower():
        return "pdf"
    if ext in ("png", "jpg", "jpeg", "gif", "bmp", "webp") or content_type.lower().startswith("image/"):
        return "image"
    if ext in ("txt",) or content_type.lower().startswith("text/"):
        return "text"
    return "unknown"


def _build_idempotency_key(content_hash: str, source_type: str, external_id: str | None) -> str:
    raw_key = f"{content_hash}:{source_type}:{external_id or ''}"
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def _format_ts(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def create_app() -> FastAPI:
    configure_logging()
    repository.init_db()

    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description="OCR-платформа для судебных и других документов.",
    )

    @app.get("/health", response_model=schemas.HealthResponse)
    async def health() -> schemas.HealthResponse:
        return schemas.HealthResponse(status="ok")

    @app.post(
        "/documents/ingest",
        response_model=schemas.IngestDocumentResponse,
        status_code=202,
    )
    async def ingest_document(request: schemas.IngestDocumentRequest) -> schemas.IngestDocumentResponse:
        inc_request("/documents/ingest")

        external_id = request.external_id or str(request.meta.get("external_id", "")).strip() or None
        try:
            content_bytes = base64.b64decode(request.content_base64)
        except Exception as exc:
            raise HTTPException(status_code=400, detail="content_base64 is invalid") from exc
        content_hash = hashlib.sha256(content_bytes).hexdigest()
        idempotency_key = request.idempotency_key or _build_idempotency_key(
            content_hash=content_hash,
            source_type=request.source_type,
            external_id=external_id,
        )
        requested_document_type = request.document_type or request.document_type_hint

        with repository.get_session() as session:
            existing = (
                session.query(models.IngestRequest)
                .filter(models.IngestRequest.idempotency_key == idempotency_key)
                .first()
            )
            if existing:
                run = session.get(models.PipelineRun, existing.pipeline_run_id)
                if run:
                    return schemas.IngestDocumentResponse(
                        document_id=existing.document_id,
                        pipeline_run_id=existing.pipeline_run_id,
                        status=run.status,  # type: ignore[arg-type]
                        idempotency_key=idempotency_key,
                    )

        document_id = repository.generate_id()
        pipeline_run_id = repository.generate_id()
        storage_path = file_storage.save_document_bytes(
            document_id=document_id,
            content=content_bytes,
            content_type=request.content_type,
        )

        try:
            with repository.get_session() as session:
                doc = models.Document(
                    id=document_id,
                    source_type=request.source_type,
                    document_type=requested_document_type,
                )
                session.add(doc)

                session.add(
                    models.DocumentFile(
                        document_id=document_id,
                        storage_path=storage_path,
                        file_type=request.content_type,
                    )
                )

                session.add(
                    models.PipelineRun(
                        id=pipeline_run_id,
                        document_id=document_id,
                        profile_id="unknown",
                        status="queued",
                        idempotency_key=idempotency_key,
                        webhook_url=request.webhook_url or request.meta.get("webhook_url"),
                    )
                )

                session.add(
                    models.IngestRequest(
                        idempotency_key=idempotency_key,
                        request_hash=content_hash,
                        source_type=request.source_type,
                        external_id=external_id,
                        document_id=document_id,
                        pipeline_run_id=pipeline_run_id,
                    )
                )
                session.commit()
        except IntegrityError:
            with repository.get_session() as session:
                existing = (
                    session.query(models.IngestRequest)
                    .filter(models.IngestRequest.idempotency_key == idempotency_key)
                    .first()
                )
                if not existing:
                    logger.exception("Unexpected IntegrityError during ingest_document")
                    raise HTTPException(status_code=409, detail="duplicate ingest detected")
                run = session.get(models.PipelineRun, existing.pipeline_run_id)
                status = run.status if run else "queued"
                return schemas.IngestDocumentResponse(
                    document_id=existing.document_id,
                    pipeline_run_id=existing.pipeline_run_id,
                    status=status,  # type: ignore[arg-type]
                    idempotency_key=idempotency_key,
                )

        try:
            publish_ingest_job(IngestJob(pipeline_run_id=pipeline_run_id, attempt=0))
        except Exception as exc:
            with repository.get_session() as session:
                run = session.get(models.PipelineRun, pipeline_run_id)
                if run:
                    run.status = "failed"
                    run.last_error = f"queue_publish_failed: {exc}"
                    run.finished_at = datetime.utcnow()
                    session.commit()
            logger.exception("ingest_enqueue_failed", pipeline_run_id=pipeline_run_id)
            raise HTTPException(status_code=503, detail="failed to enqueue pipeline run") from exc

        logger.info(
            "ingest_enqueued",
            document_id=document_id,
            pipeline_run_id=pipeline_run_id,
            idempotency_key=idempotency_key,
            source_type=request.source_type,
            content_type=request.content_type,
            has_document_type=bool(request.document_type),
            has_document_type_hint=bool(request.document_type_hint),
        )
        return schemas.IngestDocumentResponse(
            document_id=document_id,
            pipeline_run_id=pipeline_run_id,
            status="queued",
            idempotency_key=idempotency_key,
        )

    @app.post(
        "/documents/upload",
        response_model=schemas.IngestDocumentResponse,
        status_code=202,
        summary="Upload document (multipart/form-data)",
        description="Загрузка документа файлом. Байты передаются напрямую, без base64 — подходит для больших PDF (>1MB).",
    )
    async def upload_document(
        file: UploadFile = File(..., description="PDF, изображение или текстовый файл"),
        source_type: str = Form("external", description="Источник: crm, email, portal, external, other"),
        document_type: str | None = Form(None, description="Тип документа (например court_decision)"),
        idempotency_key: str | None = Form(None, description="Ключ идемпотентности"),
        external_id: str | None = Form(None, description="Внешний идентификатор"),
        webhook_url: str | None = Form(None, description="URL для отправки вебхука по готовности"),
    ) -> schemas.IngestDocumentResponse:
        inc_request("/documents/upload")

        content_bytes = await file.read()
        if not content_bytes:
            raise HTTPException(status_code=400, detail="file is empty")

        content_type = _infer_content_type(file.filename or "", file.content_type or "")
        if content_type not in ("pdf", "image", "text"):
            raise HTTPException(
                status_code=400,
                detail=f"unsupported file type: {file.filename or file.content_type}. Use pdf, image (png/jpg), or text.",
            )

        source_type = source_type.strip().lower() or "external"
        if source_type not in ("crm", "email", "portal", "external", "other"):
            source_type = "external"

        content_hash = hashlib.sha256(content_bytes).hexdigest()
        idempotency_key = idempotency_key or _build_idempotency_key(
            content_hash=content_hash,
            source_type=source_type,
            external_id=external_id,
        )
        requested_document_type = (document_type.strip() if document_type else None) or None

        with repository.get_session() as session:
            existing = (
                session.query(models.IngestRequest)
                .filter(models.IngestRequest.idempotency_key == idempotency_key)
                .first()
            )
            if existing:
                run = session.get(models.PipelineRun, existing.pipeline_run_id)
                if run:
                    return schemas.IngestDocumentResponse(
                        document_id=existing.document_id,
                        pipeline_run_id=existing.pipeline_run_id,
                        status=run.status,  # type: ignore[arg-type]
                        idempotency_key=idempotency_key,
                    )

        document_id = repository.generate_id()
        pipeline_run_id = repository.generate_id()
        storage_path = file_storage.save_document_bytes(
            document_id=document_id,
            content=content_bytes,
            content_type=content_type,
        )

        try:
            with repository.get_session() as session:
                doc = models.Document(
                    id=document_id,
                    source_type=source_type,
                    document_type=requested_document_type,
                )
                session.add(doc)
                session.add(
                    models.DocumentFile(
                        document_id=document_id,
                        storage_path=storage_path,
                        file_type=content_type,
                    )
                )
                session.add(
                    models.PipelineRun(
                        id=pipeline_run_id,
                        document_id=document_id,
                        profile_id="unknown",
                        status="queued",
                        idempotency_key=idempotency_key,
                        webhook_url=webhook_url,
                    )
                )
                session.add(
                    models.IngestRequest(
                        idempotency_key=idempotency_key,
                        request_hash=content_hash,
                        source_type=source_type,
                        external_id=external_id,
                        document_id=document_id,
                        pipeline_run_id=pipeline_run_id,
                    )
                )
                session.commit()
        except IntegrityError:
            with repository.get_session() as session:
                existing = (
                    session.query(models.IngestRequest)
                    .filter(models.IngestRequest.idempotency_key == idempotency_key)
                    .first()
                )
                if existing:
                    run = session.get(models.PipelineRun, existing.pipeline_run_id)
                    status = run.status if run else "queued"
                    return schemas.IngestDocumentResponse(
                        document_id=existing.document_id,
                        pipeline_run_id=existing.pipeline_run_id,
                        status=status,  # type: ignore[arg-type]
                        idempotency_key=idempotency_key,
                    )
            logger.exception("Unexpected IntegrityError during upload_document")
            raise HTTPException(status_code=409, detail="duplicate ingest detected")

        try:
            publish_ingest_job(IngestJob(pipeline_run_id=pipeline_run_id, attempt=0))
        except Exception as exc:
            with repository.get_session() as session:
                run = session.get(models.PipelineRun, pipeline_run_id)
                if run:
                    run.status = "failed"
                    run.last_error = f"queue_publish_failed: {exc}"
                    run.finished_at = datetime.utcnow()
                    session.commit()
            logger.exception("ingest_enqueue_failed", pipeline_run_id=pipeline_run_id)
            raise HTTPException(status_code=503, detail="failed to enqueue pipeline run") from exc

        logger.info(
            "ingest_uploaded",
            document_id=document_id,
            pipeline_run_id=pipeline_run_id,
            idempotency_key=idempotency_key,
            source_type=source_type,
            content_type=content_type,
            file_size=len(content_bytes),
        )
        return schemas.IngestDocumentResponse(
            document_id=document_id,
            pipeline_run_id=pipeline_run_id,
            status="queued",
            idempotency_key=idempotency_key,
        )

    @app.get("/pipeline-runs/{pipeline_run_id}", response_model=schemas.PipelineRunStatusResponse)
    async def get_pipeline_run_status(pipeline_run_id: str) -> schemas.PipelineRunStatusResponse:
        inc_request("/pipeline-runs/status")
        with repository.get_session() as session:
            run = session.get(models.PipelineRun, pipeline_run_id)
            if not run:
                raise HTTPException(status_code=404, detail="pipeline run not found")
            return schemas.PipelineRunStatusResponse(
                pipeline_run_id=run.id,
                document_id=run.document_id,
                profile_id=run.profile_id,
                status=run.status,  # type: ignore[arg-type]
                retry_count=run.retry_count,
                created_at=run.created_at.isoformat(),
                started_at=_format_ts(run.started_at),
                finished_at=_format_ts(run.finished_at),
                last_error=run.last_error,
            )

    @app.get("/documents/{document_id}/result", response_model=schemas.DocumentResultResponse)
    async def get_result(document_id: str) -> schemas.DocumentResultResponse:
        inc_request("/documents/result")
        with repository.get_session() as session:
            doc = session.get(models.Document, document_id)
            if not doc:
                raise HTTPException(status_code=404, detail="document not found")

            run = (
                session.query(models.PipelineRun)
                .filter(models.PipelineRun.document_id == document_id)
                .order_by(models.PipelineRun.created_at.desc())
                .first()
            )
            if not run:
                raise HTTPException(status_code=404, detail="pipeline run not found")

            text_version = (
                session.query(models.TextVersion)
                .filter(models.TextVersion.pipeline_run_id == run.id)
                .order_by(models.TextVersion.id.desc())
                .first()
            )
            structured = (
                session.query(models.StructuredVersion)
                .filter(models.StructuredVersion.pipeline_run_id == run.id)
                .order_by(models.StructuredVersion.id.desc())
                .first()
            )
            quality = (
                session.query(models.QualityScore)
                .filter(models.QualityScore.pipeline_run_id == run.id)
                .order_by(models.QualityScore.id.desc())
                .first()
            )

        raw_text_version_id = str(text_version.id) if text_version else None
        structured_version_id = str(structured.id) if structured else None

        fields = {}
        if structured and isinstance(structured.data, dict):
            for name, value in structured.data.items():
                if isinstance(value, dict):
                    fields[name] = schemas.FieldValue(
                        name=name,
                        value=value.get("value"),
                        reasoning=value.get("reasoning"),
                        confidence=value.get("confidence"),
                        source=value.get("source"),
                    )
                else:
                    fields[name] = schemas.FieldValue(name=name, value=value)

        technical = quality.technical_score if quality else None
        semantic = quality.semantic_score if quality else None
        overall = quality.overall_score if quality else None

        human_review_required = True
        human_review_reason = "low_quality_or_missing_fields"
        if overall is not None and overall >= 0.75:
            human_review_required = False
            human_review_reason = None

        validation_status = "ok" if fields else "errors"
        validation_issues: list[schemas.ValidationIssue] = []

        return schemas.DocumentResultResponse(
            document_id=document_id,
            pipeline_run_id=run.id,
            raw_text_version_id=raw_text_version_id,
            structured_version_id=structured_version_id,
            raw_text=text_version.text if text_version else None,
            fields=fields,
            technical_quality_score=technical,
            semantic_confidence_score=semantic,
            overall_quality_score=overall,
            validation_status=validation_status,  # type: ignore[arg-type]
            validation_issues=validation_issues,
            human_review_required=human_review_required,
            human_review_reason=human_review_reason,
        )

    @app.post("/mlflow/backfill", response_model=schemas.MlflowBackfillResponse)
    async def mlflow_backfill(request: schemas.MlflowBackfillRequest) -> schemas.MlflowBackfillResponse:
        inc_request("/mlflow/backfill")
        try:
            result = backfill_pipeline_runs_to_mlflow(limit=request.limit, force=request.force)
        except Exception as exc:
            logger.exception("mlflow_backfill_failed")
            raise HTTPException(status_code=500, detail=f"mlflow backfill failed: {exc}") from exc

        return schemas.MlflowBackfillResponse(
            total_candidates=result.total_candidates,
            processed=result.processed,
            logged=result.logged,
            skipped_existing=result.skipped_existing,
            skipped_incomplete=result.skipped_incomplete,
            failed=result.failed,
            logged_run_ids=result.logged_run_ids,
            failed_items=[
                schemas.MlflowBackfillFailedItem(
                    pipeline_run_id=item["pipeline_run_id"],
                    error=item["error"],
                )
                for item in result.failed_items
            ],
        )

    return app


app = create_app()


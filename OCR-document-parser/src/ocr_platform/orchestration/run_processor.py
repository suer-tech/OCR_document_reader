from __future__ import annotations

import asyncio
from datetime import datetime
from time import perf_counter

from fastapi import HTTPException

from ocr_platform.observability.logging import get_logger
from ocr_platform.observability.metrics import (
    inc_ingest_status,
    inc_ocr_step,
    observe_field_fill_rate,
    observe_ocr_step_latency,
    observe_pipeline_latency,
    observe_quality_score,
    observe_validation_issue_count,
)
from ocr_platform.observability.mlflow_client import (
    mlflow_log_metric,
    mlflow_log_param,
    mlflow_log_text,
    mlflow_run,
    mlflow_set_tag,
)
from ocr_platform.orchestration import pipeline_engine, router
from ocr_platform.services import (
    document_intel_service,
    ocr_service,
    quality_service,
    validation_service,
)
from ocr_platform.storage import models, repository

logger = get_logger(__name__)

GLOBAL_PIPELINE_TIMEOUT = 1800


async def _trigger_webhook_safely(webhook_url: str | None, payload: dict) -> None:
    if not webhook_url:
        return
    try:
        import httpx

        async with httpx.AsyncClient() as client:
            response = await client.post(webhook_url, json=payload, timeout=10.0)
            logger.info(
                "webhook_sent",
                webhook_url=webhook_url,
                status_code=response.status_code,
                pipeline_run_id=payload.get("pipeline_run_id"),
            )
    except Exception as exc:
        logger.warning(
            "webhook_send_failed",
            webhook_url=webhook_url,
            error=str(exc),
            pipeline_run_id=payload.get("pipeline_run_id"),
        )


async def process_pipeline_run(pipeline_run_id: str) -> None:
    try:
        await asyncio.wait_for(
            _process_pipeline_run_impl(pipeline_run_id),
            timeout=GLOBAL_PIPELINE_TIMEOUT,
        )
    except asyncio.TimeoutError:
        logger.error(
            "pipeline_timed_out",
            pipeline_run_id=pipeline_run_id,
            timeout=GLOBAL_PIPELINE_TIMEOUT,
        )
        with repository.get_session() as session:
            run = session.get(models.PipelineRun, pipeline_run_id)
            if run:
                run.status = "failed"
                run.last_error = f"Pipeline timed out after {GLOBAL_PIPELINE_TIMEOUT}s"
                run.finished_at = datetime.utcnow()
                session.commit()


async def _process_pipeline_run_impl(pipeline_run_id: str) -> None:
    document_id = None
    webhook_url = None
    profile_id = "unknown"
    current_step = "resolve_profile"
    start = perf_counter()

    try:
        with repository.get_session() as session:
            run = session.get(models.PipelineRun, pipeline_run_id)
            if not run:
                raise HTTPException(status_code=404, detail="pipeline run not found")
            if run.status == "done":
                return

            webhook_url = run.webhook_url
            doc = session.get(models.Document, run.document_id)
            if not doc:
                raise HTTPException(status_code=404, detail="document not found")
            file_rec = (
                session.query(models.DocumentFile)
                .filter(models.DocumentFile.document_id == doc.id)
                .first()
            )
            if not file_rec:
                raise HTTPException(status_code=404, detail="file record not found")

            run.status = "processing"
            run.started_at = run.started_at or datetime.utcnow()
            processing_started_at = run.started_at
            run.last_error = None
            session.commit()

            document_id = doc.id
            source_type = doc.source_type
            requested_document_type = doc.document_type
            content_type = file_rec.file_type
            storage_path = file_rec.storage_path
        # Сначала проверим, нет ли уже извлеченного текста в БД для этого документа (после предыдущих попыток)
        extracted_text = None
        ocr_was_used = False
        ocr_latency_ms = None
        text_source = "database_cache"

        with repository.get_session() as session:
            existing_txt = (
                session.query(models.TextVersion)
                .filter(models.TextVersion.document_id == document_id)
                .order_by(models.TextVersion.id.desc())
                .first()
            )
            if existing_txt and existing_txt.text.strip():
                extracted_text = existing_txt.text
                logger.info(
                    "restored_text_from_db",
                    document_id=document_id,
                    pipeline_run_id=pipeline_run_id,
                    text_length=len(extracted_text),
                )

        if not extracted_text:
            # Извлечение текста: pdfplumber → pymupdf → OCR (для PDF без текстового слоя)
            import asyncio

            (
                extracted_text,
                ocr_was_used,
                ocr_latency_ms,
                text_source,
            ) = await asyncio.to_thread(
                ocr_service.extract_text_at_ingest,
                storage_path,
                content_type,
                document_id=document_id,
                pipeline_run_id=pipeline_run_id,
            )
        detection_text = extracted_text

        if ocr_was_used:
            status = "success" if extracted_text.strip() else "empty"
            inc_ocr_step(content_type, status)
            if ocr_latency_ms is not None:
                observe_ocr_step_latency(content_type, ocr_latency_ms / 1000.0)
            try:
                with mlflow_run("ocr_step"):
                    mlflow_set_tag("component", "ocr")
                    mlflow_set_tag("document_id", document_id)
                    mlflow_set_tag("pipeline_run_id", pipeline_run_id)
                    mlflow_set_tag("content_type", content_type)
                    mlflow_set_tag("text_source", text_source)
                    mlflow_log_param("content_type", content_type)
                    mlflow_log_param("text_source", text_source)
                    mlflow_log_param("status", status)
                    mlflow_log_param("text_length", len(extracted_text))
                    mlflow_log_metric("ocr_latency_ms", ocr_latency_ms or 0.0)
                    mlflow_log_metric(
                        "ocr_success", 1.0 if extracted_text.strip() else 0.0
                    )
                    text_artifact = extracted_text
                    if len(text_artifact) > ocr_service.OCR_TEXT_ARTIFACT_MAX_LEN:
                        text_artifact = (
                            text_artifact[: ocr_service.OCR_TEXT_ARTIFACT_MAX_LEN]
                            + f"\n\n[truncated: original_length={len(extracted_text)}]"
                        )
                    mlflow_log_text(text_artifact, "ocr/extracted_text.txt")
            except Exception:
                logger.warning(
                    "ocr_mlflow_failed",
                    document_id=document_id,
                    pipeline_run_id=pipeline_run_id,
                )

        resolution = router.resolve_profile(
            source_type=source_type,
            requested_document_type=requested_document_type,
            detection_text=detection_text,
            document_id=document_id,
            pipeline_run_id=pipeline_run_id,
        )
        profile_id = resolution.profile_id
        profile_config = router.load_profile(profile_id)

        with repository.get_session() as session:
            run = session.get(models.PipelineRun, pipeline_run_id)
            doc = session.get(models.Document, document_id)
            if not run or not doc:
                raise HTTPException(status_code=404, detail="pipeline run not found")
            run.profile_id = profile_id
            doc.document_type = resolution.document_type
            session.commit()

        current_step = "run_pipeline"
        engine = router.build_pipeline_engine(profile_config)
        context = pipeline_engine.PipelineContext(
            document_id=document_id,
            pipeline_run_id=pipeline_run_id,
            profile_id=profile_id,
        )
        context = await engine.run(context)

        current_step = "process_pipeline_outputs"
        text = extracted_text
        fields: dict = {}
        with repository.get_session() as session:
            file_rec = (
                session.query(models.DocumentFile)
                .filter(models.DocumentFile.document_id == document_id)
                .first()
            )
            if not file_rec:
                raise HTTPException(status_code=500, detail="file record not found")

            existing_run_txt = (
                session.query(models.TextVersion)
                .filter(models.TextVersion.pipeline_run_id == pipeline_run_id)
                .first()
            )
            if not existing_run_txt:
                text_version = models.TextVersion(
                    document_id=document_id,
                    pipeline_run_id=pipeline_run_id,
                    text=text,
                )
                session.add(text_version)
                session.commit()
            else:
                existing_run_txt.text = text
                session.commit()

            fields = await document_intel_service.simple_extract_fields(
                text=text,
                profile_config=profile_config,
                profile_id=profile_id,
                pipeline_run_id=pipeline_run_id,
                document_id=document_id,
            )
            fields["processing_started_at"] = (
                processing_started_at.isoformat() if processing_started_at else None
            )
            validation_status, validation_issues = validation_service.validate_fields(
                fields, profile_id, profile_config
            )
            technical, semantic, overall = quality_service.compute_quality_scores(
                text, fields
            )

            structured = models.StructuredVersion(
                document_id=document_id,
                pipeline_run_id=pipeline_run_id,
                data=fields,
            )
            session.add(structured)

            quality = models.QualityScore(
                pipeline_run_id=pipeline_run_id,
                technical_score=technical,
                semantic_score=semantic,
                overall_score=overall,
                details=None,
            )
            session.add(quality)

            run = session.get(models.PipelineRun, pipeline_run_id)
            if run:
                run.status = "done"
                run.finished_at = datetime.utcnow()
                run.last_error = None
            session.commit()

        elapsed = perf_counter() - start
        observe_pipeline_latency(profile_id, elapsed)
        inc_ingest_status(status="done", profile_id=profile_id)

        total_fields = len(fields)
        filled_fields = sum(
            1
            for item in fields.values()
            if isinstance(item, dict) and item.get("value")
        )
        field_fill_rate = (filled_fields / total_fields) if total_fields > 0 else 0.0
        validation_issue_count = len(validation_issues)
        validation_error_count = sum(
            1 for issue in validation_issues if issue.severity == "error"
        )
        validation_warning_count = sum(
            1 for issue in validation_issues if issue.severity == "warning"
        )

        observe_quality_score(
            profile_id=profile_id, score_type="technical", value=technical
        )
        observe_quality_score(
            profile_id=profile_id, score_type="semantic", value=semantic
        )
        observe_quality_score(
            profile_id=profile_id, score_type="overall", value=overall
        )
        observe_validation_issue_count(
            profile_id=profile_id, issues=validation_issue_count
        )
        observe_field_fill_rate(profile_id=profile_id, fill_rate=field_fill_rate)

        try:
            with mlflow_run("pipeline_request_summary"):
                mlflow_set_tag("component", "pipeline_request")
                mlflow_set_tag("pipeline_run_id", pipeline_run_id)
                mlflow_set_tag("document_id", document_id)
                mlflow_set_tag("profile_id", profile_id)
                mlflow_set_tag("detection_source", resolution.detection_source)
                mlflow_set_tag("validation_status", validation_status)
                mlflow_set_tag("content_type", content_type)
                mlflow_set_tag("source_type", source_type)
                mlflow_set_tag("resolved_document_type", resolution.document_type)
                mlflow_set_tag("detection_model", resolution.detection_model or "none")
                mlflow_set_tag("text_source", text_source)

                mlflow_log_param("text_source", text_source)
                mlflow_log_param(
                    "pipeline_steps_total", len(profile_config.get("pipeline", []))
                )
                mlflow_log_param(
                    "pipeline_steps_executed",
                    len(context.data.get("executed_steps", [])),
                )
                mlflow_log_param("fields_total", total_fields)
                mlflow_log_param("fields_filled", filled_fields)
                mlflow_log_param("validation_issue_count", validation_issue_count)
                mlflow_log_param("validation_error_count", validation_error_count)
                mlflow_log_param("validation_warning_count", validation_warning_count)
                mlflow_log_param(
                    "requested_document_type_provided",
                    int(bool(requested_document_type)),
                )

                mlflow_log_metric("pipeline_success", 1.0)
                mlflow_log_metric("latency_pipeline_ms", elapsed * 1000.0)
                mlflow_log_metric("quality_technical", technical)
                mlflow_log_metric("quality_semantic", semantic)
                mlflow_log_metric("quality_overall", overall)
                mlflow_log_metric("detection_confidence", resolution.confidence)
                mlflow_log_metric("field_fill_rate", field_fill_rate)
                mlflow_log_metric(
                    "human_review_required", 1.0 if overall < 0.75 else 0.0
                )
                mlflow_log_metric(
                    "llm_used_for_doc_type",
                    1.0 if resolution.detection_source == "llm" else 0.0,
                )
                mlflow_log_metric(
                    "detection_fallback_used",
                    1.0 if "fallback" in resolution.detection_source else 0.0,
                )
                mlflow_log_metric("ocr_used", 1.0 if ocr_was_used else 0.0)
                mlflow_log_metric(
                    "score_final",
                    (overall * 100.0)
                    - (elapsed * 0.1)
                    - (validation_error_count * 2.0),
                )
        except Exception:
            logger.warning(
                "pipeline_summary_mlflow_failed",
                document_id=document_id,
                pipeline_run_id=pipeline_run_id,
            )

        logger.info(
            "pipeline_completed",
            document_id=document_id,
            pipeline_run_id=pipeline_run_id,
            profile_id=profile_id,
            document_type=resolution.document_type,
            detection_source=resolution.detection_source,
            detection_model=resolution.detection_model,
            detection_confidence=resolution.confidence,
            text_source=text_source,
            executed_steps=context.data.get("executed_steps", []),
            elapsed_seconds=elapsed,
        )

        if webhook_url:
            human_review_required = True
            human_review_reason = "low_quality_or_missing_fields"
            if overall is not None and overall >= 0.75:
                human_review_required = False
                human_review_reason = None

            webhook_payload = {
                "event": "pipeline_completed",
                "document_id": document_id,
                "pipeline_run_id": pipeline_run_id,
                "status": "done",
                "raw_text": text,
                "fields": {
                    name: (
                        {
                            "name": name,
                            "value": val.get("value") if isinstance(val, dict) else val,
                            "reasoning": val.get("reasoning")
                            if isinstance(val, dict)
                            else None,
                            "confidence": val.get("confidence")
                            if isinstance(val, dict)
                            else None,
                            "source": val.get("source")
                            if isinstance(val, dict)
                            else None,
                        }
                    )
                    for name, val in fields.items()
                },
                "technical_quality_score": technical,
                "semantic_confidence_score": semantic,
                "overall_quality_score": overall,
                "validation_status": validation_status,
                "validation_issues": [
                    {
                        "code": issue.code,
                        "message": issue.message,
                        "field_name": issue.field_name,
                        "severity": issue.severity,
                    }
                    for issue in validation_issues
                ],
                "human_review_required": human_review_required,
                "human_review_reason": human_review_reason,
                "finished_at": datetime.utcnow().isoformat(),
            }
            await _trigger_webhook_safely(webhook_url, webhook_payload)

    except Exception as exc:
        with repository.get_session() as session:
            run = session.get(models.PipelineRun, pipeline_run_id)
            if run:
                run.status = "failed"
                run.last_error = f"{type(exc).__name__}: {exc}"
                run.finished_at = datetime.utcnow()
                session.commit()
        inc_ingest_status(status="failed", profile_id=profile_id or "unknown")
        logger.exception(
            "pipeline_failed",
            pipeline_run_id=pipeline_run_id,
            profile_id=profile_id,
            current_step=current_step,
            error_type=type(exc).__name__,
            error_message=str(exc),
        )
        if webhook_url:
            webhook_payload = {
                "event": "pipeline_failed",
                "document_id": document_id,
                "pipeline_run_id": pipeline_run_id,
                "status": "failed",
                "error": f"{type(exc).__name__}: {exc}",
                "finished_at": datetime.utcnow().isoformat(),
            }
            await _trigger_webhook_safely(webhook_url, webhook_payload)
        raise

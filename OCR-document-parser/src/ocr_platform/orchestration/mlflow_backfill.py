from __future__ import annotations

from dataclasses import dataclass, field

from ocr_platform.observability.logging import get_logger
from ocr_platform.observability.mlflow_client import mlflow_log_payload_sync, mlflow_run_exists_by_tag
from ocr_platform.storage import models, repository


logger = get_logger(__name__)


@dataclass
class MlflowBackfillResult:
    total_candidates: int = 0
    processed: int = 0
    logged: int = 0
    skipped_existing: int = 0
    skipped_incomplete: int = 0
    failed: int = 0
    logged_run_ids: list[str] = field(default_factory=list)
    failed_items: list[dict[str, str]] = field(default_factory=list)


def _compute_field_fill_rate(structured_data: dict) -> tuple[int, int, float]:
    total_fields = len(structured_data)
    filled_fields = sum(1 for item in structured_data.values() if isinstance(item, dict) and item.get("value"))
    field_fill_rate = (filled_fields / total_fields) if total_fields > 0 else 0.0
    return total_fields, filled_fields, field_fill_rate


def backfill_pipeline_runs_to_mlflow(*, limit: int, force: bool = False) -> MlflowBackfillResult:
    result = MlflowBackfillResult()

    with repository.get_session() as session:
        runs = (
            session.query(models.PipelineRun)
            .filter(models.PipelineRun.status == "done")
            .order_by(models.PipelineRun.created_at.asc())
            .limit(limit)
            .all()
        )

        result.total_candidates = len(runs)
        for run in runs:
            result.processed += 1

            if not force and mlflow_run_exists_by_tag("pipeline_run_id", run.id):
                result.skipped_existing += 1
                continue

            doc = session.get(models.Document, run.document_id)
            if not doc:
                result.skipped_incomplete += 1
                continue

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

            structured_data = structured.data if structured and isinstance(structured.data, dict) else {}
            total_fields, filled_fields, field_fill_rate = _compute_field_fill_rate(structured_data)

            tags = {
                "component": "pipeline_request_backfill",
                "pipeline_run_id": run.id,
                "document_id": run.document_id,
                "profile_id": run.profile_id,
                "source_type": doc.source_type,
                "resolved_document_type": doc.document_type or "unknown",
                "status": run.status,
            }
            params = {
                "backfill": "1",
                "fields_total": str(total_fields),
                "fields_filled": str(filled_fields),
                "retry_count": str(run.retry_count),
            }

            metrics: list[tuple[str, float]] = [
                ("pipeline_success", 1.0),
                ("field_fill_rate", field_fill_rate),
            ]
            if quality and quality.technical_score is not None:
                metrics.append(("quality_technical", float(quality.technical_score)))
            if quality and quality.semantic_score is not None:
                metrics.append(("quality_semantic", float(quality.semantic_score)))
            if quality and quality.overall_score is not None:
                metrics.append(("quality_overall", float(quality.overall_score)))

            try:
                mlflow_log_payload_sync(
                    name="pipeline_request_summary_backfill",
                    tags=tags,
                    params=params,
                    metrics=metrics,
                )
                result.logged += 1
                result.logged_run_ids.append(run.id)
            except Exception as exc:
                result.failed += 1
                logger.warning(
                    "mlflow_backfill_item_failed",
                    pipeline_run_id=run.id,
                    error_type=type(exc).__name__,
                    error=str(exc),
                )
                result.failed_items.append(
                    {
                        "pipeline_run_id": run.id,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )

    return result

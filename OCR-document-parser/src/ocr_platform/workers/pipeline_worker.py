from __future__ import annotations

import asyncio
from datetime import datetime

from ocr_platform.config.settings import get_settings
from ocr_platform.observability.logging import configure_logging, get_logger
from ocr_platform.orchestration.run_processor import process_pipeline_run
from ocr_platform.queueing.rabbitmq import IngestJob, consume_ingest_jobs, publish_ingest_job
from ocr_platform.storage import models, repository

logger = get_logger(__name__)


def _set_retrying(pipeline_run_id: str, attempt: int, error: str) -> None:
    with repository.get_session() as session:
        run = session.get(models.PipelineRun, pipeline_run_id)
        if not run:
            return
        run.status = "retrying"
        run.retry_count = attempt
        run.last_error = error
        run.finished_at = None
        session.commit()


def _set_failed(pipeline_run_id: str, attempt: int, error: str) -> None:
    with repository.get_session() as session:
        run = session.get(models.PipelineRun, pipeline_run_id)
        if not run:
            return
        run.status = "failed"
        run.retry_count = attempt
        run.last_error = error
        run.finished_at = datetime.utcnow()
        session.commit()


def _handle_job(job: IngestJob) -> None:
    settings = get_settings()
    max_retries = max(0, settings.worker_max_retries)

    try:
        asyncio.run(process_pipeline_run(job.pipeline_run_id))
    except Exception as exc:
        next_attempt = job.attempt + 1
        error = f"{type(exc).__name__}: {exc}"
        if next_attempt <= max_retries:
            _set_retrying(job.pipeline_run_id, next_attempt, error)
            publish_ingest_job(IngestJob(pipeline_run_id=job.pipeline_run_id, attempt=next_attempt))
            logger.warning(
                "pipeline_run_requeued",
                pipeline_run_id=job.pipeline_run_id,
                attempt=next_attempt,
                max_retries=max_retries,
            )
        else:
            _set_failed(job.pipeline_run_id, next_attempt, error)
            logger.error(
                "pipeline_run_failed_final",
                pipeline_run_id=job.pipeline_run_id,
                attempt=next_attempt,
                max_retries=max_retries,
            )


def main() -> None:
    configure_logging()
    repository.init_db()
    logger.info("pipeline_worker_started")
    consume_ingest_jobs(_handle_job)


if __name__ == "__main__":
    main()


from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from ocr_platform.storage.models import Document, PipelineRun


def daily_report(session: Session, *, now: datetime, timezone_name: str) -> dict:
    local_zone = ZoneInfo(timezone_name)
    local_now = now.astimezone(local_zone)
    day_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)
    # Existing ORM columns contain naive UTC datetimes.
    start_utc = day_start.astimezone(timezone.utc).replace(tzinfo=None)
    end_utc = day_end.astimezone(timezone.utc).replace(tzinfo=None)
    rows = session.execute(
        select(
            PipelineRun.status,
            PipelineRun.started_at,
            PipelineRun.finished_at,
            PipelineRun.retry_count,
            Document.document_type,
            Document.id,
        )
        .join(Document, PipelineRun.document_id == Document.id)
        .where(PipelineRun.created_at < end_utc)
        .where(
            (PipelineRun.created_at >= start_utc)
            | ((PipelineRun.finished_at >= start_utc) & (PipelineRun.finished_at < end_utc))
        )
    ).all()
    by_type: dict[str, dict] = defaultdict(
        lambda: {"processed": 0, "errors": 0, "retries": 0, "avg_seconds": None}
    )
    durations: dict[str, list[float]] = defaultdict(list)
    total = {"processed": 0, "documents_unique": 0, "errors": 0, "retries": 0, "in_progress": 0}
    unique_documents: set[str] = set()
    for status, started_at, finished_at, retry_count, document_type, document_id in rows:
        kind = document_type or "unknown"
        # Count a run only on the day it reached its terminal status.
        terminal_today = finished_at is not None and start_utc <= finished_at < end_utc
        if terminal_today and status in {"done", "failed"}:
            total["processed"] += 1
            unique_documents.add(document_id)
            by_type[kind]["processed"] += 1
            if status == "failed":
                total["errors"] += 1
                by_type[kind]["errors"] += 1
            if status == "done" and started_at and finished_at and finished_at >= started_at:
                durations[kind].append((finished_at - started_at).total_seconds())
        if start_utc <= (finished_at or now.replace(tzinfo=None)) < end_utc:
            total["retries"] += max(0, retry_count or 0)
            by_type[kind]["retries"] += max(0, retry_count or 0)
    total["documents_unique"] = len(unique_documents)
    total["in_progress"] = session.scalar(
        select(func.count()).select_from(PipelineRun).where(PipelineRun.status.in_(("queued", "processing", "retrying")))
    ) or 0
    for kind, values in durations.items():
        by_type[kind]["avg_seconds"] = round(sum(values) / len(values), 1)
    return {
        "date": day_start.date().isoformat(),
        "timezone": timezone_name,
        "totals": total,
        "by_type": dict(sorted(by_type.items())),
        "counting_note": "processed = pipeline runs finished today (including failures); documents_unique = distinct documents; averages use successful runs only; retries = current retry_count for runs in today's window",
    }


def load_daily_report(database_url: str, timezone_name: str) -> dict:
    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        with Session(engine) as session:
            return daily_report(session, now=datetime.now(timezone.utc), timezone_name=timezone_name)
    finally:
        engine.dispose()


async def prometheus_snapshot(url: str, *, include_history: bool = True) -> dict:
    queries = {
        "api_up": 'up{job="ocr-api"}',
        "worker_up": 'up{job="ocr-worker"}',
        "request_rate_5m": "sum(rate(ocr_http_requests_total[5m]))",
        "error_rate_5m": 'sum(rate(ocr_http_requests_total{status_code=~"5.."}[5m]))',
        "http_p95_seconds_5m": "histogram_quantile(0.95, sum by (le) (rate(ocr_http_request_duration_seconds_bucket[5m])))",
        "rabbitmq_ready": "sum(rabbitmq_queue_messages_ready)",
    }
    if include_history:
        queries.update(
            request_peak_24h="max_over_time((sum(rate(ocr_http_requests_total[1m])))[24h:1m])",
            requests_24h="sum(increase(ocr_http_requests_total[24h]))",
        )
    result: dict[str, float | None] = {}
    async with httpx.AsyncClient(timeout=5.0) as client:
        for key, query in queries.items():
            try:
                response = await client.get(f"{url.rstrip('/')}/api/v1/query", params={"query": query})
                response.raise_for_status()
                values = response.json().get("data", {}).get("result", [])
                result[key] = float(values[0]["value"][1]) if values else None
            except (httpx.HTTPError, ValueError, KeyError, TypeError):
                result[key] = None
    return result

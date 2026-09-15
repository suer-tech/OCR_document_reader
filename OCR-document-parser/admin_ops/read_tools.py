"""Bounded read-only operations. Never return document contents, IDs or raw logs."""
from __future__ import annotations

import asyncio
import json
import math
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Literal
from zoneinfo import ZoneInfo

import httpx
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from admin_ops.config import OpsSettings
from ocr_platform.storage.models import Document, PipelineRun

METRICS = {
    "request_rate": "sum(rate(ocr_http_requests_total[5m]))",
    "http_errors": 'sum(rate(ocr_http_requests_total{status_code=~"5.."}[5m]))',
    "latency_p95": "histogram_quantile(0.95,sum by(le)(rate(ocr_http_request_duration_seconds_bucket[5m])))",
    "queue_ready": "sum(rabbitmq_queue_messages_ready)",
    "api_up": 'min(up{job="ocr-api"})',
    "worker_up": 'min(up{job="ocr-worker"})',
}
EVENTS = {
    "pipeline_failed", "pipeline_completed", "pipeline_run_requeued", "pipeline_run_failed_final",
    "pipeline_worker_started", "pipeline_step_started", "pipeline_step_finished",
    "pipeline_steps_started", "pipeline_steps_finished", "rabbitmq_consumer_started",
    "rabbitmq_connection_lost_reconnecting", "langfuse_init_failed", "langfuse_flush_failed",
    "pipeline_summary_mlflow_failed",
}
KINDS = {"court_decision_ru", "court_order_ru", "court_order", "passport_main", "passport_registration", "unknown"}
ERRORS = {"ValueError", "TypeError", "KeyError", "TimeoutError", "ConnectError", "ConnectTimeout",
          "ReadTimeout", "HTTPStatusError", "OperationalError", "RuntimeError", "ValidationError"}


class Arguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DocumentDays(Arguments):
    days: int = Field(default=8, ge=1, le=31, strict=True)
    end_date: date | None = None
    same_time: bool = True


class MetricHistory(Arguments):
    metric: Literal["request_rate", "http_errors", "latency_p95", "queue_ready", "api_up", "worker_up"]
    hours: int = Field(default=24, ge=1, le=720, strict=True)


class LogEvents(Arguments):
    hours: int = Field(default=24, ge=1, le=336, strict=True)
    level: Literal["all", "error", "warning", "info"] = "error"
    limit: int = Field(default=30, ge=1, le=100, strict=True)


TOOL_MODELS = {
    "document_days": DocumentDays, "metric_history": MetricHistory,
    "log_events": LogEvents, "system_overview": Arguments,
}


def finite(value):
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (ValueError, TypeError, OverflowError):
        return None


def history_report(session: Session, args: DocumentDays, now: datetime, zone: str) -> dict:
    local_now = now.astimezone(ZoneInfo(zone))
    end = args.end_date or local_now.date()
    if end > local_now.date() or end < local_now.date() - timedelta(days=365):
        raise ValueError("date outside supported history window")
    start_day = end - timedelta(days=args.days - 1)
    windows = []
    for offset in range(args.days):
        day = start_day + timedelta(days=offset)
        start = datetime.combine(day, datetime.min.time(), tzinfo=local_now.tzinfo)
        finish = (start.replace(hour=local_now.hour, minute=local_now.minute,
                                second=local_now.second, microsecond=local_now.microsecond)
                  if args.same_time else start + timedelta(days=1))
        finish = min(finish, local_now)
        windows.append((day, start.astimezone(timezone.utc).replace(tzinfo=None),
                        finish.astimezone(timezone.utc).replace(tzinfo=None)))
    # No text, payload, error message, path or webhook columns are selected.
    rows = session.execute(select(
        PipelineRun.document_id, PipelineRun.status, PipelineRun.started_at,
        PipelineRun.finished_at, PipelineRun.retry_count, Document.document_type,
    ).join(Document, PipelineRun.document_id == Document.id).where(
        PipelineRun.finished_at >= windows[0][1], PipelineRun.finished_at < windows[-1][2],
        PipelineRun.status.in_(("done", "failed")),
    ).limit(50001)).all()
    if len(rows) > 50000:
        return {"status": "unavailable", "reason": "row_limit_exceeded", "suggestion": "use fewer days"}
    first_seen = session.scalar(select(func.min(PipelineRun.created_at)))
    days = []
    for day, start, finish in windows:
        buckets = defaultdict(lambda: {"completed_runs": 0, "successful_runs": 0, "errors": 0,
                                       "retries": 0, "documents": set(), "durations": []})
        for document_id, status, started, finished, retries, kind in rows:
            if not start <= finished < finish:
                continue
            for key in ("total", kind if kind in KINDS else "other"):
                bucket = buckets[key]
                bucket["completed_runs"] += 1
                bucket["successful_runs"] += status == "done"
                bucket["errors"] += status == "failed"
                bucket["retries"] += max(0, retries or 0)
                bucket["documents"].add(document_id)
                if status == "done" and started and finished >= started:
                    bucket["durations"].append((finished - started).total_seconds())
        _ = buckets["total"]
        for bucket in buckets.values():
            bucket["documents_unique"] = len(bucket.pop("documents"))
            durations = bucket.pop("durations")
            bucket["avg_seconds"] = round(sum(durations) / len(durations), 2) if durations else None
        days.append({"date": day.isoformat(), "start_utc": start.isoformat() + "Z",
                     "end_utc": finish.isoformat() + "Z", "partial_day": args.same_time or day == local_now.date(),
                     "coverage": "unknown_or_partial" if first_seen is None or first_seen > start else "records_available",
                     "totals": buckets.pop("total"), "by_type": dict(buckets)})
    previous = [d["totals"]["documents_unique"] for d in days[:-1] if d["coverage"] == "records_available"]
    average = sum(previous) / len(previous) if previous else None
    current = days[-1]["totals"]["documents_unique"]
    return {"status": "ok", "source": "PostgreSQL pipeline_runs + documents", "timezone": zone,
            "as_of": local_now.isoformat(), "same_time_cutoff": args.same_time, "days": days,
            "comparison": {"baseline_days": len(previous), "baseline_average_documents": average,
                           "latest_documents": current,
                           "change_percent": round((current / average - 1) * 100, 1) if average else None},
            "notes": "Counts terminal runs by finished_at; includes failed runs. Unique documents are deduplicated per window. Retries are cumulative retry_count for these runs, not timestamped retry events. No extrapolation. Retained DB records do not prove uninterrupted collection; older missing data is not zero. Current document type is used."}


def load_history(settings: OpsSettings, args: DocumentDays, now: datetime) -> dict:
    connect_args = ({"connect_timeout": 5,
                     "options": "-c default_transaction_read_only=on -c statement_timeout=10000"}
                    if make_url(settings.database_url).get_backend_name() == "postgresql" else {})
    engine = create_engine(settings.database_url, pool_pre_ping=True, connect_args=connect_args)
    try:
        with Session(engine) as session:
            return history_report(session, args, now, settings.timezone)
    finally:
        engine.dispose()


async def read_json(url: str, params: dict) -> dict:
    # Bounded responses and timeout; no environment proxies, redirects or arbitrary URLs.
    async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
        async with client.stream("GET", url, params=params) as response:
            response.raise_for_status()
            chunks = bytearray()
            async for chunk in response.aiter_bytes():
                chunks.extend(chunk)
                if len(chunks) > 2_000_000:
                    raise ValueError("upstream response too large")
            return json.loads(chunks)


async def metric_history(settings: OpsSettings, args: MetricHistory, now: datetime) -> dict:
    start = now - timedelta(hours=args.hours)
    step = max(60, math.ceil(args.hours * 3600 / 240))
    data = await read_json(settings.prometheus_url.rstrip("/") + "/api/v1/query_range", {
        "query": METRICS[args.metric], "start": start.timestamp(), "end": now.timestamp(), "step": step,
    })
    if data.get("status") != "success":
        raise ValueError("query failed")
    series = data.get("data", {}).get("result", [])
    points = [{"timestamp": datetime.fromtimestamp(float(ts), timezone.utc).isoformat(), "value": finite(value)}
              for item in series[:1] for ts, value in item.get("values", [])[:241]]
    observed = [p["value"] for p in points if p["value"] is not None]
    return {"status": "ok", "source": "Prometheus query_range", "metric": args.metric,
            "unit": {"request_rate": "requests/second", "http_errors": "requests/second",
                     "latency_p95": "seconds", "queue_ready": "messages", "api_up": "0/1", "worker_up": "0/1"}[args.metric],
            "start": start.isoformat(), "end": now.isoformat(), "step_seconds": step,
            "points": points, "sampled_min": min(observed) if observed else None,
            "sampled_max": max(observed) if observed else None,
            "sampled_average": sum(observed) / len(observed) if observed else None,
            "expected_points": math.floor(args.hours * 3600 / step) + 1,
            "finite_points": len(observed),
            "note": "Sampled series, not exact peak or document count. Gaps/NaN are unavailable, not zero. Requested time range may exceed actual retention."}


async def log_events(settings: OpsSettings, args: LogEvents, now: datetime) -> dict:
    query = '{job="ocr-app"}'
    if args.level != "all":
        query = '{job="ocr-app",level="' + args.level + '"}'
    data = await read_json(settings.loki_url.rstrip("/") + "/loki/api/v1/query_range", {
        "query": query, "start": str(int((now - timedelta(hours=args.hours)).timestamp() * 1e9)),
        "end": str(int(now.timestamp() * 1e9)), "limit": args.limit, "direction": "backward",
    })
    if data.get("status") != "success":
        raise ValueError("query failed")
    events, scanned = [], 0
    for stream in data.get("data", {}).get("result", []):
        for ts, raw in stream.get("values", []):
            scanned += 1
            if scanned > args.limit:
                break
            try:
                entry = json.loads(raw)
                event = entry.get("message")
                if event not in EVENTS:
                    continue
                safe = {"timestamp": datetime.fromtimestamp(int(ts) / 1e9, timezone.utc).isoformat(), "event": event}
                level = entry.get("level")
                if level in {"info", "warning", "error", "critical", "debug"}:
                    safe["level"] = level
                if entry.get("error_type") in ERRORS:
                    safe["error_type"] = entry["error_type"]
                for name in ("duration_ms", "elapsed_seconds", "total_latency_ms", "attempt", "max_retries"):
                    value = finite(entry.get(name))
                    if value is not None and 0 <= value <= 1e9:
                        safe[name] = value
                events.append(safe)
            except (ValueError, TypeError, AttributeError, OverflowError):
                continue
    events.sort(key=lambda x: x["timestamp"], reverse=True)
    return {"status": "ok", "source": "Loki ocr-app structured events", "hours": args.hours,
            "events": events, "scanned": min(scanned, args.limit), "limit": args.limit,
            "limit_reached": scanned >= args.limit,
            "note": "Allowlisted technical fields only. Free text, stack traces, IDs, paths, labels and document contents removed. Omitted events may exist; empty results do not prove no incidents. No host/Docker logs are exposed."}


class ReadTools:
    def __init__(self, settings: OpsSettings):
        self.settings = settings

    async def call(self, name: str, arguments: dict) -> dict:
        try:
            model = TOOL_MODELS.get(name)
            if model is None:
                return {"status": "unavailable", "reason": "unknown_tool"}
            args = model.model_validate(arguments)
            now = datetime.now(timezone.utc)
            async with asyncio.timeout(25):
                if name == "document_days":
                    result = await asyncio.to_thread(load_history, self.settings, args, now)
                elif name == "metric_history":
                    result = await metric_history(self.settings, args, now)
                elif name == "log_events":
                    result = await log_events(self.settings, args, now)
                else:
                    result = {"status": "ok", "as_of": now.isoformat(), "timezone": self.settings.timezone,
                              "tools": list(TOOL_MODELS), "metrics": list(METRICS),
                              "architecture": "OCR API -> RabbitMQ -> worker -> PostgreSQL. Prometheus stores sampled metrics; Loki stores structured app events. Pulse is read-only; code changes require /fix. Sources can be unavailable independently.",
                              "limits": "Documents: up to 31 days per call, last 365 days if retained; metrics: last 30 days if retained; Loki: last 14 days if retained. No raw logs, SQL, shell, documents, secrets or filesystem tools."}
            if len(json.dumps(result, allow_nan=False)) > 60000:
                return {"status": "unavailable", "reason": "result_limit_exceeded"}
            return result
        except Exception as exc:
            # Exception strings can contain connection URLs and credentials.
            return {"status": "unavailable", "reason": type(exc).__name__}

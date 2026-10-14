"""Public technical vocabulary. No arbitrary database/log strings cross this boundary."""
from __future__ import annotations

import math
from datetime import datetime, timezone

STAGES = {"control", "documents", "media", "transcription", "llm", "reports", "drive", "notifications", "unknown"}
SERVICES = {
    "web", "worker", "worker-control", "worker-documents", "worker-media",
    "worker-transcription", "worker-llm", "worker-reports", "worker-drive",
    "worker-notifications", "dispatch-publisher", "media-processor",
    "document-processor", "postgres", "rabbitmq", "nginx",
}
CODES = {
    "TECHNICAL_ERROR", "RUN_FAILED", "TASK_FAILED", "UNKNOWN_OUTCOME",
    "MATRIX_NOT_PUBLISHED", "MATRIX_COMPILATION_FAILED", "BLOB_SIZE_LIMIT_EXCEEDED",
    "CORRUPT_FILE", "UNSUPPORTED_FILE", "UNSUPPORTED_SCHEMA_VERSION",
    "ASSEMBLYAI_TRANSCRIPTION_FAILED", "ASSEMBLYAI_TRANSCRIPTION_TIMEOUT",
    "GOOGLE_DRIVE_REAUTH_REQUIRED", "GOOGLE_OAUTH_INVALID_GRANT",
    "DRIVE_FILE_VERSION_CHANGED", "DRIVE_FILE_CONTENT_CHANGED",
    "REPORT_VERSION_CONFLICT", "LEASE_EXPIRED_RECOVERED", "FANOUT_REQUIRED_SHARD_FAILED",
    "FANOUT_MEMBERSHIP_INCOMPLETE", "JOIN_COVERAGE_INVALID", "TOOL_POLICY_DENIED",
    "TOOL_ADAPTER_NOT_CONFIGURED", "NETWORK_ERROR", "TIMEOUT", "PROVIDER_TIMEOUT",
    "LLM_CAPABILITY_FAILED:timeout", "LLM_CAPABILITY_FAILED:network",
    "LLM_CAPABILITY_FAILED:rate_limit", "LLM_CAPABILITY_FAILED:provider_unavailable",
    "LLM_CAPABILITY_FAILED:invalid_provider_response", "LLM_CAPABILITY_FAILED:invalid_structured_output",
    "LLM_CAPABILITY_FAILED:missing_structured_output", "LLM_CAPABILITY_FAILED:incomplete_structured_output",
    "HTTP_429", "HTTP_500", "HTTP_502", "HTTP_503", "HTTP_504",
}


def safe_code(value):
    return value if isinstance(value, str) and value in CODES else "TECHNICAL_ERROR"


def safe_stage(value):
    return value if isinstance(value, str) and value in STAGES else "unknown"


def daily_report(raw):
    """Allowlist aggregate fields at both collector and Telegram boundaries."""
    if not isinstance(raw, dict):
        raise TypeError("invalid daily aggregate")
    clean, dates = {}, {}
    for key in ("start", "end", "as_of"):
        value = raw.get(key)
        if not isinstance(value, str) or len(value) > 40:
            raise ValueError("invalid daily timestamp")
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("missing daily timezone")
        dates[key] = parsed
        clean[key] = parsed.astimezone(timezone.utc).isoformat()
    if not dates["start"] <= dates["as_of"] <= dates["end"] or dates["start"] == dates["end"]:
        raise ValueError("invalid daily window")
    for key in ("candidates", "completed_runs", "succeeded", "failed", "cancelled", "timed_runs",
                "failed_attempts", "retry_attempts", "active_runs", "waiting_runs", "queued_tasks", "running_tasks"):
        value = raw.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 1000000:
            raise ValueError("invalid daily count")
        clean[key] = value
    if (clean["completed_runs"] != clean["succeeded"] + clean["failed"] + clean["cancelled"]
            or clean["candidates"] > clean["completed_runs"] or clean["timed_runs"] > clean["succeeded"]):
        raise ValueError("inconsistent daily counts")
    for key in ("avg_seconds", "p95_seconds"):
        value = raw.get(key)
        if clean["timed_runs"] == 0 and value is None:
            clean[key] = None
        elif (clean["timed_runs"] > 0 and isinstance(value, (int, float)) and not isinstance(value, bool)
              and math.isfinite(value) and 0 <= value <= 315360000):
            clean[key] = value
        else:
            raise ValueError("invalid daily timing")
    stages = raw.get("error_stages")
    if not isinstance(stages, list) or len(stages) > 100:
        raise ValueError("invalid daily errors")
    counts = {}
    for item in stages:
        if not isinstance(item, dict):
            raise TypeError("invalid error stage")
        count = item.get("count")
        if isinstance(count, bool) or not isinstance(count, int) or not 0 <= count <= 1000000:
            raise ValueError("invalid error count")
        stage = safe_stage(item.get("stage"))
        counts[stage] = counts.get(stage, 0) + count
    if sum(counts.values()) != clean["failed_attempts"]:
        raise ValueError("inconsistent error counts")
    clean["error_stages"] = [{"stage": stage, "count": count} for stage, count in sorted(counts.items()) if count]
    return clean

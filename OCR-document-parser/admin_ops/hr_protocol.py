"""Public technical vocabulary. No arbitrary database/log strings cross this boundary."""
from __future__ import annotations

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

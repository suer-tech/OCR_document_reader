from __future__ import annotations

from collections import defaultdict
from threading import Lock

from prometheus_client import Counter, Gauge, Histogram

REQUEST_COUNT = Counter("ocr_requests_total", "Total OCR API requests", ["endpoint"])
PIPELINE_LATENCY = Histogram("ocr_pipeline_latency_seconds", "Pipeline execution time", ["profile_id"])
INGEST_STATUS_COUNT = Counter("ocr_ingest_status_total", "Document ingest outcomes", ["status", "profile_id"])
PIPELINE_STEP_COUNT = Counter(
    "ocr_pipeline_step_total",
    "Pipeline step executions by status",
    ["profile_id", "step", "status"],
)
PIPELINE_STEP_LATENCY = Histogram(
    "ocr_pipeline_step_latency_seconds",
    "Pipeline step execution duration",
    ["profile_id", "step"],
)
LLM_CALL_COUNT = Counter(
    "ocr_llm_call_total",
    "LLM call outcomes by task/provider",
    ["task_name", "provider", "status"],
)
LLM_CALL_LATENCY = Histogram(
    "ocr_llm_call_latency_seconds",
    "LLM call latency by task/provider",
    ["task_name", "provider", "status"],
)
LLM_ATTEMPT_COUNT = Counter(
    "ocr_llm_attempt_total",
    "LLM model attempt outcomes by task/provider",
    ["task_name", "provider", "status"],
)
LLM_TOKEN_COUNT_MISSING = Counter(
    "ocr_llm_token_usage_missing_total",
    "LLM calls where token usage is missing in provider response",
    ["task_name", "provider", "request_type", "profile_id"],
)
LLM_PROMPT_TOKENS = Histogram(
    "ocr_llm_prompt_tokens",
    "Prompt tokens per LLM call",
    ["task_name", "provider", "request_type", "profile_id"],
)
LLM_COMPLETION_TOKENS = Histogram(
    "ocr_llm_completion_tokens",
    "Completion tokens per LLM call",
    ["task_name", "provider", "request_type", "profile_id"],
)
LLM_TOTAL_TOKENS = Histogram(
    "ocr_llm_total_tokens",
    "Total tokens per LLM call",
    ["task_name", "provider", "request_type", "profile_id"],
)
LLM_AVG_TOTAL_TOKENS = Gauge(
    "ocr_llm_avg_total_tokens",
    "Running average of total tokens for similar LLM requests",
    ["task_name", "provider", "request_type", "profile_id"],
)
QUALITY_SCORE_HIST = Histogram(
    "ocr_quality_score",
    "Distribution of quality scores",
    ["profile_id", "score_type"],
)
VALIDATION_ISSUE_COUNT = Histogram(
    "ocr_validation_issue_count",
    "Validation issue count per processed document",
    ["profile_id"],
)
FIELD_FILL_RATE = Histogram(
    "ocr_field_fill_rate",
    "Ratio of extracted fields with non-empty value",
    ["profile_id"],
)
OCR_STEP_COUNT = Counter(
    "ocr_step_total",
    "OCR step executions by status",
    ["content_type", "status"],
)
OCR_STEP_LATENCY = Histogram(
    "ocr_step_latency_seconds",
    "OCR step execution duration",
    ["content_type"],
)
_llm_token_avg_lock = Lock()
_llm_token_avg_state: dict[tuple[str, str, str, str], tuple[float, int]] = defaultdict(lambda: (0.0, 0))


def inc_request(endpoint: str) -> None:
    REQUEST_COUNT.labels(endpoint=endpoint).inc()


def observe_pipeline_latency(profile_id: str, seconds: float) -> None:
    PIPELINE_LATENCY.labels(profile_id=profile_id).observe(seconds)


def inc_ingest_status(status: str, profile_id: str = "unknown") -> None:
    INGEST_STATUS_COUNT.labels(status=status, profile_id=profile_id).inc()


def inc_pipeline_step(profile_id: str, step: str, status: str) -> None:
    PIPELINE_STEP_COUNT.labels(profile_id=profile_id, step=step, status=status).inc()


def observe_pipeline_step_latency(profile_id: str, step: str, seconds: float) -> None:
    PIPELINE_STEP_LATENCY.labels(profile_id=profile_id, step=step).observe(seconds)


def inc_llm_call(task_name: str, provider: str, status: str) -> None:
    LLM_CALL_COUNT.labels(task_name=task_name, provider=provider, status=status).inc()


def observe_llm_call_latency(task_name: str, provider: str, status: str, seconds: float) -> None:
    LLM_CALL_LATENCY.labels(task_name=task_name, provider=provider, status=status).observe(seconds)


def inc_llm_attempt(task_name: str, provider: str, status: str) -> None:
    LLM_ATTEMPT_COUNT.labels(task_name=task_name, provider=provider, status=status).inc()


def observe_llm_token_usage(
    *,
    task_name: str,
    provider: str,
    request_type: str,
    profile_id: str,
    prompt_tokens: int | None,
    completion_tokens: int | None,
    total_tokens: int | None,
) -> float | None:
    labels = {
        "task_name": task_name,
        "provider": provider,
        "request_type": request_type,
        "profile_id": profile_id,
    }
    if prompt_tokens is not None:
        LLM_PROMPT_TOKENS.labels(**labels).observe(float(prompt_tokens))
    if completion_tokens is not None:
        LLM_COMPLETION_TOKENS.labels(**labels).observe(float(completion_tokens))
    if total_tokens is None and prompt_tokens is not None and completion_tokens is not None:
        total_tokens = prompt_tokens + completion_tokens
    if total_tokens is None:
        LLM_TOKEN_COUNT_MISSING.labels(**labels).inc()
        return None

    LLM_TOTAL_TOKENS.labels(**labels).observe(float(total_tokens))
    key = (task_name, provider, request_type, profile_id)
    with _llm_token_avg_lock:
        prev_sum, prev_count = _llm_token_avg_state[key]
        new_sum = prev_sum + float(total_tokens)
        new_count = prev_count + 1
        avg_value = new_sum / new_count
        _llm_token_avg_state[key] = (new_sum, new_count)
    LLM_AVG_TOTAL_TOKENS.labels(**labels).set(avg_value)
    return avg_value


def observe_quality_score(profile_id: str, score_type: str, value: float) -> None:
    QUALITY_SCORE_HIST.labels(profile_id=profile_id, score_type=score_type).observe(value)


def observe_validation_issue_count(profile_id: str, issues: int) -> None:
    VALIDATION_ISSUE_COUNT.labels(profile_id=profile_id).observe(float(issues))


def observe_field_fill_rate(profile_id: str, fill_rate: float) -> None:
    FIELD_FILL_RATE.labels(profile_id=profile_id).observe(fill_rate)


def inc_ocr_step(content_type: str, status: str) -> None:
    OCR_STEP_COUNT.labels(content_type=content_type, status=status).inc()


def observe_ocr_step_latency(content_type: str, seconds: float) -> None:
    OCR_STEP_LATENCY.labels(content_type=content_type).observe(seconds)

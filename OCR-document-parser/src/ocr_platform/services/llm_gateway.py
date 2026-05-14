from __future__ import annotations

import json
from dataclasses import dataclass
from time import perf_counter
from typing import Any

import httpx

from ocr_platform.config.settings import get_settings
from ocr_platform.observability.logging import get_logger
from ocr_platform.observability.metrics import (
    inc_llm_attempt,
    inc_llm_call,
    observe_llm_call_latency,
    observe_llm_token_usage,
)
from ocr_platform.observability.mlflow_client import (
    mlflow_log_metric,
    mlflow_log_param,
    mlflow_log_text,
    mlflow_run,
    mlflow_set_tag,
)

logger = get_logger(__name__)


@dataclass
class LlmTokenUsage:
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None


@dataclass
class LlmJsonResult:
    parsed: dict[str, Any]
    winner_model: str
    attempts: list[dict[str, Any]]
    total_latency_ms: float
    token_usage: LlmTokenUsage | None
    avg_total_tokens: float | None


def _truncate_text(value: str, limit: int = 50000) -> str:
    if len(value) <= limit:
        return value
    return f"{value[:limit]}\n\n[truncated: original_length={len(value)}]"


def _normalize_token_count(raw: Any) -> int | None:
    if raw is None:
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


def _extract_token_usage(response_json: dict[str, Any]) -> LlmTokenUsage | None:
    usage = response_json.get("usage")
    if not isinstance(usage, dict):
        return None

    prompt_tokens = _normalize_token_count(usage.get("prompt_tokens"))
    completion_tokens = _normalize_token_count(usage.get("completion_tokens"))
    total_tokens = _normalize_token_count(usage.get("total_tokens"))

    if prompt_tokens is None:
        prompt_tokens = _normalize_token_count(usage.get("input_tokens"))
    if completion_tokens is None:
        completion_tokens = _normalize_token_count(usage.get("output_tokens"))
    if total_tokens is None and prompt_tokens is not None and completion_tokens is not None:
        total_tokens = prompt_tokens + completion_tokens

    if prompt_tokens is None and completion_tokens is None and total_tokens is None:
        return None
    return LlmTokenUsage(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
    )


def build_chat_completions_url(base_url: str) -> str:
    normalized = base_url.rstrip("/")
    if normalized.endswith("/chat/completions"):
        return normalized
    return f"{normalized}/chat/completions"


def call_llm_json_with_fallback(
    *,
    task_name: str,
    provider: str,
    request_type: str,
    profile_id: str | None = None,
    document_id: str | None = None,
    pipeline_run_id: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    timeout_seconds: float,
    models: list[str],
    system_prompt: str,
    user_content: str,
    response_schema: dict[str, Any],
    temperature: float = 0.0,
    mlflow_tags: dict[str, str] | None = None,
    mlflow_params: dict[str, Any] | None = None,
) -> LlmJsonResult | None:
    resolved_api_key, resolved_base_url = _resolve_provider_credentials(provider, api_key, base_url)
    attempts: list[dict[str, Any]] = []
    call_started = perf_counter()
    normalized_profile_id = profile_id or "all"
    logger.info(
        "llm_call_started",
        task_name=task_name,
        provider=provider,
        request_type=request_type,
        profile_id=normalized_profile_id,
        document_id=document_id,
        pipeline_run_id=pipeline_run_id,
        model_chain=models,
        timeout_seconds=timeout_seconds,
    )
    if not resolved_api_key or not resolved_base_url or not models:
        logger.warning(
            "llm_call_skipped",
            task_name=task_name,
            provider=provider,
            request_type=request_type,
            profile_id=normalized_profile_id,
            document_id=document_id,
            pipeline_run_id=pipeline_run_id,
            reason="missing_credentials_or_models",
        )
        inc_llm_call(task_name=task_name, provider=provider, status="skipped")
        _log_llm_call_to_mlflow(
            task_name=task_name,
            provider=provider,
            request_type=request_type,
            profile_id=normalized_profile_id,
            winner_model=None,
            system_prompt=system_prompt,
            user_content=user_content,
            response_schema=response_schema,
            attempts=attempts,
            token_usage=None,
            avg_total_tokens=None,
            tags=mlflow_tags or {},
            params=mlflow_params or {},
        )
        return None

    headers = {
        "Authorization": f"Bearer {resolved_api_key}",
        "Content-Type": "application/json",
    }

    with httpx.Client(timeout=timeout_seconds) as client:
        for model_name in models:
            attempt_started = perf_counter()
            payload = {
                "model": model_name,
                "temperature": temperature,
                "response_format": response_schema,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
            }
            try:
                response_text = ""
                response = client.post(
                    build_chat_completions_url(resolved_base_url),
                    json=payload,
                    headers=headers,
                )
                response.raise_for_status()
                data = response.json()
                response_text = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                parsed = json.loads(response_text)
                usage = _extract_token_usage(data)

                attempt_latency_ms = round((perf_counter() - attempt_started) * 1000, 2)
                attempts.append(
                    {
                        "model": model_name,
                        "status": "success",
                        "latency_ms": attempt_latency_ms,
                        "response_text": _truncate_text(response_text),
                        "token_usage": {
                            "prompt_tokens": usage.prompt_tokens if usage else None,
                            "completion_tokens": usage.completion_tokens if usage else None,
                            "total_tokens": usage.total_tokens if usage else None,
                        },
                    }
                )
                inc_llm_attempt(task_name=task_name, provider=provider, status="success")
                total_latency_ms = round((perf_counter() - call_started) * 1000, 2)
                inc_llm_call(task_name=task_name, provider=provider, status="success")
                observe_llm_call_latency(
                    task_name=task_name,
                    provider=provider,
                    status="success",
                    seconds=total_latency_ms / 1000.0,
                )
                avg_total_tokens = observe_llm_token_usage(
                    task_name=task_name,
                    provider=provider,
                    request_type=request_type,
                    profile_id=normalized_profile_id,
                    prompt_tokens=usage.prompt_tokens if usage else None,
                    completion_tokens=usage.completion_tokens if usage else None,
                    total_tokens=usage.total_tokens if usage else None,
                )

                logger.info(
                    "llm_call_succeeded",
                    task_name=task_name,
                    provider=provider,
                    request_type=request_type,
                    profile_id=normalized_profile_id,
                    document_id=document_id,
                    pipeline_run_id=pipeline_run_id,
                    winner_model=model_name,
                    attempts_count=len(attempts),
                    total_latency_ms=total_latency_ms,
                    prompt_tokens=usage.prompt_tokens if usage else None,
                    completion_tokens=usage.completion_tokens if usage else None,
                    total_tokens=usage.total_tokens if usage else None,
                    avg_total_tokens=avg_total_tokens,
                )
                _log_llm_call_to_mlflow(
                    task_name=task_name,
                    provider=provider,
                    request_type=request_type,
                    profile_id=normalized_profile_id,
                    winner_model=model_name,
                    system_prompt=system_prompt,
                    user_content=user_content,
                    response_schema=response_schema,
                    attempts=attempts,
                    token_usage=usage,
                    avg_total_tokens=avg_total_tokens,
                    tags=mlflow_tags or {},
                    params=mlflow_params or {},
                )
                return LlmJsonResult(
                    parsed=parsed,
                    winner_model=model_name,
                    attempts=attempts,
                    total_latency_ms=total_latency_ms,
                    token_usage=usage,
                    avg_total_tokens=avg_total_tokens,
                )
            except Exception as exc:
                attempt_latency_ms = round((perf_counter() - attempt_started) * 1000, 2)
                attempts.append(
                    {
                        "model": model_name,
                        "status": "error",
                        "error": str(exc),
                        "latency_ms": attempt_latency_ms,
                    }
                )
                inc_llm_attempt(task_name=task_name, provider=provider, status="error")
                logger.warning(
                    "llm_attempt_failed",
                    task_name=task_name,
                    provider=provider,
                    request_type=request_type,
                    profile_id=normalized_profile_id,
                    model=model_name,
                    attempt_latency_ms=attempt_latency_ms,
                    error=str(exc),
                )

    total_latency_ms = round((perf_counter() - call_started) * 1000, 2)
    inc_llm_call(task_name=task_name, provider=provider, status="failed")
    observe_llm_call_latency(
        task_name=task_name,
        provider=provider,
        status="failed",
        seconds=total_latency_ms / 1000.0,
    )
    logger.error(
        "llm_call_failed",
        task_name=task_name,
        provider=provider,
        request_type=request_type,
        profile_id=normalized_profile_id,
        document_id=document_id,
        pipeline_run_id=pipeline_run_id,
        attempts_count=len(attempts),
        total_latency_ms=total_latency_ms,
    )
    _log_llm_call_to_mlflow(
        task_name=task_name,
        provider=provider,
        request_type=request_type,
        profile_id=normalized_profile_id,
        winner_model=None,
        system_prompt=system_prompt,
        user_content=user_content,
        response_schema=response_schema,
        attempts=attempts,
        token_usage=None,
        avg_total_tokens=None,
        tags=mlflow_tags or {},
        params=mlflow_params or {},
    )
    return None


def _log_llm_call_to_mlflow(
    *,
    task_name: str,
    provider: str,
    request_type: str,
    profile_id: str,
    winner_model: str | None,
    system_prompt: str,
    user_content: str,
    response_schema: dict[str, Any],
    attempts: list[dict[str, Any]],
    token_usage: LlmTokenUsage | None,
    avg_total_tokens: float | None,
    tags: dict[str, str],
    params: dict[str, Any],
) -> None:
    try:
        with mlflow_run(task_name):
            mlflow_set_tag("component", "llm_gateway")
            mlflow_set_tag("provider", provider)
            mlflow_set_tag("request_type", request_type)
            mlflow_set_tag("profile_id", profile_id)
            mlflow_set_tag("winner_model", winner_model or "none")
            for key, value in tags.items():
                mlflow_set_tag(key, value)

            mlflow_log_param("attempt_count", len(attempts))
            mlflow_log_param(
                "attempt_models",
                ",".join(item.get("model", "unknown") for item in attempts),
            )
            for key, value in params.items():
                mlflow_log_param(key, value)
            mlflow_log_metric("llm_attempt_count", len(attempts))
            mlflow_log_metric("llm_failed_attempt_count", sum(1 for item in attempts if item.get("status") == "error"))
            mlflow_log_metric("llm_success", 1.0 if winner_model else 0.0)
            if attempts:
                total_latency_ms = sum(float(item.get("latency_ms", 0.0)) for item in attempts)
                mlflow_log_metric("llm_total_latency_ms", total_latency_ms)
                mlflow_log_metric("llm_avg_attempt_latency_ms", total_latency_ms / len(attempts))
            if token_usage is not None:
                if token_usage.prompt_tokens is not None:
                    mlflow_log_metric("llm_prompt_tokens", float(token_usage.prompt_tokens))
                if token_usage.completion_tokens is not None:
                    mlflow_log_metric("llm_completion_tokens", float(token_usage.completion_tokens))
                if token_usage.total_tokens is not None:
                    mlflow_log_metric("llm_total_tokens", float(token_usage.total_tokens))
            if avg_total_tokens is not None:
                mlflow_log_metric("llm_avg_total_tokens_for_group", avg_total_tokens)

            mlflow_log_text(system_prompt, "prompts/system_prompt.txt")
            mlflow_log_text(_truncate_text(user_content), "prompts/user_prompt.txt")
            mlflow_log_text(
                json.dumps(response_schema, ensure_ascii=False, indent=2),
                "prompts/response_schema.json",
            )
            mlflow_log_text(
                json.dumps(attempts, ensure_ascii=False, indent=2),
                "attempts/attempts.json",
            )
            response_items = []
            for item in attempts:
                response_items.append(
                    {
                        "model": item.get("model"),
                        "status": item.get("status"),
                        "latency_ms": item.get("latency_ms"),
                        "error": item.get("error"),
                        "response_text": item.get("response_text"),
                        "token_usage": item.get("token_usage"),
                    }
                )
            mlflow_log_text(
                json.dumps(response_items, ensure_ascii=False, indent=2),
                "responses/llm_responses.json",
            )
    except Exception:
        # Ошибки логирования не должны ломать бизнес-пайплайн.
        pass


def _resolve_provider_credentials(
    provider: str,
    api_key_override: str | None,
    base_url_override: str | None,
) -> tuple[str | None, str | None]:
    if api_key_override and base_url_override:
        return api_key_override, base_url_override

    settings = get_settings()
    normalized = provider.strip().lower()
    if normalized == "openrouter":
        return (
            api_key_override or settings.openrouter_api_key,
            base_url_override or settings.openrouter_base_url,
        )

    # По умолчанию считаем провайдер openai-compatible.
    return (
        api_key_override or settings.openai_api_key,
        base_url_override or settings.openai_base_url,
    )

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Annotated, Any, Iterable

from annotated_types import Ge, Le
from pydantic import BaseModel, ConfigDict

from ocr_platform.config.settings import get_settings
from ocr_platform.observability.langfuse_prompts import get_doc_type_detection_prompt
from ocr_platform.observability.logging import get_logger
from ocr_platform.observability.mlflow_client import (
    mlflow_log_metric,
    mlflow_log_param,
    mlflow_log_text,
    mlflow_run,
    mlflow_set_tag,
)
from ocr_platform.services.llm_gateway import call_llm_json_with_fallback

logger = get_logger(__name__)


@dataclass
class DocumentTypeDetection:
    document_type: str
    confidence: float
    source: str
    reasoning: str | None = None
    model_name: str | None = None


class DocumentTypeSgrResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reasoning: str
    confidence: Annotated[float, Ge(0.0), Le(1.0)]
    document_type: str


def _unknown_detection(source: str) -> DocumentTypeDetection:
    return DocumentTypeDetection(
        document_type="unknown",
        confidence=0.0,
        source=source,
        reasoning=None,
        model_name=None,
    )


def _build_json_schema(allowed_types: list[str]) -> dict:
    schema = DocumentTypeSgrResponse.model_json_schema()
    schema.setdefault("properties", {})
    schema["properties"].setdefault("document_type", {"type": "string"})
    schema["properties"]["document_type"]["enum"] = allowed_types
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "document_type_classifier_sgr",
            "strict": True,
            "schema": schema,
        },
    }


def _llm_detect_openrouter(
    text: str,
    allowed_types: Iterable[str],
    llm_config: dict[str, Any] | None,
    document_id: str | None = None,
    pipeline_run_id: str | None = None,
) -> tuple[DocumentTypeDetection | None, list[dict]]:
    settings = get_settings()
    llm_config = llm_config or settings_doc_type_llm_config()
    allowed = sorted(set(allowed_types))
    prompt = get_doc_type_detection_prompt()
    provider = str(llm_config.get("provider", "router_ai"))
    primary_model = str(llm_config.get("model") or "").strip()
    model_chain = [primary_model] if primary_model else []
    if not model_chain:
        return None, []
    temperature = float(llm_config.get("temperature", 0.0))
    timeout_seconds = float(
        llm_config.get("timeout_seconds", settings.openai_timeout_seconds)
    )
    reasoning_effort = llm_config.get("reasoning_effort")

    response_format = _build_json_schema(allowed)
    llm_result = call_llm_json_with_fallback(
        task_name="document_type_detection_llm",
        request_type="document_type_detection",
        profile_id="all",
        document_id=document_id,
        pipeline_run_id=pipeline_run_id,
        provider=provider,
        timeout_seconds=timeout_seconds,
        models=model_chain,
        system_prompt=prompt,
        user_content=text[:6000],
        response_schema=response_format,
        temperature=temperature,
        reasoning_effort=reasoning_effort,
        mlflow_tags={
            "component_task": "document_type_detection",
            "provider": provider,
            "document_id": document_id or "none",
            "pipeline_run_id": pipeline_run_id or "none",
        },
        mlflow_params={
            "allowed_types": ",".join(allowed),
            "configured_model": primary_model,
        },
    )
    if llm_result is None:
        logger.warning("document_type_llm_raw_missing")
        return None, []

    attempts = llm_result.attempts
    parsed = llm_result.parsed
    reasoning = str(parsed.get("reasoning", "")).strip() or None
    logger.info(
        "document_type_llm_raw_received",
        parsed=parsed,
        reasoning=reasoning,
        winner_model=llm_result.winner_model,
        attempts_count=len(attempts),
        total_latency_ms=llm_result.total_latency_ms,
        attempts=attempts,
        token_usage=llm_result.token_usage,
        avg_total_tokens=llm_result.avg_total_tokens,
    )
    detected_type = str(parsed.get("document_type", "unknown"))
    confidence = float(parsed.get("confidence", 0.0))
    if detected_type not in allowed:
        return None, attempts

    return (
        DocumentTypeDetection(
            document_type=detected_type,
            confidence=max(0.0, min(1.0, confidence)),
            source="llm",
            reasoning=reasoning,
            model_name=llm_result.winner_model,
        ),
        attempts,
    )


def _log_detection_to_mlflow(
    final_result: DocumentTypeDetection,
    allowed_types: list[str],
    attempts: list[dict],
    text_length: int,
) -> None:
    try:
        with mlflow_run("document_type_detection"):
            mlflow_set_tag("component", "document_type_classifier")
            mlflow_set_tag("detection_source", final_result.source)
            mlflow_set_tag("winner_model", final_result.model_name or "none")
            mlflow_log_param("allowed_types", ",".join(allowed_types))
            mlflow_log_param(
                "attempt_models", ",".join(a.get("model", "unknown") for a in attempts)
            )
            mlflow_log_param("attempt_count", len(attempts))
            mlflow_log_param("input_text_length", text_length)
            mlflow_log_metric("final_confidence", final_result.confidence)
            mlflow_log_text(
                json.dumps(attempts, ensure_ascii=False, indent=2),
                "document_type_attempts.json",
            )
    except Exception:
        pass


def detect_document_type(
    text: str,
    allowed_types: Iterable[str],
    llm_config: dict[str, Any] | None = None,
    document_id: str | None = None,
    pipeline_run_id: str | None = None,
) -> DocumentTypeDetection:
    """
    Определяет тип документа только через LLM.
    Если тип не удалось определить, возвращает unknown.
    """
    llm_config = llm_config or settings_doc_type_llm_config()
    provider = str(llm_config.get("provider", "router_ai")).strip().lower()
    allowed = sorted(set(allowed_types))
    attempts: list[dict] = []

    final_result: DocumentTypeDetection
    if not _provider_has_credentials(provider):
        logger.warning(
            "document_type_llm_skipped",
            reason="missing_provider_credentials",
            provider=provider,
        )
        final_result = _unknown_detection("llm_unavailable")
    elif not text.strip():
        logger.info("document_type_llm_skipped", reason="empty_input_text")
        final_result = _unknown_detection("llm_no_text")
    else:
        llm_result, attempts = _llm_detect_openrouter(
            text,
            allowed,
            llm_config,
            document_id=document_id,
            pipeline_run_id=pipeline_run_id,
        )
        if llm_result is not None:
            final_result = llm_result
        else:
            logger.warning("document_type_llm_unresolved")
            final_result = _unknown_detection("llm_unresolved")

    _log_detection_to_mlflow(
        final_result=final_result,
        allowed_types=allowed,
        attempts=attempts,
        text_length=len(text),
    )
    return final_result


def settings_doc_type_llm_config() -> dict[str, Any]:
    """
    Одна настроенная модель для вызовов без llm-конфигурации router.yaml.
    """
    settings = get_settings()
    return {
        "provider": settings.document_type_llm_provider,
        "model": settings.document_type_llm_model,
        "temperature": 0.0,
        "timeout_seconds": settings.openai_timeout_seconds,
    }


def _provider_has_credentials(provider: str) -> bool:
    settings = get_settings()
    if provider == "router_ai":
        return bool(settings.router_ai_api_key)
    if provider == "openrouter":
        return bool(settings.openrouter_api_key)
    return bool(settings.openai_api_key)

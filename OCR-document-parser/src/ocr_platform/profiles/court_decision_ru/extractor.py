"""LLM-экстрактор полей судебного решения (court_decision_ru)."""

from __future__ import annotations

from typing import Any, Dict

from pydantic import BaseModel, ConfigDict, Field, create_model

from ocr_platform.observability.logging import get_logger
from ocr_platform.services.llm_gateway import call_llm_json_with_fallback

logger = get_logger(__name__)


class FieldExtractionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reasoning: str
    confidence: float
    value: str | None


def _safe_model_suffix(profile_id: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in profile_id)


def _build_fields_schema(profile_id: str, fields_cfg: dict[str, Any]) -> dict[str, Any]:
    fields_block_definitions: dict[str, tuple[type[FieldExtractionResult], Field]] = {}
    for field_name, cfg in fields_cfg.items():
        if not isinstance(cfg, dict):
            cfg = {}
        label_ru = str(cfg.get("label_ru", field_name))
        description_ru = str(cfg.get("description_ru", "")).strip()
        required = bool(cfg.get("required", False))
        description_parts = [f"Поле: {label_ru}. Обязательное: {required}."]
        if description_ru:
            description_parts.append(description_ru)
        fields_block_definitions[field_name] = (
            FieldExtractionResult,
            Field(description=" ".join(description_parts)),
        )

    model_suffix = _safe_model_suffix(profile_id) or "dynamic"
    fields_block_model = create_model(
        f"FieldsBlock_{model_suffix}",
        __config__=ConfigDict(extra="forbid"),
        **fields_block_definitions,
    )
    extraction_response_model = create_model(
        f"ExtractionResponse_{model_suffix}",
        __config__=ConfigDict(extra="forbid"),
        fields=(fields_block_model, ...),
    )
    return {
        "type": "json_schema",
        "json_schema": {
            "name": f"field_extraction_{model_suffix}",
            "strict": True,
            "schema": extraction_response_model.model_json_schema(),
        },
    }


def _extraction_prompt(fields_cfg: dict[str, Any]) -> str:
    lines: list[str] = []
    for field_name, cfg in fields_cfg.items():
        if not isinstance(cfg, dict):
            cfg = {}
        label_ru = cfg.get("label_ru", field_name)
        description_ru = cfg.get("description_ru", "")
        required = bool(cfg.get("required", False))
        lines.append(
            f"- {field_name}: {label_ru}. Обязательное: {required}. Описание: {description_ru}"
        )
    fields_hint = "\n".join(lines)
    return (
        "Извлеки поля из текста судебного документа. "
        "Верни только JSON по схеме. "
        "Для каждого поля заполни reasoning (как искал и почему выбрано), затем confidence (0..1), затем value. "
        "Если поле не найдено, укажи value=null, confidence=0.0 и объясни в reasoning, что значение не найдено. "
        "Не смешивай рассуждения между полями. "
        f"\n\nПоля для извлечения:\n{fields_hint}"
    )


def _candidate_models(primary_model: str | None, fallback_from_profile: list[str] | None = None) -> list[str]:
    """Цепочка моделей только из llm_extraction (YAML). Без fallback на settings/env."""
    fallback_models = []
    if fallback_from_profile:
        fallback_models = [m.strip() for m in fallback_from_profile if m and m.strip()]
    models: list[str] = []
    if primary_model:
        models.append(primary_model)
    for model in fallback_models:
        if model not in models:
            models.append(model)
    return models[:3]


class CourtDecisionRuExtractor:
    """LLM-экстрактор для профиля court_decision_ru."""

    def extract(
        self,
        text: str,
        profile_config: dict[str, Any],
        profile_id: str,
        pipeline_run_id: str,
        document_id: str,
    ) -> Dict[str, dict]:
        if not text.strip():
            return {}

        fields_cfg = profile_config.get("fields_llm", {})
        field_names = list(fields_cfg.keys())
        if not field_names:
            return {}

        models_cfg = profile_config.get("models", {})
        llm_extraction_cfg = models_cfg.get("llm_extraction", {})
        provider = str(llm_extraction_cfg.get("provider", "")).strip()
        if not provider:
            raise ValueError("models.llm_extraction.provider must be set in profile")
        primary_model = str(llm_extraction_cfg.get("model", "")).strip() or None
        fallback_models = llm_extraction_cfg.get("fallback_models", [])
        if not isinstance(fallback_models, list):
            fallback_models = []
        temperature = float(llm_extraction_cfg.get("temperature", 0.0))
        timeout_seconds = float(llm_extraction_cfg.get("timeout_seconds", 180.0))
        model_chain = _candidate_models(primary_model, fallback_models)

        schema = _build_fields_schema(profile_id, fields_cfg)
        prompt = _extraction_prompt(fields_cfg)
        logger.info(
            "field_extraction_llm_started",
            document_id=document_id,
            pipeline_run_id=pipeline_run_id,
            profile_id=profile_id,
            provider=provider,
            model_chain=model_chain,
            field_count=len(field_names),
        )
        llm_result = call_llm_json_with_fallback(
            task_name="field_extraction_llm",
            request_type="field_extraction",
            profile_id=profile_id,
            document_id=document_id,
            pipeline_run_id=pipeline_run_id,
            provider=provider,
            timeout_seconds=timeout_seconds,
            models=model_chain,
            system_prompt=prompt,
            user_content=text,
            response_schema=schema,
            temperature=temperature,
            mlflow_tags={
                "component_task": "field_extraction",
                "provider": provider,
                "profile_id": profile_id,
                "pipeline_run_id": pipeline_run_id,
                "document_id": document_id,
            },
            mlflow_params={
                "field_count": len(field_names),
                "configured_model": primary_model or "from_default_chain",
            },
        )
        if llm_result is None:
            logger.warning(
                "field_extraction_llm_failed",
                document_id=document_id,
                pipeline_run_id=pipeline_run_id,
                profile_id=profile_id,
                provider=provider,
            )
            return {}

        logger.info(
            "field_extraction_llm_succeeded",
            document_id=document_id,
            pipeline_run_id=pipeline_run_id,
            profile_id=profile_id,
            winner_model=llm_result.winner_model,
            attempts_count=len(llm_result.attempts),
            total_latency_ms=llm_result.total_latency_ms,
            token_usage=llm_result.token_usage,
            avg_total_tokens=llm_result.avg_total_tokens,
            attempts=llm_result.attempts,
        )
        parsed_fields = llm_result.parsed.get("fields", {})
        if not isinstance(parsed_fields, dict):
            return {}

        normalized: Dict[str, dict] = {}
        for field_name in field_names:
            raw_value = parsed_fields.get(field_name, {})
            if isinstance(raw_value, dict):
                confidence = float(raw_value.get("confidence", 0.0))
                normalized[field_name] = {
                    "reasoning": str(raw_value.get("reasoning", "")),
                    "value": raw_value.get("value"),
                    "confidence": max(0.0, min(1.0, confidence)),
                }
            else:
                normalized[field_name] = {
                    "reasoning": "Поле не удалось распарсить из ответа LLM.",
                    "value": None,
                    "confidence": 0.0,
                }
        return normalized

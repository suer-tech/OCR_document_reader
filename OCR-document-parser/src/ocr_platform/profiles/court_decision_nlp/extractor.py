"""NLP-экстрактор полей судебного решения (court_decision_nlp).

Вся логика из NLP-entity-extractor перенесена в этот профиль:
- NER (transformer) + правила (rules) + постобработка (postprocess).
- Без вызова внешних LLM/API.
"""

from __future__ import annotations

from time import perf_counter
from typing import Any, Dict

from ocr_platform.observability.logging import get_logger
from ocr_platform.observability.mlflow_client import (
    mlflow_log_metric,
    mlflow_log_param,
    mlflow_run,
    mlflow_set_tag,
)

from .predictor import FioPredictor

logger = get_logger(__name__)

# Маппинг nlp_source -> способ извлечения значения из PredictionResult.
_NLP_FIELD_GETTERS = {
    "applicant_fio": lambda r: r.fields.applicant_fio.normalized if r.fields.applicant_fio else None,
    "judge_fio": lambda r: r.fields.judge_fio.normalized if r.fields.judge_fio else None,
    "court_name": lambda r: r.fields.court_name,
    "case_number": lambda r: r.fields.case_number,
    "inn": lambda r: r.fields.inn,
    "decision_date": lambda r: r.fields.decision_date,
    "procedure_end_date": lambda r: r.fields.procedure_end_date,
    "procedure_end_date_is_calculated": lambda r: r.fields.procedure_end_date_is_calculated,
    "procedure_type": lambda r: r.fields.procedure_type,
    "early_report_deadline": lambda r: r.fields.early_report_deadline,
    "motivating_part": lambda r: r.fields.motivating_part,
    "resolutive_part": lambda r: r.fields.resolutive_part,
}


class CourtDecisionNlpExtractor:
    """Локальная NLP-экстракция: NER + правила, без LLM."""

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

        extraction_started = perf_counter()
        try:
            models_cfg = profile_config.get("models", {})
            nlp_cfg = models_cfg.get("nlp", {}) if isinstance(models_cfg.get("nlp"), dict) else {}
            model_dir = nlp_cfg.get("model_dir")
            max_batch_size = nlp_cfg.get("max_batch_size")
            predictor = FioPredictor(model_dir=model_dir, max_batch_size=max_batch_size)
            prediction = predictor.predict(text)
        except Exception as exc:
            elapsed_ms = (perf_counter() - extraction_started) * 1000.0
            logger.warning(
                "nlp_extraction_failed",
                profile_id=profile_id,
                document_id=document_id,
                error=str(exc),
            )
            _log_nlp_extraction_to_mlflow(
                profile_id=profile_id,
                document_id=document_id,
                pipeline_run_id=pipeline_run_id,
                model_version="none",
                field_count=0,
                filled_count=0,
                confidence=0.0,
                latency_ms=elapsed_ms,
                success=False,
            )
            return {}

        base_confidence = prediction.confidence
        fields_cfg = profile_config.get("fields_nlp", {})
        normalized: Dict[str, dict] = {}
        for field_name, cfg in fields_cfg.items():
            if not isinstance(cfg, dict):
                normalized[field_name] = {"reasoning": "", "value": None, "confidence": 0.0}
                continue
            nlp_source = cfg.get("nlp_source")
            if not nlp_source or nlp_source not in _NLP_FIELD_GETTERS:
                normalized[field_name] = {
                    "reasoning": f"nlp_source '{nlp_source}' не поддерживается" if nlp_source else "",
                    "value": None,
                    "confidence": 0.0,
                }
                continue
            getter = _NLP_FIELD_GETTERS[nlp_source]
            value = getter(prediction)
            normalized[field_name] = {
                "reasoning": f"NLP ({prediction.model_version})",
                "value": value,
                "confidence": base_confidence,
            }

        elapsed_ms = (perf_counter() - extraction_started) * 1000.0
        filled_count = sum(1 for v in normalized.values() if isinstance(v, dict) and v.get("value"))
        _log_nlp_extraction_to_mlflow(
            profile_id=profile_id,
            document_id=document_id,
            pipeline_run_id=pipeline_run_id,
            model_version=prediction.model_version,
            field_count=len(normalized),
            filled_count=filled_count,
            confidence=base_confidence,
            latency_ms=elapsed_ms,
            success=True,
        )

        return normalized


def _log_nlp_extraction_to_mlflow(
    *,
    profile_id: str,
    document_id: str,
    pipeline_run_id: str,
    model_version: str,
    field_count: int,
    filled_count: int,
    confidence: float,
    latency_ms: float,
    success: bool,
) -> None:
    try:
        with mlflow_run("field_extraction_nlp"):
            mlflow_set_tag("component", "nlp_extraction")
            mlflow_set_tag("component_task", "field_extraction")
            mlflow_set_tag("profile_id", profile_id)
            mlflow_set_tag("pipeline_run_id", pipeline_run_id)
            mlflow_set_tag("document_id", document_id)
            mlflow_set_tag("model_version", model_version)

            mlflow_log_param("field_count", field_count)
            mlflow_log_param("fields_filled", filled_count)
            mlflow_log_param("configured_model", model_version)

            mlflow_log_metric("extraction_success", 1.0 if success else 0.0)
            mlflow_log_metric("extraction_latency_ms", latency_ms)
            mlflow_log_metric("extraction_confidence", confidence)
            mlflow_log_metric("field_fill_rate", filled_count / field_count if field_count else 0.0)
    except Exception:
        pass

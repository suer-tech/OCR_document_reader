"""Локальный NLP-экстрактор для профиля rtk_nlp."""

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

from . import rules

logger = get_logger(__name__)

# Маппинг nlp_source -> функция извлечения
_NLP_FIELD_GETTERS = {
    "creditor": rules.extract_creditor,
    "claims_amount": rules.extract_claims_amount,
    "grounds": rules.extract_grounds,
    "case_number": rules.extract_case_number,
}


def _is_generic_creditor(val: str | None) -> bool:
    if not val:
        return True
    val_clean = (
        val.lower()
        .strip()
        .replace('"', "")
        .replace("«", "")
        .replace("»", "")
        .replace("'", "")
        .strip()
    )
    generic_phrases = {
        "общество с ограниченной ответственностью",
        "общество с ограниченной",
        "акционерное общество",
        "публичное акционерное общество",
        "непубличное акционерное общество",
        "закрытое акционерное общество",
        "открытое акционерное общество",
        "ооо",
        "ао",
        "пао",
        "зао",
        "оао",
        "нао",
    }
    return val_clean in generic_phrases


class RtkNlpExtractor:
    """Локальная NLP-экстракция для RTK на базе Regex-правил."""

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

        # Для RTK у нас нет NER модели, поэтому prediction = text
        base_confidence = (
            0.5  # Пониженный confidence, так как используется только regex
        )

        fields_cfg = profile_config.get("fields_nlp", {})
        normalized: Dict[str, dict] = {}
        for field_name, cfg in fields_cfg.items():
            confidence = base_confidence
            if not isinstance(cfg, dict):
                normalized[field_name] = {
                    "reasoning": "",
                    "value": None,
                    "confidence": 0.0,
                }
                continue
            nlp_source = cfg.get("nlp_source")
            if not nlp_source or nlp_source not in _NLP_FIELD_GETTERS:
                normalized[field_name] = {
                    "reasoning": f"nlp_source '{nlp_source}' не поддерживается"
                    if nlp_source
                    else "",
                    "value": None,
                    "confidence": 0.0,
                }
                continue

            getter = _NLP_FIELD_GETTERS[nlp_source]
            value = getter(text)

            reasoning = (
                "NLP (Regex Rule)"
                if nlp_source != "creditor"
                else "LLM (gpt-oss:20b via Ollama)"
            )

            if field_name == "creditor" and value:
                confidence = 0.9

            normalized[field_name] = {
                "reasoning": reasoning if value else "Значение не найдено паттернами",
                "value": value,
                "confidence": confidence if value else 0.0,
            }

        elapsed_ms = (perf_counter() - extraction_started) * 1000.0
        filled_count = sum(
            1 for v in normalized.values() if isinstance(v, dict) and v.get("value")
        )

        try:
            with mlflow_run("field_extraction_nlp"):
                mlflow_set_tag("component", "nlp_extraction")
                mlflow_set_tag("component_task", "field_extraction")
                mlflow_set_tag("profile_id", profile_id)
                mlflow_set_tag("pipeline_run_id", pipeline_run_id)
                mlflow_set_tag("document_id", document_id)
                mlflow_set_tag("model_version", "rtk_regex_v1")

                mlflow_log_param("field_count", len(normalized))
                mlflow_log_param("fields_filled", filled_count)
                mlflow_log_param("configured_model", "rtk_regex_v1")

                mlflow_log_metric("extraction_success", 1.0)
                mlflow_log_metric("extraction_latency_ms", elapsed_ms)
                mlflow_log_metric("extraction_confidence", base_confidence)
                mlflow_log_metric(
                    "field_fill_rate",
                    filled_count / len(normalized) if normalized else 0.0,
                )
        except Exception:
            pass

        return normalized

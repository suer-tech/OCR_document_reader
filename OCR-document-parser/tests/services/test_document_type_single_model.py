"""Classification uses the configured provider/model, without model switching."""
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from ocr_platform.config.settings import Settings
from ocr_platform.services import document_type_service as service


MODEL = "anthropic/claude-sonnet-5.5"


def setup(monkeypatch, *, fails=False):
    calls = []
    settings = Settings(_env_file=None, router_ai_api_key="test-key", openai_api_key=None,
                        llm_default_fallback_models="forbidden-old-model",
                        openai_doc_type_models="another-forbidden-model")
    monkeypatch.setattr(service, "get_settings", lambda: settings)
    monkeypatch.setattr(service, "get_doc_type_detection_prompt", lambda: "Classify the document")
    monkeypatch.setattr(service, "_log_detection_to_mlflow", lambda **kwargs: None)

    def provider(**kwargs):
        calls.append(kwargs)
        if fails:
            return None
        return SimpleNamespace(
            parsed={"document_type": "court_decision", "confidence": .99, "reasoning": "court act"},
            winner_model=MODEL, attempts=[{"model": MODEL}], total_latency_ms=1,
            token_usage=None, avg_total_tokens=None,
        )

    monkeypatch.setattr(service, "call_llm_json_with_fallback", provider)
    return calls


def test_classifier_uses_router_ai_credentials_and_ignores_old_model_chain(monkeypatch):
    calls = setup(monkeypatch)
    config = {"provider": "router_ai", "model": MODEL, "fallback_models": ["openai/gpt-oss-120b"]}
    result = service.detect_document_type("source", ["court_decision"], config)
    assert result.document_type == "court_decision"
    assert result.model_name == MODEL
    assert len(calls) == 1
    assert calls[0]["provider"] == "router_ai" and calls[0]["models"] == [MODEL]


def test_classifier_failure_returns_unknown_without_trying_another_model(monkeypatch):
    calls = setup(monkeypatch, fails=True)
    result = service.detect_document_type("source", ["court_decision"],
                                          {"provider": "router_ai", "model": MODEL,
                                           "fallback_models": ["openai/gpt-oss-120b"]})
    assert result.document_type == "unknown" and result.source == "llm_unresolved"
    assert len(calls) == 1 and calls[0]["models"] == [MODEL]


def test_classifier_without_explicit_config_uses_one_settings_model(monkeypatch):
    calls = setup(monkeypatch)
    result = service.detect_document_type("source", ["court_decision"])
    assert result.document_type == "court_decision"
    assert calls[0]["provider"] == "router_ai" and calls[0]["models"] == [MODEL]
    assert "fallback_models" not in service.settings_doc_type_llm_config()


@pytest.mark.parametrize("model", [None, "", " "])
def test_missing_primary_model_does_not_use_legacy_fallbacks(monkeypatch, model):
    calls = setup(monkeypatch)
    result = service.detect_document_type("source", ["court_decision"],
                                          {"provider": "router_ai", "model": model,
                                           "fallback_models": ["openai/gpt-oss-120b"]})
    assert result.document_type == "unknown"
    assert calls == []


def test_current_court_and_router_configs_use_one_router_ai_model_per_stage():
    root = Path(__file__).parents[2] / "src/ocr_platform/config/pipelines"
    court = yaml.safe_load((root / "profiles/court_decision_ru.yaml").read_text(encoding="utf-8"))
    router = yaml.safe_load((root / "system/router.yaml").read_text(encoding="utf-8"))
    for config, expected_model in (
        (court["models"]["llm_extraction"], "google/gemini-4-argon"),
        (router["detection"]["llm"], MODEL),
    ):
        assert config["provider"] == "router_ai"
        assert config["model"] == expected_model
        assert not config.get("fallback_models")

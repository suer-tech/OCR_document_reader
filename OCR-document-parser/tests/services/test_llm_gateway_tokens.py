from __future__ import annotations

from typing import Any

from ocr_platform.services import llm_gateway


def test_extract_token_usage_from_openai_usage() -> None:
    data = {
        "usage": {
            "prompt_tokens": 120,
            "completion_tokens": 30,
            "total_tokens": 150,
        }
    }

    usage = llm_gateway._extract_token_usage(data)

    assert usage is not None
    assert usage.prompt_tokens == 120
    assert usage.completion_tokens == 30
    assert usage.total_tokens == 150


def test_extract_token_usage_from_input_output_usage() -> None:
    data = {
        "usage": {
            "input_tokens": 80,
            "output_tokens": 20,
        }
    }

    usage = llm_gateway._extract_token_usage(data)

    assert usage is not None
    assert usage.prompt_tokens == 80
    assert usage.completion_tokens == 20
    assert usage.total_tokens == 100


def test_call_llm_json_with_fallback_reports_token_usage(monkeypatch: Any) -> None:
    class DummyResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return {
                "choices": [{"message": {"content": '{"status":"ok"}'}}],
                "usage": {
                    "prompt_tokens": 90,
                    "completion_tokens": 10,
                    "total_tokens": 100,
                },
            }

    class DummyClient:
        def __init__(self, timeout: float) -> None:
            self.timeout = timeout

        def __enter__(self) -> "DummyClient":
            return self

        def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
            return False

        def post(self, url: str, json: dict[str, Any], headers: dict[str, str]) -> DummyResponse:
            assert url.endswith("/chat/completions")
            assert json["model"] == "test-model"
            assert "Authorization" in headers
            return DummyResponse()

    monkeypatch.setattr(llm_gateway, "_resolve_provider_credentials", lambda *_: ("key", "https://api.example.com/v1"))
    monkeypatch.setattr(llm_gateway.httpx, "Client", DummyClient)
    monkeypatch.setattr(llm_gateway, "inc_llm_attempt", lambda **_: None)
    monkeypatch.setattr(llm_gateway, "inc_llm_call", lambda **_: None)
    monkeypatch.setattr(llm_gateway, "observe_llm_call_latency", lambda **_: None)
    monkeypatch.setattr(llm_gateway, "_log_llm_call_to_mlflow", lambda **_: None)

    observed: dict[str, Any] = {}

    def _fake_observe_llm_token_usage(**kwargs: Any) -> float:
        observed.update(kwargs)
        return 100.0

    monkeypatch.setattr(llm_gateway, "observe_llm_token_usage", _fake_observe_llm_token_usage)

    result = llm_gateway.call_llm_json_with_fallback(
        task_name="field_extraction_llm",
        provider="openrouter",
        request_type="field_extraction",
        profile_id="court_decision_ru",
        document_id="doc-1",
        pipeline_run_id="run-1",
        timeout_seconds=10.0,
        models=["test-model"],
        system_prompt="prompt",
        user_content="content",
        response_schema={"type": "json_object"},
    )

    assert result is not None
    assert result.parsed == {"status": "ok"}
    assert result.token_usage is not None
    assert result.token_usage.prompt_tokens == 90
    assert result.token_usage.completion_tokens == 10
    assert result.token_usage.total_tokens == 100
    assert result.avg_total_tokens == 100.0

    assert observed["task_name"] == "field_extraction_llm"
    assert observed["provider"] == "openrouter"
    assert observed["request_type"] == "field_extraction"
    assert observed["profile_id"] == "court_decision_ru"
    assert observed["prompt_tokens"] == 90
    assert observed["completion_tokens"] == 10
    assert observed["total_tokens"] == 100

from types import SimpleNamespace

import pytest

from ocr_platform.services import llm_gateway


@pytest.mark.parametrize("base", ["https://routerai.ru/api/v1", "https://routerai.ru/api/v1/", "https://routerai.ru/api/v1/decisions", "https://routerai.ru/api/v1/chat/completions"])
def test_decisions_url(base):
    assert llm_gateway.build_decisions_url(base) == "https://routerai.ru/api/v1/decisions"


@pytest.mark.parametrize("value", [-1, 80, 95, True, False, "0.98", None, float("nan"), float("inf")])
def test_malformed_probability_cannot_confirm(value):
    assert llm_gateway.decision_probabilities({"answers": {"f": {"type": "noul", "noul": value}}}, ["f"]) == {}


def test_only_requested_native_probabilities():
    answers = {"yes": {"type": "noul", "noul": 1}, "no": {"type": "noul", "noul": 0},
               "other": {"type": "noul", "noul": .99}, "choice": {"type": "choice", "noul": .99}}
    assert llm_gateway.decision_probabilities({"answers": answers}, ["yes", "no", "missing", "choice"]) == {"yes": 1., "no": 0.}


def test_router_ai_uses_own_credentials(monkeypatch):
    monkeypatch.setattr(llm_gateway, "get_settings", lambda: SimpleNamespace(router_ai_api_key="router-key", router_ai_base_url="https://routerai.ru/api/v1", openai_api_key="wrong-key", openai_base_url="wrong-url"))
    assert llm_gateway._resolve_provider_credentials("router_ai", None, None) == ("router-key", "https://routerai.ru/api/v1")


def test_native_request_and_shared_telemetry(monkeypatch):
    calls = []
    class Client:
        def __init__(self, timeout):
            assert timeout == 15
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def post(self, url, json, headers):
            calls.append((url, json))
            return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"answers": {"months": {"type": "noul", "noul": .99}}, "usage": {"input_tokens": 100, "output_tokens": 8}})
    logs = []
    monkeypatch.setattr(llm_gateway.httpx, "Client", Client)
    monkeypatch.setattr(llm_gateway, "_resolve_provider_credentials", lambda *_: ("key", "https://routerai.ru/api/v1"))
    monkeypatch.setattr(llm_gateway, "_log_llm_call_to_mlflow", lambda **kwargs: logs.append(kwargs))
    questions = {"months": {"type": "noul", "instructions": "Is six months explicitly assigned?"}}
    result = llm_gateway.call_llm_json_with_fallback(task_name="court_jev", provider="router_ai", request_type="decisions", timeout_seconds=15, models=["typesafe/jev-1.14"], system_prompt="", user_content="raw", response_schema=questions, decisions_questions=questions, decisions_state={"document": "raw", "months": 6})
    assert calls == [("https://routerai.ru/api/v1/decisions", {"model": "typesafe/jev-1.14", "state": {"document": "raw", "months": 6}, "questions": questions})]
    assert result.token_usage.total_tokens == 108
    assert llm_gateway.decision_probabilities(result.parsed, ["months"]) == {"months": .99}
    assert logs[0]["winner_model"] == "typesafe/jev-1.14"
    assert logs[0]["request_type"] == "decisions"


def test_failed_decisions_do_not_switch_to_chat(monkeypatch):
    requests = []
    class Client:
        def __init__(self, timeout):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def post(self, url, json, headers):
            requests.append((url, json))
            return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"choices": []})
    monkeypatch.setattr(llm_gateway.httpx, "Client", Client)
    monkeypatch.setattr(llm_gateway, "_resolve_provider_credentials", lambda *_: ("key", "https://routerai.ru/api/v1"))
    monkeypatch.setattr(llm_gateway, "_log_llm_call_to_mlflow", lambda **_: None)
    result = llm_gateway.call_llm_json_with_fallback(task_name="court_jev", provider="router_ai", request_type="decisions", timeout_seconds=15, models=["typesafe/jev-1.14"], system_prompt="", user_content="raw", response_schema={}, decisions_questions={"f": {"type": "noul", "instructions": "Correct?"}})
    assert result is None
    assert len(requests) == 1
    assert requests[0][0].endswith("/decisions")

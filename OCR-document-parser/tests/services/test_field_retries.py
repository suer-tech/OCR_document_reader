from copy import deepcopy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from ocr_platform.orchestration.router import load_profile
from ocr_platform.services import extraction_agent, passport_vision_service as vision
from ocr_platform.services import court_selective_llm as court
from ocr_platform.services.field_retries import empty_value, preserves_values
from ocr_platform.services.validation_service import profile_field_issues, review_requirement


@pytest.mark.parametrize("value", [None, "", " \n\t", [], {}, {"name": None}, [None]])
def test_empty_values(value):
    assert empty_value(value)


@pytest.mark.parametrize("value", [False, 0, 0.0, "0", "0.00", {"flag": False}, [0]])
def test_zero_and_false_are_not_empty(value):
    assert not empty_value(value)


def test_merge_cannot_change_known_cells_or_reorder_claims():
    old = [{"priority_queue": "3 очередь", "amount": None, "duty": "0.00", "salary": False}]
    assert preserves_values(old, [{**old[0], "amount": "10.00"}])
    assert not preserves_values(old, [{**old[0], "amount": "10.00", "duty": "1.00"}])
    assert not preserves_values(old, [{**old[0], "priority_queue": "2 очередь"}])
    assert not preserves_values(False, 0)


def passport_answer(profile, **updates):
    values = {"passport_series": "0102", "passport_number": "001234", "last_name": "Тестов",
              "first_name": "Иван", "patronymic": None, "gender": "Мужской", "birth_date": "01.02.1990",
              "birth_place": "Г. ТЕСТ", "issue_date": "03.04.2010", "department_code": "001-002",
              "issued_by": "ОТДЕЛОМ ТЕСТ", "registration_address": "123456, г. Тест, ул. Тестовая, д. 1",
              "post_index": None, "region": None, "city": None, "street": None}
    return {**{n: values[n] for n in profile["fields"]}, "raw_text": "Исходный тестовый текст", **updates}


@pytest.fixture
def passport_transport(monkeypatch):
    client = SimpleNamespace(post=AsyncMock())
    class Context:
        async def __aenter__(self):
            return client
        async def __aexit__(self, *a):
            return False
    monkeypatch.setattr(vision.httpx, "AsyncClient", lambda **k: Context())
    monkeypatch.setattr(vision, "_document_images", lambda p: [{"type": "image_url", "image_url": {"url": "mock"}}])
    monkeypatch.setattr(vision, "get_settings", lambda: SimpleNamespace(
        router_ai_api_key="test", router_ai_base_url="https://mock.invalid"))
    def response(data, status=200):
        return httpx.Response(status, request=httpx.Request("POST", "https://mock.invalid"),
            json={"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(data)}}]})
    return client, response


def requested_passport_fields(call):
    prompt = call.kwargs["json"]["messages"][0]["content"][0]["text"]
    shape, _ = json.JSONDecoder().raw_decode(prompt.split("JSON shape:\n")[1])
    return set(shape) - {"raw_text"}


@pytest.mark.asyncio
@pytest.mark.parametrize("profile_id", ["passport_main", "passport_registration"])
async def test_passport_optional_nulls_never_trigger_retry(passport_transport, profile_id):
    client, respond = passport_transport
    profile = load_profile(profile_id)
    client.post.return_value = respond(passport_answer(profile))
    await vision.extract_passport_fields("mock", profile)
    assert client.post.await_count == 1


@pytest.mark.asyncio
async def test_passport_retries_only_remaining_required_values(passport_transport):
    client, respond = passport_transport
    profile = load_profile("passport_main")
    initial = passport_answer(profile, last_name=None, birth_place=" ")
    client.post.side_effect = [respond(initial),
        respond({"last_name": None, "birth_place": "Г. ТЕСТ", "raw_text": "Место рождения: Г. ТЕСТ"}),
        respond({"last_name": "Тестов", "raw_text": "Фамилия: ТЕСТОВ"})]
    result = await vision.extract_passport_fields("mock", profile)
    assert client.post.await_count == 3
    assert requested_passport_fields(client.post.await_args_list[1]) == {"last_name", "birth_place"}
    assert requested_passport_fields(client.post.await_args_list[2]) == {"last_name"}
    assert result["last_name"]["value"] == "Тестов"
    assert result["passport_number"]["value"] == "001234"
    assert result["patronymic"]["value"] is None  # Optional: never requested again.
    assert "Фамилия: ТЕСТОВ" in result["_raw_text"]


@pytest.mark.asyncio
async def test_passport_stops_after_three_retries_and_preserves_good_values(passport_transport):
    client, respond = passport_transport
    profile = load_profile("passport_main")
    client.post.side_effect = [respond(passport_answer(profile, last_name=None)),
                              *[respond({"last_name": None, "raw_text": ""}) for _ in range(3)]]
    result = await vision.extract_passport_fields("mock", profile)
    assert client.post.await_count == 4
    assert result["last_name"]["value"] is None
    assert result["last_name"]["empty_field_retries"] == 3
    assert result["passport_number"]["value"] == "001234"
    assert all(requested_passport_fields(c) == {"last_name"} for c in client.post.await_args_list[1:])


@pytest.mark.asyncio
async def test_failed_retry_does_not_lose_initial_passport_values(passport_transport):
    client, respond = passport_transport
    profile = load_profile("passport_main")
    client.post.side_effect = [respond(passport_answer(profile, last_name=None)),
                              respond({}, 500), respond({"last_name": "Тестов", "raw_text": "ТЕСТОВ"})]
    result = await vision.extract_passport_fields("mock", profile)
    assert client.post.await_count == 3 and result["passport_number"]["value"] == "001234"
    assert result["last_name"]["value"] == "Тестов"


@pytest.mark.asyncio
@pytest.mark.parametrize("profile_id", ["rtk", "rtk2"])
async def test_agent_retries_only_required_and_keeps_fixed_context(monkeypatch, profile_id):
    initial = {"required": {"value": None}, "optional": {"value": None},
               "flag": {"value": False}, "amount": {"value": 0}, "creditor": {"value": "ООО Тест"}}
    calls = []
    async def extract(text, cfg, *args, **kwargs):
        calls.append((cfg, deepcopy(kwargs)))
        if len(calls) == 1:
            return deepcopy(initial)
        return {"required": {"value": "found"}, "optional": {"value": "unrequested"},
                "creditor": {"value": "Changed"}, "flag": {"value": True}}
    monkeypatch.setattr(extraction_agent, "_run_agent_extraction_impl", extract)
    monkeypatch.setattr(extraction_agent, "resolve_llm_model", lambda p: SimpleNamespace(name=lambda: "test"))
    cfg = {n: {"required": n != "optional"} for n in initial}
    result = await extraction_agent.run_agent_extraction("source", cfg, profile_id,
        {"models": {"llm_extraction": {"provider": "router_ai", "model": "test"}}})
    assert len(calls) == 2 and set(calls[1][0]) == {"required"}
    assert calls[1][1]["retained_fields"]["creditor"]["value"] == "ООО Тест"
    assert result["required"]["value"] == "found"
    assert result["optional"]["value"] is None
    assert result["flag"]["value"] is False and result["amount"]["value"] == 0
    assert result["creditor"] == initial["creditor"]


@pytest.mark.asyncio
@pytest.mark.parametrize("required,expected", [(True, 4), (False, 1)])
async def test_agent_missing_field_budget(monkeypatch, required, expected):
    mocked = AsyncMock(side_effect=lambda *a, **k: {"field": {"value": None}})
    monkeypatch.setattr(extraction_agent, "_run_agent_extraction_impl", mocked)
    monkeypatch.setattr(extraction_agent, "resolve_llm_model", lambda p: SimpleNamespace(name=lambda: "test"))
    await extraction_agent.run_agent_extraction("source", {"field": {"required": required}}, "rtk",
        {"models": {"llm_extraction": {"provider": "router_ai", "model": "test"}}})
    assert mocked.await_count == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("required,expected", [(True, 4), (False, 1)])
async def test_court_empty_field_budget(monkeypatch, required, expected):
    calls = []
    def provider(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(parsed={"case_number": {"value": None, "confidence": .9,
            "evidence": "", "reasoning": "Проверен весь текст; номер не указан.", "ambiguous": False}}, winner_model="test")
    monkeypatch.setattr(court, "call_llm_json_with_fallback", provider)
    monkeypatch.setattr(court, "get_field_instruction", lambda *a, **k: "")
    fields = await court.extract_court_llm_fields("source", {"case_number": {"required": required}},
        {"models": {"llm_extraction": {"provider": "router_ai", "model": "test"}}})
    assert len(calls) == expected
    assert fields["case_number"]["value"] is None
    assert bool(fields["case_number"].get("validation_issue")) is required


def test_exhausted_required_field_cannot_be_autoaccepted_by_high_quality():
    profile = {"fields": {"creditor_inn": {"required": True}, "optional": {"required": False}}}
    fields = {"creditor_inn": {"value": None, "empty_field_retries": 3},
              "optional": {"value": None, "empty_field_retries": 3}}
    issues = profile_field_issues(fields, "rtk", profile)
    assert [i.field_name for i in issues] == ["creditor_inn"]
    assert review_requirement(1.0, issues) == (True, "required_field_retry_exhausted")

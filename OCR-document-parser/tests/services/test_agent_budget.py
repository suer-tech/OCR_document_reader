import pytest
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.usage import UsageLimits

from ocr_platform.services.agent_limits import BoundedAgent


@pytest.mark.asyncio
@pytest.mark.parametrize("requested,expected", [(None, 3), (50, 3), (1, 1)])
async def test_repeating_tool_gets_one_final_request_without_tools(requested, expected):
    calls, lookups = [], []

    def respond(messages, info):
        calls.append(messages)
        if not info.function_tools:
            assert not info.result_tools
            return ModelResponse(parts=[TextPart("Use the found INN: 1234567890")])
        return ModelResponse(parts=[ToolCallPart.from_raw_args("lookup", {"name": "same"})])

    agent = BoundedAgent(FunctionModel(respond))

    @agent.tool_plain
    def lookup(name: str) -> str:
        lookups.append(name)
        return "Found INN: 1234567890"

    limits = UsageLimits(request_limit=requested)
    if expected < 3:
        with pytest.raises(UsageLimitExceeded):
            await agent.run("extract", usage_limits=limits)
    else:
        result = await agent.run("extract", usage_limits=limits)
        assert result.data == "Use the found INN: 1234567890"
        assert result.usage().requests == 4
        assert any("1234567890" in str(message) for message in calls[-1])
    assert len(calls) == expected + (1 if expected == 3 else 0)
    assert len(lookups) == expected
    assert limits.request_limit == requested  # Do not mutate caller settings.


@pytest.mark.asyncio
async def test_http_finalization_preserves_evidence_and_sends_no_tools():
    import json
    import httpx
    from openai import AsyncOpenAI
    from pydantic import BaseModel
    from ocr_platform.services.extraction_agent import ReasoningEffortOpenAIModel

    class Answer(BaseModel):
        inn: str

    sent, lookups = [], []

    def respond(request):
        payload = json.loads(request.content)
        sent.append(payload)
        if len(sent) <= 3:
            message = {"role": "assistant", "content": None, "tool_calls": [{
                "id": f"call-{len(sent)}", "type": "function", "function": {
                    "name": "lookup", "arguments": '{"name":"known company"}',
                },
            }]}
        else:
            assert "tools" not in payload and "tool_choice" not in payload
            assert "original document" in str(payload["messages"])
            assert "Found INN: 1234567890" in str(payload["messages"])
            assert all("tool_calls" not in m and m["role"] != "tool" for m in payload["messages"])
            assert "do not search" in payload["messages"][-1]["content"]
            message = {"role": "assistant", "content": '{"inn":"1234567890"}'}
        return httpx.Response(200, json={
            "id": "canary", "object": "chat.completion", "created": 1,
            "model": "anthropic/claude-sonnet-5.5",
            "choices": [{"index": 0, "finish_reason": "stop", "message": message}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        })

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        client = AsyncOpenAI(api_key="test", base_url="https://router.invalid/v1", http_client=http)
        model = ReasoningEffortOpenAIModel("anthropic/claude-sonnet-5.5", openai_client=client)
        agent = BoundedAgent(model, result_type=Answer)

        @agent.tool_plain
        def lookup(name: str) -> str:
            lookups.append(name)
            return "Found INN: 1234567890"

        result = await agent.run("original document")
    assert result.data.inn == "1234567890"
    assert len(sent) == 4 and len(lookups) == 3


@pytest.mark.asyncio
async def test_invalid_final_answer_does_not_restart_search():
    from pydantic import BaseModel

    class Answer(BaseModel):
        inn: str

    calls = []

    def respond(messages, info):
        calls.append(1)
        if info.function_tools:
            return ModelResponse(parts=[ToolCallPart.from_raw_args("lookup", {})])
        return ModelResponse(parts=[TextPart("invalid JSON")])

    agent = BoundedAgent(FunctionModel(respond), result_type=Answer)

    @agent.tool_plain
    def lookup() -> str:
        return "Found INN: 1234567890"

    with pytest.raises(UsageLimitExceeded, match="Final answer without tools"):
        await agent.run("extract")
    assert len(calls) == 4


@pytest.mark.asyncio
async def test_tool_result_can_complete_normally_within_budget():
    calls = []

    def respond(messages, info):
        calls.append(messages)
        if len(calls) == 1:
            return ModelResponse(parts=[ToolCallPart.from_raw_args("lookup", {})])
        return ModelResponse(parts=[TextPart("found")])

    agent = BoundedAgent(FunctionModel(respond))

    @agent.tool_plain
    def lookup() -> str:
        return "Found INN: 1234567890"

    assert (await agent.run("extract")).data == "found"
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_combined_extraction_does_not_restart_exhausted_agent(monkeypatch):
    from ocr_platform.services import extraction_agent as extraction

    calls = []

    async def exhausted(*args, **kwargs):
        calls.append(1)
        raise UsageLimitExceeded("request_limit of 3")

    monkeypatch.setattr(extraction, "resolve_llm_model", lambda config: FunctionModel(lambda m, i: None))
    monkeypatch.setattr(extraction, "get_field_instruction", lambda *a, **kw: "extract")
    monkeypatch.setattr(extraction.agent_court_decision_combined, "run", exhausted)
    with pytest.raises(UsageLimitExceeded):
        await extraction.run_agent_extraction(
            "text", {"debtor_full_name": {"extraction_method": "llm"}},
            profile_id="court_decision_ru", profile_config={},
        )
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_claude_adapter_sends_supported_tool_choice_and_accepts_result():
    import json
    import httpx
    from openai import AsyncOpenAI
    from pydantic import BaseModel
    from ocr_platform.services.extraction_agent import ReasoningEffortOpenAIModel

    class Answer(BaseModel):
        ok: bool

    sent = []

    def respond(request):
        payload = json.loads(request.content)
        sent.append(payload)
        return httpx.Response(200, json={
            "id": "canary", "object": "chat.completion", "created": 1,
            "model": "anthropic/claude-sonnet-5.5",
            "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
                "role": "assistant", "content": None, "tool_calls": [{
                    "id": "call", "type": "function", "function": {
                        "name": "final_result", "arguments": '{"ok":true}',
                    },
                }],
            }}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        })

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        client = AsyncOpenAI(api_key="test", base_url="https://router.invalid/v1", http_client=http)
        model = ReasoningEffortOpenAIModel(
            "anthropic/claude-sonnet-5.5", reasoning_effort="low", openai_client=client,
        )
        result = await BoundedAgent(model, result_type=Answer).run("confirm")
    assert result.data.ok is True
    assert len(sent) == 1
    assert sent[0]["tool_choice"] == "auto"
    assert sent[0]["reasoning_effort"] == "low"
    assert "parallel_tool_calls" not in sent[0]

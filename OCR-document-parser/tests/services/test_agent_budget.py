import pytest
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.usage import UsageLimits

from ocr_platform.services.agent_limits import BoundedAgent


@pytest.mark.asyncio
@pytest.mark.parametrize("requested,expected", [(None, 3), (50, 3), (1, 1)])
async def test_repeating_tool_stops_before_fourth_paid_request(requested, expected):
    calls, lookups = [], []

    def respond(messages, info):
        calls.append(messages)
        return ModelResponse(parts=[ToolCallPart.from_raw_args("lookup", {"name": "same"})])

    agent = BoundedAgent(FunctionModel(respond))

    @agent.tool_plain
    def lookup(name: str) -> str:
        lookups.append(name)
        return "Found INN: 1234567890"

    limits = UsageLimits(request_limit=requested)
    with pytest.raises(UsageLimitExceeded):
        await agent.run("extract", usage_limits=limits)
    assert len(calls) == expected
    assert len(lookups) == expected
    assert limits.request_limit == requested  # Do not mutate caller settings.


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

import json
from pathlib import Path

import pytest
from pydantic import BaseModel, TypeAdapter, ValidationError
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.messages import ModelResponse, RetryPromptPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from ocr_platform.services import extraction_agent as extraction


def confidence_fields():
    models = {
        model for model in vars(extraction).values()
        if isinstance(model, type) and issubclass(model, BaseModel)
    }
    return [
        (model, name, info)
        for model in models
        for name, info in model.model_fields.items()
        if name == "confidence" or name.endswith("_confidence")
    ]


@pytest.mark.parametrize("value", [-0.01, 1.5, 80, 95, 100, float("nan"), float("inf"), float("-inf")])
def test_every_extraction_confidence_rejects_out_of_range(value):
    for model, name, info in confidence_fields():
        with pytest.raises(ValidationError):
            TypeAdapter(info.rebuild_annotation()).validate_python(value)


@pytest.mark.parametrize("value", [0.0, 0.5, 0.95, 1.0])
def test_every_extraction_confidence_preserves_valid_values(value):
    for model, name, info in confidence_fields():
        assert TypeAdapter(info.rebuild_annotation()).validate_python(value) == value, (model, name)
        prop = model.model_json_schema()["properties"][name]
        assert prop["minimum"] == 0 and prop["maximum"] == 1, (model, name)


def test_saved_json_schemas_have_confidence_bounds():
    checked = 0

    def check(schema):
        nonlocal checked
        if isinstance(schema, dict):
            for name, prop in schema.get("properties", {}).items():
                if name == "confidence" or name.endswith("_confidence"):
                    assert prop.get("minimum") == 0 and prop.get("maximum") == 1, name
                    checked += 1
            for child in schema.values():
                check(child)
        elif isinstance(schema, list):
            for child in schema:
                check(child)

    root = Path(extraction.__file__).parents[1] / "config/pipelines/schemas"
    for path in root.rglob("*.json"):
        # json.loads(bytes) also supports the existing UTF-16 court schema.
        check(json.loads(path.read_bytes()))
    assert checked > 50


def sample_result(schema, confidence):
    """Produce a result with nullable business values and controlled confidence."""
    if "anyOf" in schema:
        if any(option.get("type") == "null" for option in schema["anyOf"]):
            return None
        return sample_result(schema["anyOf"][0], confidence)
    kind = schema.get("type")
    if kind == "object":
        return {
            name: confidence if name == "confidence" or name.endswith("_confidence")
            else sample_result(prop, confidence)
            for name, prop in schema["properties"].items()
        }
    if kind == "boolean":
        return False
    if kind == "array":
        return []
    if kind in ("integer", "number"):
        return 1
    return "test"


@pytest.mark.asyncio
@pytest.mark.parametrize("agent_name", [
    "agent_generic", "agent_claims_amount", "agent_rtk_combined", "agent_rtk_tax_combined",
    "agent_rtk2_combined", "agent_rtk3_combined", "agent_rtk3_claims",
    "agent_court_decision_combined", "agent_passport_main_combined", "agent_passport_registration_combined",
])
async def test_real_agent_requests_correction_before_accepting_result(agent_name):
    calls = []

    def respond(messages, info):
        tool = info.result_tools[0]
        retries = [part for msg in messages for part in msg.parts if isinstance(part, RetryPromptPart)]
        calls.append(retries)
        if len(calls) == 2:
            assert retries
            assert any(error["type"] == "less_than_equal" for error in retries[-1].content)
        payload = sample_result(tool.parameters_json_schema, 95 if len(calls) == 1 else 0.95)
        return ModelResponse(parts=[ToolCallPart.from_raw_args(tool.name, payload)])

    agent = getattr(extraction, agent_name)
    with agent.override(model=FunctionModel(respond)):
        result = await agent.run("Extract fields", deps="document text")
    assert len(calls) == 2
    values = result.data.model_dump()
    assert all(value == 0.95 for name, value in values.items()
               if name == "confidence" or name.endswith("_confidence"))


@pytest.mark.asyncio
async def test_repeated_invalid_confidence_exhausts_retries():
    calls = 0

    def respond(messages, info):
        nonlocal calls
        calls += 1
        tool = info.result_tools[0]
        return ModelResponse(parts=[ToolCallPart.from_raw_args(
            tool.name, {"value": "found", "confidence": 95, "reasoning": "test"},
        )])

    with extraction.agent_generic.override(model=FunctionModel(respond)):
        with pytest.raises(UnexpectedModelBehavior):
            await extraction.agent_generic.run("Extract field", deps="document text")
    assert calls == 4  # Initial answer plus the agent's three correction attempts.

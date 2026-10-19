"""Bound tool turns, then finalize once from the existing evidence without tools."""
import json
from dataclasses import replace

from pydantic import TypeAdapter
from pydantic_ai import Agent, RunContext, capture_run_messages
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.messages import (
    RetryPromptPart, TextPart, ToolCallPart,
    ToolReturnPart, UserPromptPart,
)
from pydantic_ai.settings import merge_model_settings
from pydantic_ai.usage import Usage, UsageLimits

from ocr_platform.observability.logging import get_logger

MAX_AGENT_REQUESTS = 3
logger = get_logger(__name__)


def history_without_tools(messages):
    """Retain evidence as text, without historical tool-use protocol blocks.

    Anthropic requires tool definitions for tool-use blocks. Converting these
    blocks to ordinary text permits a request with literally no tools defined.
    """
    history = []
    for message in messages:
        parts = []
        for part in message.parts:
            if isinstance(part, ToolCallPart):
                parts.append(TextPart(
                    f"Previous tool call {part.tool_call_id}: {part.tool_name}; arguments: {part.args_as_json_str()}"
                ))
            elif isinstance(part, ToolReturnPart):
                parts.append(UserPromptPart(
                    f"Recorded tool result {part.tool_call_id}: {part.tool_name}: {part.model_response_str()}"
                ))
            elif isinstance(part, RetryPromptPart):
                parts.append(UserPromptPart(f"Previous validation feedback: {part.model_response()}"))
            else:
                parts.append(part)
        history.append(replace(message, parts=parts))
    return history


class BoundedAgent(Agent):
    def __init__(self, *args, result_type=str, **kwargs):
        super().__init__(*args, result_type=result_type, **kwargs)
        self._budget_result_type = result_type

    async def run(self, *args, usage_limits=None, **kwargs):
        limits = usage_limits or UsageLimits()
        limit = limits.request_limit
        limits = replace(
            limits,
            request_limit=min(limit, MAX_AGENT_REQUESTS) if limit is not None else MAX_AGENT_REQUESTS,
        )
        usage = kwargs.get("usage") or Usage()
        kwargs["usage"] = usage
        with capture_run_messages() as messages:
            try:
                return await super().run(*args, usage_limits=limits, **kwargs)
            except UsageLimitExceeded:
                # Preserve explicit stricter limits and all token budgets. Only
                # request-budget exhaustion after actual tool results is recoverable.
                limits.check_tokens(usage)
                has_results = any(
                    isinstance(part, ToolReturnPart)
                    for message in messages for part in message.parts
                )
                if limits.request_limit != MAX_AGENT_REQUESTS or usage.requests != MAX_AGENT_REQUESTS or not has_results:
                    raise
                return await self._finalize_without_tools(args, kwargs, messages, limits, usage)

    async def _finalize_without_tools(self, args, kwargs, messages, limits, usage):
        result_type = kwargs.get("result_type") or self._budget_result_type
        adapter = TypeAdapter(result_type)
        prompt = (
            "Tool/search budget is exhausted. Continue this same task using the document and "
            "the recorded tool results above. Tool results are already available in the history: "
            "if an INN was found, use it for the corresponding person or organization; do not search "
            "for it again. Do not confuse debtor, creditor, financial manager and SRO tax IDs. "
            "If a required value is still unavailable, use null where the schema permits it; "
            "do not invent it. No tools are available. Give the final answer now. "
        )
        if result_type is not str:
            prompt += "Return ONLY raw JSON conforming to this schema: " + json.dumps(adapter.json_schema(), ensure_ascii=False)
        model = await self._get_model(kwargs.get("model"))
        # A plain-text result avoids PydanticAI's final_result tool as well.
        final_agent = Agent(model, result_type=str, retries=0, name="extraction_final_answer")
        logger.warning("extraction_finalizing_without_tools", agent_name=self.name, requests=usage.requests)
        try:
            final = await final_agent.run(
                prompt,
                message_history=history_without_tools(messages),
                usage=usage,
                usage_limits=replace(limits, request_limit=usage.requests + 1),
                model_settings=merge_model_settings(self.model_settings, kwargs.get("model_settings")),
            )
            data = final.data if result_type is str else adapter.validate_json(final.data)
            context = RunContext(
                self._get_deps(kwargs.get("deps")), model, usage,
                args[0] if args else kwargs["user_prompt"], messages=final.all_messages(),
            )
            data = await self._validate_result(data, context, None)
            return replace(final, data=data)
        except Exception as exc:
            # Never restart the tool loop if even this single final answer is invalid.
            raise UsageLimitExceeded(f"Final answer without tools could not be validated: {exc}") from exc

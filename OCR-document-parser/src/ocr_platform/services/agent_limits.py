"""Hard request budget for one extraction operation (including tool turns)."""
from dataclasses import replace

from pydantic_ai import Agent
from pydantic_ai.usage import UsageLimits

MAX_AGENT_REQUESTS = 3


class BoundedAgent(Agent):
    async def run(self, *args, usage_limits=None, **kwargs):
        limits = usage_limits or UsageLimits()
        limit = limits.request_limit
        limits = replace(
            limits,
            request_limit=min(limit, MAX_AGENT_REQUESTS) if limit is not None else MAX_AGENT_REQUESTS,
        )
        return await super().run(*args, usage_limits=limits, **kwargs)

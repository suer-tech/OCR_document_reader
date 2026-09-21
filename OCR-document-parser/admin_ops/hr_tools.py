"""Bounded client for the HR VPS operations gateway."""
from __future__ import annotations

import json
from typing import Literal
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, ConfigDict, Field

from admin_ops.config import OpsSettings


class Arguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HrDays(Arguments):
    days: int = Field(default=8, ge=1, le=31, strict=True)
    same_time: bool = True


class HrMetricHistory(Arguments):
    metric: Literal[
        "terminal_runs_24h", "failed_runs_1h", "oldest_active_seconds", "queue_ready",
        "consumer_count", "host_cpu_percent", "host_memory_percent", "disk_free_percent",
        "web_up", "processing_ready",
        "request_rate", "active_connections",
    ]
    hours: int = Field(default=24, ge=1, le=720, strict=True)


class HrLogEvents(Arguments):
    hours: int = Field(default=24, ge=1, le=336, strict=True)
    level: Literal["all", "debug", "info", "warning", "error", "critical"] = "error"
    limit: int = Field(default=30, ge=1, le=100, strict=True)


HR_TOOL_MODELS = {
    "hr_days": HrDays,
    "hr_metric_history": HrMetricHistory,
    "hr_log_events": HrLogEvents,
    "hr_overview": Arguments,
}


def configured(settings: OpsSettings) -> bool:
    parsed = urlparse(settings.hr_base_url)
    return parsed.scheme == "https" and bool(parsed.netloc) and parsed.path.rstrip("/").endswith("/api/ops") \
        and not parsed.username and not parsed.password and len(settings.hr_read_token) >= 32


async def call_hr(settings: OpsSettings, name: str, arguments: BaseModel) -> dict:
    if not configured(settings):
        return {"status": "unavailable", "system": "hr", "reason": "not_configured"}
    routes = {
        "hr_days": ("v1/days", {"days": arguments.days, "same_time": str(arguments.same_time).lower()}),
        "hr_metric_history": ("v1/metrics", {"metric": arguments.metric, "hours": arguments.hours}),
        "hr_log_events": ("v1/logs", {"hours": arguments.hours, "level": arguments.level, "limit": arguments.limit}),
        "hr_overview": ("v1/overview", {}),
    }
    route, params = routes[name]
    url = f"{settings.hr_base_url.rstrip('/')}/{route}"
    try:
        async with httpx.AsyncClient(timeout=15, trust_env=False, follow_redirects=False) as client:
            async with client.stream("GET", url, params=params,
                                     headers={"Authorization": f"Bearer {settings.hr_read_token}"}) as response:
                response.raise_for_status()
                payload = bytearray()
                async for chunk in response.aiter_bytes():
                    payload.extend(chunk)
                    if len(payload) > 1_000_000:
                        raise ValueError("result too large")
        result = json.loads(payload)
        if not isinstance(result, dict) or result.get("system") != "hr":
            raise ValueError("invalid HR response")
        return result
    except Exception as exc:  # response text and URL can contain private details
        return {"status": "unavailable", "system": "hr", "reason": type(exc).__name__}


async def fetch_hr_alerts(settings: OpsSettings) -> dict:
    """Internal bot probe. It shares the same fixed-origin and bounded-response policy."""
    if not configured(settings):
        return {"status": "unavailable", "system": "hr", "reason": "not_configured"}
    url = f"{settings.hr_base_url.rstrip('/')}/v1/alerts"
    try:
        async with httpx.AsyncClient(timeout=15, trust_env=False, follow_redirects=False) as client:
            response = await client.get(url, headers={"Authorization": f"Bearer {settings.hr_read_token}"})
            response.raise_for_status()
            if len(response.content) > 200_000:
                raise ValueError("result too large")
        result = response.json()
        if not isinstance(result, dict) or result.get("system") != "hr" or not isinstance(result.get("alerts"), list):
            raise ValueError("invalid HR response")
        return result
    except Exception as exc:
        return {"status": "unavailable", "system": "hr", "reason": type(exc).__name__}

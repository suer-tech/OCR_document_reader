"""Private stdio MCP adapter; capability is bound to one ongoing Pulse question."""
import logging
import os

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

mcp = FastMCP("ocr_read")
READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)


async def call(name: str, arguments: dict) -> dict:
    try:
        async with httpx.AsyncClient(timeout=40, trust_env=False) as client:
            response = await client.post(
                "http://127.0.0.1:8080/pulse/read/" + os.environ["PULSE_READ_JOB"],
                headers={"X-Read-Token": os.environ["PULSE_READ_TOKEN"]},
                json={"name": name, "arguments": arguments},
            )
            response.raise_for_status()
            return response.json()
    except Exception as exc:
        return {"status": "unavailable", "reason": type(exc).__name__}


@mcp.tool(annotations=READ_ONLY)
async def document_days(days: int = 8, end_date: str | None = None, same_time: bool = True) -> dict:
    """Read daily document/run totals, types, errors, retries and successful-run duration. Up to 31 days. end_date is YYYY-MM-DD or today. same_time=True compares each day only up to current local clock time (use for 'is today busy?'); False returns full past days and today's partial day. Includes numeric comparison and coverage caveats."""
    return await call("document_days", {"days": days, "end_date": end_date, "same_time": same_time})


@mcp.tool(annotations=READ_ONLY)
async def metric_history(metric: str, hours: int = 24) -> dict:
    """Read sampled Prometheus history up to 720 hours. metric: request_rate, http_errors, latency_p95, queue_ready, api_up, worker_up. These are HTTP/scrape metrics, not document counts; gaps are unknown."""
    return await call("metric_history", {"metric": metric, "hours": hours})


@mcp.tool(annotations=READ_ONLY)
async def log_events(hours: int = 24, level: str = "error", limit: int = 30) -> dict:
    """Read safe structured OCR application events from Loki, up to 336 hours and 100 entries. level: error, warning, info, all. No raw messages, stack traces, personal data, document IDs or secrets. Empty results do not prove no incidents."""
    return await call("log_events", {"hours": hours, "level": level, "limit": limit})


@mcp.tool(annotations=READ_ONLY)
async def system_overview() -> dict:
    """Read architecture, timezone, available sources and tool limitations (not live health)."""
    return await call("system_overview", {})


if __name__ == "__main__":
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    mcp.run(transport="stdio")

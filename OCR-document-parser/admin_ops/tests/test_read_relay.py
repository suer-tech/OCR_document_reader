import asyncio
import json

import httpx
import pytest

from admin_ops import ai, bot as bot_module
from admin_ops.config import OpsSettings
from admin_ops.read_relay import ReadJob, ReadJobs


def test_relay_round_trip_and_call_limits():
    async def run():
        job = ReadJob()
        task = asyncio.create_task(job.ask("system_overview", {}))
        call = await job.calls.get()
        assert not job.deliver("wrong", {"status": "ok"})
        with pytest.raises(ValueError):
            job.deliver(call["call_id"], {"value": float("nan")})
        assert job.deliver(call["call_id"], {"status": "ok"})
        assert await task == {"status": "ok"}
        assert not job.waiting
        job.count = 12
        assert (await job.ask("system_overview", {}))["reason"] == "tool_budget_exceeded"
    asyncio.run(run())


def test_bot_sdk_mcp_reverse_relay_without_network_or_model(monkeypatch):
    async def run():
        settings = OpsSettings(role="pulse", internal_token="x" * 32, telegram_token="dummy")
        monkeypatch.setattr(ai, "get_ops_settings", lambda: settings)
        jobs = ReadJobs()
        monkeypatch.setattr(ai, "read_jobs", jobs)
        seen = []

        async def answer(request, job_id=None, token=None):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=ai.app), base_url="http://test") as client:
                denied = await client.post(f"/pulse/read/{job_id}", json={"name": "system_overview"})
                assert denied.status_code == 403
                response = await client.post(f"/pulse/read/{job_id}", headers={"X-Read-Token": token},
                                             json={"name": "system_overview"})
                seen.append(response.json())
            return {"answer": "Проверено"}

        monkeypatch.setattr(ai, "answer_pulse", answer)
        bot = bot_module.AdminBot(settings)
        await bot.client.aclose()
        bot.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=ai.app))
        try:
            response = await bot.worker(settings.pulse_url + "/pulse", {"question": "Что доступно?", "snapshot": {}})
            assert response == {"answer": "Проверено"}
            assert seen[0]["status"] == "ok"
            assert "system_overview" in seen[0]["tools"]
            assert not jobs.jobs  # Removed by bot's finally block.
        finally:
            await bot.aclose()
    asyncio.run(run())


def test_pulse_sdk_configuration_registers_read_only_mcp(monkeypatch):
    seen = {}

    async def run_codex(**kwargs):
        seen.update(kwargs)
        return "ok"
    monkeypatch.setattr(ai, "run_codex", run_codex)
    result = asyncio.run(ai.answer_pulse(ai.PulseRequest(question="Сравни дни", snapshot={}), "a" * 32, "test-token"))
    assert result == {"answer": "ok"}
    assert seen["sandbox"] == "read_only"
    config = seen["config"]
    assert config["features.shell_tool"] is False
    assert config["web_search"] == "disabled"
    assert config["mcp_servers.ocr_read"]["required"] is True
    assert set(config["mcp_servers.ocr_read"]["enabled_tools"]) == {"document_days", "metric_history", "log_events", "system_overview"}
    assert "test-token" not in seen["prompt"]
    assert "same_time=true" in seen["instructions"]


def test_initial_database_failure_does_not_block_other_sources(monkeypatch):
    def database(*args):
        raise RuntimeError("secret credentials")

    async def metrics(*args):
        return {"api_up": 1.0}

    monkeypatch.setattr(bot_module, "load_daily_report", database)
    monkeypatch.setattr(bot_module, "prometheus_snapshot", metrics)

    async def run():
        bot = bot_module.AdminBot(OpsSettings())
        try:
            snapshot = await bot.snapshot()
            assert snapshot["daily"] == {"status": "unavailable", "reason": "RuntimeError"}
            assert snapshot["metrics"]["api_up"] == 1
            assert "secret" not in json.dumps(snapshot)
        finally:
            await bot.aclose()
    asyncio.run(run())

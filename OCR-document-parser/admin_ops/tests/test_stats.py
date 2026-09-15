import asyncio
import json
from datetime import datetime, timezone

import httpx
import pytest

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from admin_ops import stats
from admin_ops.bot import format_health
from admin_ops.stats import daily_report
from ocr_platform.storage.models import Base, Document, PipelineRun


def utc_naive(*parts: int) -> datetime:
    # Production ORM stores naive UTC timestamps.
    return datetime(*parts, tzinfo=timezone.utc).replace(tzinfo=None)


def test_daily_report_uses_local_day_and_no_document_contents() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all(
            [
                Document(id="doc-1", source_type="api", document_type="court_decision_ru"),
                Document(id="doc-2", source_type="api", document_type="court_decision_ru"),
                Document(id="doc-3", source_type="api", document_type="passport_main"),
            ]
        )
        session.add_all(
            [
                PipelineRun(id="run-1", document_id="doc-1", profile_id="court", status="done",
                            created_at=utc_naive(2026, 9, 14, 20), started_at=utc_naive(2026, 9, 14, 20),
                            finished_at=utc_naive(2026, 9, 14, 20, 1), retry_count=1),
                PipelineRun(id="run-2", document_id="doc-2", profile_id="court", status="failed",
                            created_at=utc_naive(2026, 9, 15, 1), started_at=utc_naive(2026, 9, 15, 1),
                            finished_at=utc_naive(2026, 9, 15, 1, 5), retry_count=2,
                            last_error="sensitive error text"),
                PipelineRun(id="run-3", document_id="doc-3", profile_id="passport", status="processing",
                            created_at=utc_naive(2026, 9, 13, 10), retry_count=0),
            ]
        )
        session.commit()
        result = daily_report(
            session, now=datetime(2026, 9, 15, 12, tzinfo=timezone.utc),
            timezone_name="Asia/Yekaterinburg",
        )
    assert result["date"] == "2026-09-15"
    assert result["totals"] == {
        "processed": 2, "documents_unique": 2, "errors": 1,
        "retries": 3, "in_progress": 1,
    }
    assert result["by_type"]["court_decision_ru"]["avg_seconds"] == 60.0
    assert "sensitive error text" not in str(result)


@pytest.mark.parametrize("raw, expected", [
    ("NaN", None), ("+Inf", None), ("-Inf", None), ("1e999", None),
    ("invalid", None), (None, None), ("0", 0.0), ("1.25", 1.25),
])
@pytest.mark.parametrize("include_history", [True, False])
def test_prometheus_snapshot_is_strict_json_safe(monkeypatch, raw, expected, include_history):
    original_client = httpx.AsyncClient

    def respond(request):
        return httpx.Response(200, json={
            "status": "success",
            "data": {"result": [{"value": [123, raw]}]},
        })

    monkeypatch.setattr(stats.httpx, "AsyncClient", lambda **kwargs: original_client(
        transport=httpx.MockTransport(respond), trust_env=False, **kwargs,
    ))
    snapshot = asyncio.run(stats.prometheus_snapshot("http://prometheus:9090", include_history=include_history))
    assert len(snapshot) == (8 if include_history else 6)
    assert all(value == expected for value in snapshot.values())
    json.dumps(snapshot, allow_nan=False)
    # Exercise the exact encoder used by AdminBot.worker before any network call.
    httpx.Request("POST", "http://awg-gateway:8080/pulse", json={"snapshot": snapshot})
    if expected is None:
        assert "HTTP p95 за 5 мин: нет данных" in format_health(snapshot)


def test_pulse_command_sends_snapshot_with_missing_p95(monkeypatch):
    from admin_ops import bot as bot_module
    from admin_ops.config import OpsSettings

    original_client = httpx.AsyncClient
    forwarded = []

    def respond(request):
        if request.url.host == "prometheus":
            value = "NaN" if "histogram_quantile" in request.url.params["query"] else "1"
            return httpx.Response(200, json={"data": {"result": [{"value": [123, value]}]}})
        if request.url.path == "/pulse":
            forwarded.append(json.loads(request.content))
            return httpx.Response(200, json={"answer": "ok"})
        assert request.url.host == "api.telegram.org"
        return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr(stats.httpx, "AsyncClient", lambda **kwargs: original_client(
        transport=httpx.MockTransport(respond), trust_env=False,
    ))
    monkeypatch.setattr(bot_module, "load_daily_report", lambda *args: {"date": "2026-09-15"})
    bot = bot_module.AdminBot(OpsSettings(telegram_token="dummy", admin_ids="123"))

    async def run():
        try:
            await bot.handle({"message": {
                "from": {"id": 123}, "chat": {"id": 123, "type": "private"},
                "text": "/pulse Сколько документов сегодня?",
            }})
            await asyncio.gather(*bot.dialog_tasks.values())
        finally:
            await bot.aclose()

    asyncio.run(run())
    assert len(forwarded) == 1
    assert forwarded[0]["snapshot"]["metrics"]["http_p95_seconds_5m"] is None
    assert forwarded[0]["snapshot"]["metrics"]["api_up"] == 1.0
    assert bot.history[123][-1]["answer"] == "ok"

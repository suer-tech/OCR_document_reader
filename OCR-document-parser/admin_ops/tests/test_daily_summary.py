import asyncio
import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from admin_ops import daily_summary as summary
from admin_ops.bot import AdminBot
from admin_ops.config import OpsSettings
from ocr_platform.storage.models import Base, Document, PipelineRun

ZONE = ZoneInfo("Asia/Yekaterinburg")


def local(day, hour=21, minute=0):
    return datetime(2026, 10, day, hour, minute, tzinfo=ZONE)


def utc(day, hour, minute=0):
    return local(day, hour, minute).astimezone(timezone.utc).replace(tzinfo=None)


def fixture_database(path):
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        s.add_all([Document(id="a", source_type="api", document_type="rtk2"),
                   Document(id="b", source_type="api", document_type="PRIVATE TYPE NAME"),
                   Document(id="c", source_type="api", document_type="rtk3")])
        s.add_all([
            PipelineRun(id="1", document_id="a", profile_id="rtk2", status="done",
                        created_at=utc(13, 23), started_at=utc(14, 0), finished_at=utc(14, 0, 1)),
            PipelineRun(id="2", document_id="a", profile_id="rtk2", status="done",
                        created_at=utc(14, 20), started_at=utc(14, 20), finished_at=utc(14, 20, 3), retry_count=2),
            PipelineRun(id="3", document_id="b", profile_id="PRIVATE PROFILE NAME", status="failed",
                        created_at=utc(14, 20), started_at=utc(14, 20), finished_at=utc(14, 20, 5),
                        last_error="PRIVATE DOCUMENT ERROR", retry_count=3),
            PipelineRun(id="4", document_id="c", profile_id="rtk3", status="done",
                        created_at=utc(14, 20), started_at=utc(14, 20), finished_at=utc(14, 21)),
            PipelineRun(id="5", document_id="c", profile_id="rtk3", status="processing",
                        created_at=utc(13, 22)),
        ])
        s.commit()
    engine.dispose()
    return f"sqlite:///{path}"


def test_summary_counts_completion_window_unique_documents_and_real_durations(tmp_path):
    url = fixture_database(tmp_path / "data.db")
    report = summary.load_summary(url, local(14, 0), local(14))
    assert (report["documents"], report["runs"], report["successes"], report["errors"], report["retries"]) == (2, 3, 2, 1, 5)
    assert (report["avg_seconds"], report["median_seconds"], report["p95_seconds"]) == (120, 120, 180)
    assert report["in_progress"] == 1
    assert report["by_type"]["РТК-2"]["runs"] == 2
    text = summary.format_summary(report, {}, local(14, 0), local(14), local(14))
    assert "00:00–21:00" in text
    assert "Документов: 2" in text and "завершённых запусков: 3" in text
    assert "PRIVATE" not in text and "PRIVATE" not in json.dumps(report)
    assert "API нет данных" in text and "RabbitMQ нет данных" in text


def test_empty_and_unavailable_data_are_distinct(tmp_path):
    url = fixture_database(tmp_path / "data.db")
    report = summary.load_summary(url, local(12, 0), local(12))
    assert report["runs"] == 0 and report["avg_seconds"] is None
    text = summary.format_summary(None, {}, local(14, 0), local(14), local(14))
    assert "недоступна" in text and "Документов: 0" not in text


def test_midnight_cutoff_covers_previous_full_day():
    assert summary.report_window(local(15, 0)) == (local(14, 0), local(15, 0))


def test_missing_duration_is_not_replaced_with_zero(tmp_path):
    url = fixture_database(tmp_path / "data.db")
    engine = create_engine(url)
    with Session(engine) as session:
        session.add(PipelineRun(id="6", document_id="a", profile_id="rtk2", status="done",
                                created_at=utc(14, 20), finished_at=utc(14, 20, 10)))
        session.commit()
    engine.dispose()
    report = summary.load_summary(url, local(14, 0), local(14))
    assert report["successes"] == 3 and report["timed_runs"] == 2
    assert report["avg_seconds"] == 120
    assert "для 2 из 3" in summary.format_summary(report, {}, local(14, 0), local(14), local(14))


def test_delivery_uses_telegram_boundary_and_survives_restart_and_partial_failure(monkeypatch, tmp_path):
    url = fixture_database(tmp_path / "data.db")
    settings = OpsSettings(database_url=url, state_path=str(tmp_path / "state.db"),
                           admin_ids="123,456", telegram_token="synthetic", daily_summary_hour=21)
    requests = []
    original_client = httpx.AsyncClient
    failures = {456: 1}

    def respond(request):
        if request.url.host == "prometheus":
            return httpx.Response(200, json={"data": {"result": [{"value": [0, "1"]}]}})
        assert request.url.host == "api.telegram.org" and request.url.path.endswith("/sendMessage")
        payload = json.loads(request.content)
        requests.append(payload)
        if failures.get(payload["chat_id"], 0):
            failures[payload["chat_id"]] -= 1
            return httpx.Response(503, json={"ok": False})
        return httpx.Response(200, json={"ok": True})

    def client(**kwargs):
        kwargs.pop("proxy", None)
        kwargs["trust_env"] = False
        return original_client(transport=httpx.MockTransport(respond), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client)

    async def run():
        bot = AdminBot(settings)
        service = summary.DailySummaries(settings, bot.send)
        assert await service.tick(local(14, 20, 59)) == 0
        assert not requests
        assert await service.tick(local(14)) == 1
        assert len(requests) == 2
        await bot.aclose()
        restarted = AdminBot(settings)
        try:
            assert await summary.DailySummaries(settings, restarted.send).tick(local(15, 8)) == 1
            assert await summary.DailySummaries(settings, restarted.send).tick(local(15, 9)) == 0
        finally:
            await restarted.aclose()

    asyncio.run(run())
    assert [r["chat_id"] for r in requests] == [123, 456, 456]
    assert requests[1]["text"] == requests[2]["text"]
    assert "Документов: 2" in requests[0]["text"] and "PRIVATE" not in str(requests)


def test_restart_catches_latest_due_summary_without_initial_backlog(monkeypatch, tmp_path):
    settings = OpsSettings(state_path=str(tmp_path / "state.db"), admin_ids="123")
    sent = []

    async def build(*args):
        return "safe digest"

    async def send(admin, body):
        sent.append((admin, body))

    monkeypatch.setattr(summary, "build_summary", build)

    async def run():
        service = summary.DailySummaries(settings, send)
        assert await service.tick(local(14, 10)) == 0
        assert await summary.DailySummaries(settings, send).tick(local(15, 8)) == 1
        assert await service.tick(local(15, 20)) == 0
        assert await service.tick(local(15)) == 1

    asyncio.run(run())
    assert len(sent) == 2


def test_disabled_schedule_never_reads_sources_or_sends(tmp_path):
    settings = OpsSettings(state_path=str(tmp_path / "state.db"), daily_summary_enabled=False)
    assert asyncio.run(summary.DailySummaries(settings, None).tick(local(14))) == 0
    assert not (tmp_path / "state.db").exists()


@pytest.mark.parametrize("field,value", [("daily_summary_hour", 24), ("daily_summary_minute", 60)])
def test_invalid_schedule_rejected(field, value):
    with pytest.raises(ValueError):
        OpsSettings(**{field: value})

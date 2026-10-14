"""Independent daily HH acceptance; synthetic snapshots, no external delivery.

The PostgreSQL runner checks the same aggregates through the real collector CLI.
"""
import asyncio
import copy
import importlib
import importlib.util
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from admin_ops.config import OpsSettings

ZONE = ZoneInfo("Asia/Yekaterinburg")


def local(day, hour=19, minute=5):
    return datetime(2026, 10, day, hour, minute, tzinfo=ZONE)


def utc(value):
    return value.astimezone(timezone.utc).isoformat()


def product():
    assert importlib.util.find_spec("admin_ops.hr_daily_summary") is not None, (
        "PRODUCT RED: separate scheduled HH daily summary is not implemented"
    )
    return importlib.import_module("admin_ops.hr_daily_summary")


def config(tmp_path, **overrides):
    return OpsSettings(_env_file=None, state_path=str(tmp_path / "shared-state.sqlite3"),
                       admin_ids="123,456", hr_monitor_host="synthetic.invalid", **overrides)


def report(day=14, **overrides):
    end = local(day)
    result = {"start": utc(end.replace(hour=0, minute=0)), "end": utc(end), "as_of": utc(end),
              "candidates": 3, "completed_runs": 5, "succeeded": 3, "failed": 1, "cancelled": 1,
              "timed_runs": 2, "avg_seconds": 120, "p95_seconds": 180,
              "failed_attempts": 3, "retry_attempts": 2,
              "error_stages": [{"stage": "documents", "count": 2}, {"stage": "llm", "count": 1}],
              "active_runs": 2, "waiting_runs": 1, "queued_tasks": 4, "running_tasks": 1}
    result.update(overrides)
    return result


def snapshot(now=None, **overrides):
    result = {"schema_version": 1, "generated_at": utc(now or local(14, 19, 6)),
              "sources": {"docker": "ok", "database": "ok", "events": "ok"},
              "services": [{"name": "web", "running": 1, "unhealthy": 0, "restarts": 0, "oom": False}],
              "incidents": [], "daily_reports": [report(14), report(13)]}
    result.update(overrides)
    return result


def test_separate_product_defaults_and_interface():
    module = product()
    settings = OpsSettings(_env_file=None)
    assert settings.hr_daily_summary_enabled is True
    assert (settings.hr_daily_summary_hour, settings.hr_daily_summary_minute) == (19, 5)
    assert (settings.daily_summary_hour, settings.daily_summary_minute) == (19, 0)
    assert hasattr(module, "build_summary") and hasattr(module, "HrDailySummaries")


def test_builder_displays_complete_window_counts_timing_coverage_and_current_queue(monkeypatch, tmp_path):
    module = product()

    async def read(_):
        return snapshot()

    monkeypatch.setattr(module, "read_snapshot", read)
    text = asyncio.run(module.build_summary(config(tmp_path), local(14), local(14, 19, 6)))
    assert "00:00" in text and "19:05" in text and "14.10.2026" in text
    assert any(word in text.lower() for word in ("кандидат", "hh"))
    assert re.search(r"кандидат\w*\s*:?\s*3", text.lower())
    assert re.search(r"запуск\w*\s*:?\s*5", text.lower())
    assert any(word in text.lower() for word in ("успеш", "ошиб"))
    assert "2 из 3" in text, "Missing timing must remain missing, with coverage visible"
    assert any(word in text.lower() for word in ("очеред", "ожидан"))
    assert "PRIVATE_SYNTHETIC" not in text


@pytest.mark.parametrize("failure", ["database", "ssh", "malformed", "stale"])
def test_unavailable_statistics_are_explicit_unknown_without_claiming_zero(monkeypatch, tmp_path, failure):
    module = product()

    async def read(_):
        if failure == "ssh":
            raise OSError("PRIVATE_SYNTHETIC_SSH_FAILURE")
        data = snapshot()
        if failure == "database":
            data["sources"]["database"] = "unavailable"
        elif failure == "malformed":
            data["daily_reports"][0]["candidates"] = -1
        elif failure == "stale":
            data["generated_at"] = utc(local(14, 18))
        return data

    monkeypatch.setattr(module, "read_snapshot", read)
    text = asyncio.run(module.build_summary(config(tmp_path), local(14), local(14, 19, 6)))
    assert any(word in text.lower() for word in ("недоступ", "неизвест", "нет данных"))
    assert not re.search(r"кандидат\w*\s*:?\s*0", text.lower())
    assert "PRIVATE_SYNTHETIC" not in text


def test_private_extra_fields_and_untrusted_stage_never_reach_summary(monkeypatch, tmp_path):
    module = product()

    async def read(_):
        data = snapshot()
        data["daily_reports"][0].update(candidate_id="PRIVATE_SYNTHETIC_ID", raw_error="PRIVATE_SYNTHETIC_ERROR")
        data["daily_reports"][0]["error_stages"].append({"stage": "PRIVATE_SYNTHETIC_STAGE", "count": 1})
        return data

    monkeypatch.setattr(module, "read_snapshot", read)
    text = asyncio.run(module.build_summary(config(tmp_path), local(14), local(14, 19, 6)))
    assert "PRIVATE_SYNTHETIC" not in text


@pytest.mark.parametrize("incomplete", ["as_of", "generated_at"])
def test_fresh_pre_cutoff_aggregate_must_retry_instead_of_freezing_partial_day(monkeypatch, tmp_path, incomplete):
    module = product()

    async def read(_):
        data = snapshot(now=local(14, 19, 5) + timedelta(seconds=5))
        if incomplete == "as_of":
            data["daily_reports"][0]["as_of"] = utc(local(14, 19, 4))
        else:
            data["generated_at"] = utc(local(14) - timedelta(seconds=1))
        return data

    monkeypatch.setattr(module, "read_snapshot", read)
    with pytest.raises(ValueError):
        asyncio.run(module.build_summary(config(tmp_path), local(14), local(14, 19, 5) + timedelta(seconds=5)))


def test_daily_delivery_independent_from_ocr_schedule_and_durable_per_recipient(monkeypatch, tmp_path):
    module = product()
    from admin_ops import daily_summary as ocr
    sent, attempts = [], {456: 0}

    async def build(*_):
        return "HH synthetic daily summary"

    async def build_ocr(*_):
        return "OCR synthetic daily summary"

    async def send(chat, body):
        sent.append((chat, body))
        if chat == 456 and body.startswith("HH") and attempts[456] == 0:
            attempts[456] += 1
            raise OSError("synthetic delivery failure")

    monkeypatch.setattr(module, "build_summary", build)
    monkeypatch.setattr(ocr, "build_summary", build_ocr)
    settings = config(tmp_path)

    async def run():
        hh = module.HrDailySummaries(settings, send)
        old = ocr.DailySummaries(settings, send)
        assert await hh.tick(local(14, 18)) == 0
        assert await old.tick(local(14, 18)) == 0
        assert await old.tick(local(14, 19, 0)) == 2
        assert await hh.tick(local(14, 19, 0)) == 0
        assert await hh.tick(local(14)) == 1
        assert await module.HrDailySummaries(settings, send).tick(local(14, 19, 6)) == 1
        assert await module.HrDailySummaries(settings, send).tick(local(14, 19, 7)) == 0

    asyncio.run(run())
    assert sum(body.startswith("OCR") for _, body in sent) == 2
    assert [(chat, body) for chat, body in sent if body.startswith("HH")] == [
        (123, "HH synthetic daily summary"), (456, "HH synthetic daily summary"),
        (456, "HH synthetic daily summary")]


def test_first_activation_skips_history_and_next_morning_catches_latest_due_day(monkeypatch, tmp_path):
    module, sent = product(), []

    async def build(_settings, end, _now):
        return utc(end)

    async def send(chat, text):
        sent.append((chat, text))

    monkeypatch.setattr(module, "build_summary", build)
    settings = config(tmp_path)

    async def run():
        assert await module.HrDailySummaries(settings, send).tick(local(14, 20)) == 0
        assert await module.HrDailySummaries(settings, send).tick(local(16, 8)) == 2
        assert await module.HrDailySummaries(settings, send).tick(local(16, 9)) == 0

    asyncio.run(run())
    assert sent == [(123, utc(local(15))), (456, utc(local(15)))]


def test_pre_cutoff_data_is_retried_and_unavailable_source_is_not_successful_zero(monkeypatch, tmp_path):
    module, sent = product(), []
    current = snapshot(local(14, 18))

    async def read(_):
        return copy.deepcopy(current)

    async def send(chat, body):
        sent.append((chat, body))

    monkeypatch.setattr(module, "read_snapshot", read)
    settings = config(tmp_path)

    async def run():
        scheduler = module.HrDailySummaries(settings, send)
        await scheduler.tick(local(14, 18))
        current.update(snapshot(local(14)))
        current["daily_reports"][0]["as_of"] = utc(local(14, 19, 4))
        try:
            await scheduler.tick(local(14))
        except ValueError:
            pass  # run loop may own retry handling; no delivery may be persisted.
        assert sent == []
        current.update(snapshot(local(14, 19, 6)))
        current["sources"]["database"] = "unavailable"
        await module.HrDailySummaries(settings, send).tick(local(14, 19, 6))

    asyncio.run(run())
    assert len(sent) == 2
    assert all(any(word in body.lower() for word in ("недоступ", "неизвест", "нет данных")) for _, body in sent)


def test_disabled_daily_summary_never_reads_or_writes_state(monkeypatch, tmp_path):
    module = product()

    async def forbidden(*_):
        raise AssertionError("Disabled daily summary touched source or delivery")

    monkeypatch.setattr(module, "read_snapshot", forbidden)
    settings = config(tmp_path, hr_daily_summary_enabled=False)
    assert asyncio.run(module.HrDailySummaries(settings, forbidden).tick(local(14))) == 0
    assert not (tmp_path / "shared-state.sqlite3").exists()

"""Independent MON-001..003 acceptance; all values are synthetic.

Focused consumer tests complement run_hr_incident_postgres_acceptance.py, which
exercises the real collector CLI against a disposable PostgreSQL 16 boundary.
No test contacts Telegram or candidate production data.
"""
import asyncio
import copy
import hashlib
import importlib
import importlib.util
import json
from datetime import datetime, timedelta, timezone

import pytest

NOW = datetime(2026, 10, 14, 12, tzinfo=timezone.utc)


def product():
    assert importlib.util.find_spec("admin_ops.hr_incidents") is not None, (
        "PRODUCT RED MON-001: candidate incident monitor is not implemented"
    )
    return importlib.import_module("admin_ops.hr_incidents")


def settings(tmp_path, **overrides):
    from admin_ops.config import OpsSettings
    return OpsSettings(_env_file=None, admin_ids="123,456", hr_monitor_host="fixture.invalid",
                       state_path=str(tmp_path / "outbox.sqlite3"), **overrides)


def stamp(value):
    return value.isoformat().replace("+00:00", "Z")


def snapshot(at=NOW, *, incidents=None, healthy=True):
    return {"schema_version": 1, "generated_at": stamp(at),
            "sources": {"docker": "ok", "database": "ok", "events": "ok"},
            "services": [{"name": "web", "running": int(healthy), "unhealthy": int(not healthy),
                          "restarts": 0, "oom": False}], "incidents": incidents or []}


def incident(identity="synthetic-task", *, at=None, kind="task_failed", code="HTTP_503"):
    return {"id": hashlib.sha256(identity.encode()).hexdigest(),
            "occurred_at": stamp(at or NOW + timedelta(seconds=5)),
            "kind": kind, "stage": "documents", "code": code, "attempts": 2}


def test_product_exposes_collector_and_monitor():
    assert importlib.util.find_spec("admin_ops.hr_snapshot") is not None, (
        "PRODUCT RED MON-002: sanitized PostgreSQL collector CLI is not implemented"
    )
    assert hasattr(product(), "HrIncidentMonitor")


def test_new_task_and_terminal_incidents_are_durable_and_explain_action(monkeypatch, tmp_path):
    module, sent = product(), []
    current = snapshot(incidents=[incident("old", at=NOW - timedelta(hours=1))])

    async def read(_):
        return copy.deepcopy(current)

    async def send(chat, text):
        sent.append((chat, text))

    monkeypatch.setattr(module, "read_snapshot", read)
    config = settings(tmp_path)

    async def run():
        monitor = module.HrIncidentMonitor(config, send)
        await monitor.tick(NOW)
        assert sent == [], "First activation must not flood historical task errors"
        current["incidents"] += [incident(), incident("terminal", kind="run_failed")]
        current["generated_at"] = stamp(NOW + timedelta(seconds=20))
        assert await monitor.tick(NOW + timedelta(seconds=20)) == 4
        await monitor.tick(NOW + timedelta(seconds=40))
        await module.HrIncidentMonitor(config, send).tick(NOW + timedelta(seconds=60))

    asyncio.run(run())
    assert len(sent) == 4
    assert sorted(chat for chat, _ in sent) == [123, 123, 456, 456]
    bodies = {body for _, body in sent}
    assert len(bodies) == 2, "Task failure and final run failure are distinct incidents"
    for body in bodies:
        assert "HTTP_503" in body
        assert any(word in body.lower() for word in ("документ", "обработ", "этап"))
        assert any(word in body.lower() for word in ("открой", "проверь", "повтор", "сервис"))
        assert "synthetic-task" not in body and "terminal" not in body


def test_partial_telegram_failure_retries_only_pending_recipient_after_restart(monkeypatch, tmp_path):
    module, calls = product(), []
    current = snapshot()
    failed = False

    async def read(_):
        return copy.deepcopy(current)

    async def send(chat, text):
        nonlocal failed
        calls.append((chat, text))
        if chat == 456 and not failed:
            failed = True
            raise OSError("synthetic delivery unavailable")

    monkeypatch.setattr(module, "read_snapshot", read)
    config = settings(tmp_path)

    async def run():
        await module.HrIncidentMonitor(config, send).tick(NOW)
        current["incidents"] = [incident()]
        current["generated_at"] = stamp(NOW + timedelta(seconds=20))
        assert await module.HrIncidentMonitor(config, send).tick(NOW + timedelta(seconds=20)) == 1
        assert await module.HrIncidentMonitor(config, send).tick(NOW + timedelta(seconds=40)) == 1
        await module.HrIncidentMonitor(config, send).tick(NOW + timedelta(seconds=60))

    asyncio.run(run())
    assert [chat for chat, _ in calls].count(123) == 1
    assert [chat for chat, _ in calls].count(456) == 2
    assert len({text for _, text in calls}) == 1


def test_current_service_failure_alerts_on_activation_and_recovers_once(monkeypatch, tmp_path):
    module, sent = product(), []
    current = snapshot(healthy=False)

    async def read(_):
        return copy.deepcopy(current)

    async def send(chat, text):
        sent.append((chat, text))

    monkeypatch.setattr(module, "read_snapshot", read)
    config = settings(tmp_path)

    async def run():
        await module.HrIncidentMonitor(config, send).tick(NOW)
        assert len(sent) == 2, "Existing unhealthy infrastructure must alert immediately"
        await module.HrIncidentMonitor(config, send).tick(NOW + timedelta(seconds=20))
        assert len(sent) == 2
        current.update(snapshot(NOW + timedelta(seconds=40)))
        await module.HrIncidentMonitor(config, send).tick(NOW + timedelta(seconds=40))
        await module.HrIncidentMonitor(config, send).tick(NOW + timedelta(seconds=60))

    asyncio.run(run())
    assert len(sent) == 4
    assert all("web" in text for _, text in sent)
    assert any("восстанов" in text.lower() or "снова доступ" in text.lower() for _, text in sent)
    assert not any("кандидат успешно" in text.lower() for _, text in sent)


@pytest.mark.parametrize("fault", ["transport", "stale", "database", "truncated", "unsafe"])
def test_loss_or_unsafe_snapshot_is_explicit_after_two_polls(monkeypatch, tmp_path, fault):
    module, sent = product(), []
    current = snapshot()
    broken = False

    async def read(_):
        if broken and fault == "transport":
            raise OSError("PRIVATE_SYNTHETIC_TRANSPORT_CONTENT")
        return copy.deepcopy(current)

    async def send(chat, text):
        sent.append((chat, text))

    monkeypatch.setattr(module, "read_snapshot", read)
    config = settings(tmp_path)

    async def run():
        nonlocal broken
        monitor = module.HrIncidentMonitor(config, send)
        await monitor.tick(NOW)
        broken = True
        current["generated_at"] = stamp(NOW + timedelta(seconds=120))
        if fault == "stale":
            current["generated_at"] = stamp(NOW - timedelta(seconds=91))
        elif fault == "database":
            current["sources"]["database"] = "unavailable"
        elif fault == "truncated":
            current["sources"]["events"] = "truncated"
        elif fault == "unsafe":
            current["incidents"] = [incident(code="PRIVATE_SYNTHETIC_ERROR_CONTENT")]
        await monitor.tick(NOW + timedelta(seconds=120))
        await monitor.tick(NOW + timedelta(seconds=140))

    asyncio.run(run())
    assert len(sent) >= 2, "Missing/invalid data must not silently appear healthy"
    assert "PRIVATE_SYNTHETIC" not in json.dumps(sent)
    assert any(word in " ".join(text for _, text in sent).lower()
               for word in ("недоступ", "монитор", "усеч", "неполн", "ошиб", "связ", "наблюд"))


def test_disabled_monitor_does_not_read_sources_or_touch_state(monkeypatch, tmp_path):
    module = product()

    async def forbidden(*_):
        raise AssertionError("Disabled monitor touched external boundary")

    monkeypatch.setattr(module, "read_snapshot", forbidden)
    config = settings(tmp_path, hr_monitor_enabled=False)
    assert asyncio.run(module.HrIncidentMonitor(config, forbidden).tick(NOW)) == 0
    assert not (tmp_path / "outbox.sqlite3").exists()


def test_missing_monitor_host_stays_silent(monkeypatch, tmp_path):
    module = product()
    from admin_ops.config import OpsSettings

    async def forbidden(*_):
        raise AssertionError("Unconfigured monitor touched external boundary")

    monkeypatch.setattr(module, "read_snapshot", forbidden)
    config = OpsSettings(_env_file=None, hr_monitor_host="", admin_ids="123",
                         state_path=str(tmp_path / "unconfigured.sqlite3"))
    assert asyncio.run(module.HrIncidentMonitor(config, forbidden).tick(NOW)) == 0
    assert not (tmp_path / "unconfigured.sqlite3").exists()


@pytest.mark.parametrize("signal", ["restart", "oom", "memory", "disk"])
def test_resource_and_crash_signals_alert_once_per_active_condition(monkeypatch, tmp_path, signal):
    module, sent = product(), []
    current = snapshot()
    current["host"] = {"memory_used_percent": 40, "disk_free_percent": 70}

    async def read(_):
        return copy.deepcopy(current)

    async def send(chat, text):
        sent.append((chat, text))

    monkeypatch.setattr(module, "read_snapshot", read)
    config = settings(tmp_path)

    async def run():
        monitor = module.HrIncidentMonitor(config, send)
        await monitor.tick(NOW)
        assert not sent
        current["generated_at"] = stamp(NOW + timedelta(seconds=20))
        if signal == "restart":
            current["services"][0]["restarts"] = 1
        elif signal == "oom":
            current["services"][0]["oom"] = True
        elif signal == "memory":
            current["host"]["memory_used_percent"] = 98
        else:
            current["host"]["disk_free_percent"] = 5
        await monitor.tick(NOW + timedelta(seconds=20))
        await module.HrIncidentMonitor(config, send).tick(NOW + timedelta(seconds=40))

    asyncio.run(run())
    assert len(sent) == 2, "New crash/resource condition must alert once to each recipient"

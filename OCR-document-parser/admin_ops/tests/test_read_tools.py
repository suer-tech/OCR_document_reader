import asyncio
import json
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from admin_ops import read_tools as rt
from admin_ops.config import OpsSettings
from ocr_platform.storage.models import Base, Document, PipelineRun


def test_document_comparison_uses_equal_local_time_and_deduplicates():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Document(id="sensitive-id", source_type="api", document_type="court_decision_ru"))
        for name, day, hour, status in [("seed", 10, 1, "done"), ("a", 14, 9, "done"),
                                        ("b", 14, 14, "done"), ("c", 15, 9, "done"), ("d", 15, 10, "failed")]:
            session.add(PipelineRun(id=name, document_id="sensitive-id", profile_id="court",
                                   status=status, created_at=datetime(2026, 9, day, hour),
                                   started_at=datetime(2026, 9, day, hour), finished_at=datetime(2026, 9, day, hour, 1),
                                   retry_count=1, last_error="PERSONAL SECRET"))
        session.commit()
        now = datetime(2026, 9, 15, 11, tzinfo=timezone.utc)  # 16:00 local
        report = rt.history_report(session, rt.DocumentDays(days=2), now, "Asia/Yekaterinburg")
        assert report["days"][0]["totals"]["completed_runs"] == 1
        assert report["days"][1]["totals"]["completed_runs"] == 2
        assert report["days"][1]["totals"]["documents_unique"] == 1
        assert report["days"][1]["totals"]["errors"] == 1
        assert report["days"][1]["totals"]["avg_seconds"] == 60
        assert report["comparison"]["change_percent"] == 0
        full = rt.history_report(session, rt.DocumentDays(days=2, same_time=False), now, "Asia/Yekaterinburg")
        assert full["days"][0]["totals"]["completed_runs"] == 2
        assert full["days"][1]["partial_day"] is True
        assert "sensitive-id" not in json.dumps(report)
        assert "PERSONAL SECRET" not in json.dumps(report)


def test_absent_history_does_not_produce_fake_baseline():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        result = rt.history_report(session, rt.DocumentDays(days=3), datetime.now(timezone.utc), "UTC")
    assert result["comparison"]["baseline_days"] == 0
    assert result["comparison"]["change_percent"] is None
    assert all(d["coverage"] == "unknown_or_partial" for d in result["days"])


@pytest.mark.parametrize("name,args", [
    ("shell", {"cmd": "cat /etc/passwd"}), ("document_days", {"days": 1000}),
    ("document_days", {"sql": "SELECT * FROM text_versions"}),
    ("document_days", {"end_date": "2099-01-01", "days": 0}),
    ("metric_history", {"metric": "arbitrary_promql"}),
    ("metric_history", {"metric": "api_up", "url": "http://evil"}),
    ("log_events", {"query": "arbitrary logql"}), ("log_events", {"limit": 101}),
])
def test_read_tools_reject_unbounded_or_arbitrary_access(name, args):
    result = asyncio.run(rt.ReadTools(OpsSettings()).call(name, args))
    assert result["status"] == "unavailable"
    assert "SELECT" not in json.dumps(result)


def test_loki_strips_every_free_text_identifier_and_unknown_event(monkeypatch):
    async def read_json(url, params):
        assert url == "http://loki:3100/loki/api/v1/query_range"
        assert params["query"] == '{job="ocr-app",level="error"}'
        rows = [
            {"message": "pipeline_failed", "level": "error", "error_type": "ValueError",
             "error_message": "PASSPORT SECRET", "document_id": "PASSPORT SECRET", "path": "PASSPORT SECRET",
             "payload": {"text": "PASSPORT SECRET"}, "duration_ms": 150, "elapsed_seconds": "NaN"},
            {"message": "PASSPORT SECRET", "level": "error"},
        ]
        return {"status": "success", "data": {"result": [{"stream": {"unsafe_label": "PASSPORT SECRET"},
                "values": [["1000000000", json.dumps(row)] for row in rows]}]}}

    monkeypatch.setattr(rt, "read_json", read_json)
    result = asyncio.run(rt.ReadTools(OpsSettings()).call("log_events", {}))
    assert len(result["events"]) == 1
    assert result["events"][0]["duration_ms"] == 150
    assert "PASSPORT SECRET" not in json.dumps(result)
    assert "elapsed_seconds" not in result["events"][0]
    json.dumps(result, allow_nan=False)


def test_metric_history_reports_sample_coverage_and_not_nan(monkeypatch):
    async def read_json(url, params):
        assert "query_range" in url
        assert params["query"] == rt.METRICS["latency_p95"]
        assert params["step"] >= 60
        return {"status": "success", "data": {"result": [{"metric": {"private": "SECRET"},
                "values": [[100, "NaN"], [160, "0"], [220, "2"]]}]}}
    monkeypatch.setattr(rt, "read_json", read_json)
    result = asyncio.run(rt.ReadTools(OpsSettings()).call("metric_history", {"metric": "latency_p95"}))
    assert result["finite_points"] == 2
    assert result["unit"] == "seconds"
    assert result["sampled_max"] == 2
    assert result["points"][0]["value"] is None
    assert "SECRET" not in json.dumps(result, allow_nan=False)


def test_source_failure_is_unavailable_not_zero_or_secret(monkeypatch):
    async def read_json(*args):
        raise RuntimeError("postgres://secret:password@host")
    monkeypatch.setattr(rt, "read_json", read_json)
    assert asyncio.run(rt.ReadTools(OpsSettings()).call("log_events", {})) == {
        "status": "unavailable", "reason": "RuntimeError",
    }

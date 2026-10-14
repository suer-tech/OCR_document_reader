"""Scheduled aggregate-only digests, independent of Codex and document contents."""
from __future__ import annotations

import asyncio
import logging
import math
import sqlite3
from collections import defaultdict
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import mean, median
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from admin_ops.stats import prometheus_snapshot
from ocr_platform.storage.models import Document, PipelineRun

logger = logging.getLogger(__name__)
TYPE_NAMES = {
    "court_decision": "Решения суда", "court_decision_ru": "Решения суда",
    "court_order": "Судебные приказы", "court_order_ru": "Судебные приказы",
    "passport_main": "Паспорта", "passport_registration": "Регистрация",
    "rtk": "РТК", "rtk2": "РТК-2", "rtk3": "РТК-3", "unknown": "Не определён",
}


def report_window(end: datetime) -> tuple[datetime, datetime]:
    """A current-day digest ends at the configured local wall-clock time."""
    start = end.replace(hour=0, minute=0, second=0, microsecond=0)
    if start == end:
        start -= timedelta(days=1)
    return start, end


def summary_report(session: Session, start: datetime, end: datetime) -> dict:
    start_utc = start.astimezone(timezone.utc).replace(tzinfo=None)
    end_utc = end.astimezone(timezone.utc).replace(tzinfo=None)
    # Select only operational metadata; never select last_error, texts or payloads.
    rows = session.execute(select(
        PipelineRun.document_id, PipelineRun.status, PipelineRun.started_at,
        PipelineRun.finished_at, PipelineRun.retry_count, Document.document_type,
        PipelineRun.profile_id,
    ).join(Document, PipelineRun.document_id == Document.id).where(
        PipelineRun.finished_at >= start_utc, PipelineRun.finished_at < end_utc,
        PipelineRun.status.in_(("done", "failed")),
    ).limit(50001)).all()
    if len(rows) > 50000:
        raise ValueError("summary row limit exceeded")
    documents, durations = set(), []
    by_type = defaultdict(lambda: {"runs": 0, "errors": 0, "durations": []})
    errors = retries = successes = 0
    for doc_id, status, started, finished, retry_count, kind, profile in rows:
        documents.add(doc_id)
        label = TYPE_NAMES.get(profile, TYPE_NAMES.get(kind, "Другие"))
        bucket = by_type[label]
        bucket["runs"] += 1
        bucket["errors"] += status == "failed"
        errors += status == "failed"
        successes += status == "done"
        retries += max(0, retry_count or 0)
        if status == "done" and started is not None and finished >= started:
            seconds = (finished - started).total_seconds()
            durations.append(seconds)
            bucket["durations"].append(seconds)
    for bucket in by_type.values():
        values = bucket.pop("durations")
        bucket["avg_seconds"] = mean(values) if values else None
    ordered = sorted(durations)
    return {
        "documents": len(documents), "runs": len(rows), "successes": successes,
        "errors": errors, "retries": retries,
        "avg_seconds": mean(durations) if durations else None,
        "median_seconds": median(durations) if durations else None,
        "p95_seconds": ordered[math.ceil(0.95 * len(ordered)) - 1] if ordered else None,
        "timed_runs": len(durations), "by_type": dict(sorted(by_type.items())),
        "in_progress": session.scalar(select(func.count()).select_from(PipelineRun).where(
            PipelineRun.status.in_(("queued", "processing", "retrying")),
        )) or 0,
    }


def load_summary(database_url: str, start: datetime, end: datetime) -> dict:
    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        if engine.dialect.name == "postgresql":
            engine = engine.execution_options(postgresql_readonly=True)
        with Session(engine) as session:
            if engine.dialect.name == "postgresql":
                from sqlalchemy import text
                session.execute(text("SET LOCAL statement_timeout = '10s'"))
            return summary_report(session, start, end)
    finally:
        engine.dispose()


def duration(value: float | None) -> str:
    if value is None:
        return "нет данных"
    return f"{value:.1f} с" if value < 60 else f"{value / 60:.1f} мин"


def format_summary(report: dict | None, metrics: dict, start: datetime, end: datetime, now: datetime) -> str:
    lines = [f"📊 OCR · {start:%d.%m.%Y}",
             f"Период: {start:%H:%M}–{end:%H:%M} ({end.tzinfo})"]
    if report is None:
        lines.append("⚠️ Статистика обработки недоступна; количество и ошибки неизвестны.")
    else:
        lines.extend([
            f"Документов: {report['documents']} · завершённых запусков: {report['runs']}",
            f"Успешно: {report['successes']} · ошибок: {report['errors']} · ретраев: {report['retries']}",
            (f"Время обработки: среднее {duration(report['avg_seconds'])}, "
             f"медиана {duration(report['median_seconds'])}, 95% — до {duration(report['p95_seconds'])}"),
        ])
        if report["timed_runs"] != report["successes"]:
            lines.append(f"Тайминги доступны для {report['timed_runs']} из {report['successes']} успешных запусков.")
        for label, bucket in report["by_type"].items():
            lines.append(f"• {label}: {bucket['runs']} запусков, ошибок {bucket['errors']}, "
                         f"среднее {duration(bucket['avg_seconds'])}")
        lines.append(f"В работе/очереди БД: {report['in_progress']}")
    def up(key):
        value = metrics.get(key)
        return "нет данных" if value is None else ("доступен" if value == 1 else "недоступен")
    ready = metrics.get("rabbitmq_ready")
    queue = "нет данных" if ready is None else str(round(ready))
    lines.append(f"Состояние на {now:%d.%m %H:%M}: API {up('api_up')}, worker {up('worker_up')}; очередь RabbitMQ {queue}.")
    lines.append("Время — по успешным запускам; ретраи — накопленные у завершённых запусков.")
    return "\n".join(lines)


async def build_summary(settings, end: datetime, now: datetime) -> str:
    zone = ZoneInfo(settings.timezone)
    end, now = end.astimezone(zone), now.astimezone(zone)
    start, end = report_window(end)
    results = await asyncio.gather(
        asyncio.to_thread(load_summary, settings.database_url, start, end),
        prometheus_snapshot(settings.prometheus_url, include_history=False),
        return_exceptions=True,
    )
    report, metrics = results
    if isinstance(report, Exception):
        logger.warning("daily_summary_statistics_unavailable: %s", type(report).__name__)
        report = None
    if isinstance(metrics, Exception):
        metrics = {}
    return format_summary(report, metrics, start, end, now)


class DailySummaries:
    """One durable message per cutoff and recipient, with retries on explicit failures.

    Telegram has no idempotency key: a lost response after delivery can cause a
    duplicate. Persistent state prevents ordinary restart/retry duplicates.
    """
    def __init__(self, settings, send):
        self.settings, self.send = settings, send

    def connect(self):
        Path(self.settings.state_path).parent.mkdir(parents=True, exist_ok=True)
        return sqlite3.connect(self.settings.state_path, timeout=5)

    async def tick(self, now: datetime | None = None) -> int:
        if not self.settings.daily_summary_enabled:
            return 0
        now = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo(self.settings.timezone))
        cutoff = now.replace(hour=self.settings.daily_summary_hour,
                             minute=self.settings.daily_summary_minute, second=0, microsecond=0)
        if now < cutoff:
            cutoff -= timedelta(days=1)
        key = cutoff.isoformat()
        admins = sorted(self.settings.allowed_admin_ids)
        with closing(self.connect()) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS daily_summary_state (key TEXT PRIMARY KEY, value REAL NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS daily_summary_deliveries "
                       "(cutoff TEXT, recipient INTEGER, body TEXT NOT NULL, sent_at REAL, lease_until REAL, "
                       "PRIMARY KEY(cutoff, recipient))")
            db.execute("INSERT OR IGNORE INTO daily_summary_state VALUES ('activated_at', ?)", (now.timestamp(),))
            activated = db.execute("SELECT value FROM daily_summary_state WHERE key='activated_at'").fetchone()[0]
            existing = db.execute("SELECT body FROM daily_summary_deliveries WHERE cutoff=? LIMIT 1", (key,)).fetchone()
        if cutoff.timestamp() >= activated:
            body = existing[0] if existing else await build_summary(self.settings, cutoff, now)
            with closing(self.connect()) as db, db:
                db.executemany("INSERT OR IGNORE INTO daily_summary_deliveries (cutoff,recipient,body) VALUES (?,?,?)",
                               [(key, admin, body) for admin in admins])
        with closing(self.connect()) as db:
            pending = db.execute("SELECT cutoff,recipient,body FROM daily_summary_deliveries "
                                 "WHERE sent_at IS NULL ORDER BY cutoff,recipient").fetchall()
        sent = 0
        for delivery_key, admin, body in pending:
            if admin not in admins:
                continue
            with closing(self.connect()) as db, db:
                claimed = db.execute("UPDATE daily_summary_deliveries SET lease_until=? "
                                     "WHERE cutoff=? AND recipient=? AND sent_at IS NULL "
                                     "AND (lease_until IS NULL OR lease_until<=?)",
                                     (now.timestamp() + 120, delivery_key, admin, now.timestamp())).rowcount
            if not claimed:
                continue
            try:
                await self.send(admin, body)
            except Exception as exc:  # noqa: BLE001 - retry delivery without killing the scheduler
                logger.warning("daily_summary_delivery_failed: %s", type(exc).__name__)
                with closing(self.connect()) as db, db:
                    db.execute("UPDATE daily_summary_deliveries SET lease_until=NULL WHERE cutoff=? AND recipient=?",
                               (delivery_key, admin))
            else:
                with closing(self.connect()) as db, db:
                    db.execute("UPDATE daily_summary_deliveries SET sent_at=?,lease_until=NULL WHERE cutoff=? AND recipient=?",
                               (now.timestamp(), delivery_key, admin))
                sent += 1
                logger.info("daily_summary_sent: %s", delivery_key)
        return sent

    async def run(self):
        while True:
            try:
                await self.tick()
            except Exception as exc:  # noqa: BLE001 - scheduler survives transient source/state failures
                logger.error("daily_summary_tick_failed: %s", type(exc).__name__)
            await asyncio.sleep(60)

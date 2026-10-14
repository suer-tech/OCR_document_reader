"""Daily aggregate HH digest through the existing restricted host snapshot."""
from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from admin_ops.daily_summary import DailySummaries, duration
from admin_ops.hr_incidents import (
    STAGE_NAMES,
    read_snapshot,
    timestamp,
    validate_snapshot,
)
from admin_ops.hr_protocol import SERVICES, daily_report

logger = logging.getLogger(__name__)


async def build_summary(settings, end: datetime, now: datetime) -> str:
    zone = ZoneInfo(settings.timezone)
    end, now = end.astimezone(zone), now.astimezone(zone)
    start = end.replace(hour=0, minute=0, second=0, microsecond=0)
    lines = [f"📊 HH-агент · {start:%d.%m.%Y}", f"Период: {start:%H:%M}–{end:%H:%M} ({zone})"]
    try:
        raw = await read_snapshot(settings)
        clean = validate_snapshot(raw, now)
        if clean["sources"]["database"] != "ok":
            raise ValueError("daily database unavailable")
        reports = raw.get("daily_reports")
        if not isinstance(reports, list) or len(reports) > 2:
            raise ValueError("invalid daily reports")
        candidates = [daily_report(item) for item in reports]
        report = next(item for item in candidates if timestamp(item["start"]) == start
                      and timestamp(item["end"]) == end)
        generated = timestamp(raw["generated_at"]).astimezone(zone)
        if (timestamp(report["as_of"]) - generated).total_seconds() > 30:
            raise ValueError("daily aggregate newer than snapshot")
    except Exception as exc:  # noqa: BLE001 - missing source is an explicit unknown, never zero
        logger.warning("hr_daily_summary_source_unavailable: %s", type(exc).__name__)
        return "\n".join(lines + ["⚠️ Статистика HH недоступна; число обработок, ошибки и очередь неизвестны."])
    # A recent observer poll can predate the cutoff by seconds. Do not persist
    # that partial day: the durable scheduler retries on its next tick.
    if generated < end or timestamp(report["as_of"]) < end:
        raise ValueError("daily aggregate not yet complete")
    lines.extend([
        f"Кандидатов: {report['candidates']} · завершённых запусков: {report['completed_runs']}",
        f"Успешно: {report['succeeded']} · с ошибкой: {report['failed']} · отменено: {report['cancelled']}",
        f"Время успешных: среднее {duration(report['avg_seconds'])}, 95% — до {duration(report['p95_seconds'])}",
    ])
    if report["timed_runs"] != report["succeeded"]:
        lines.append(f"Тайминги: {report['timed_runs']} из {report['succeeded']} успешных запусков.")
    lines.append(f"Ошибок попыток: {report['failed_attempts']} · повторных попыток: {report['retry_attempts']}")
    if report["error_stages"]:
        lines.append("По этапам: " + "; ".join(f"{STAGE_NAMES[item['stage']]} — {item['count']}"
                                              for item in report["error_stages"]))
    lines.append(f"Сейчас ({generated:%d.%m %H:%M}): в работе {report['active_runs']}, "
                 f"ждут человека {report['waiting_runs']}; задач в очереди {report['queued_tasks']}, "
                 f"выполняется {report['running_tasks']}.")
    if clean["sources"]["docker"] != "ok":
        lines.append("⚠️ Состояние сервисов неизвестно.")
    else:
        up = {item["name"] for item in clean["services"] if item["running"] and not item["unhealthy"]}
        missing = len(SERVICES - up)
        lines.append("Сервисы: доступны." if not missing else f"⚠️ Недоступных/unhealthy сервисов: {missing}.")
    lines.append("Время — от первой попытки до завершения; кандидаты считаются без повторов запусков.")
    return "\n".join(lines)


class HrDailySummaries(DailySummaries):
    namespace = "hr_daily_summary"
    schedule_prefix = "hr_daily_summary"

    async def build(self, end, now):
        return await build_summary(self.settings, end, now)

    async def tick(self, now=None):
        if not self.settings.hr_monitor_host:
            return 0
        return await super().tick(now)

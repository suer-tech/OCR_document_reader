"""Read-only HR incident observation and durable Telegram delivery."""
from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import math
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from admin_ops.hr_protocol import SERVICES, safe_code, safe_stage

logger = logging.getLogger(__name__)
STAGE_NAMES = {"control": "управление обработкой", "documents": "разбор документов",
               "media": "подготовка записи", "transcription": "стенограмма интервью",
               "llm": "AI-анализ и матрица вакансии", "reports": "формирование отчёта",
               "drive": "Google Drive", "notifications": "уведомления", "unknown": "обработка"}
REASONS = {
    "MATRIX_NOT_PUBLISHED": "матрица вакансии ещё не опубликована",
    "BLOB_SIZE_LIMIT_EXCEEDED": "артефакт превышает допустимый размер",
    "CORRUPT_FILE": "файл повреждён; требуется замена файла",
    "UNSUPPORTED_FILE": "формат файла не поддерживается; требуется замена файла",
    "GOOGLE_DRIVE_REAUTH_REQUIRED": "нужно повторно подключить Google Drive",
    "GOOGLE_OAUTH_INVALID_GRANT": "нужно повторно подключить Google Drive",
    "ASSEMBLYAI_TRANSCRIPTION_FAILED": "сервис транскрибации не обработал запись",
    "ASSEMBLYAI_TRANSCRIPTION_TIMEOUT": "истекло время ожидания транскрибации",
    "REPORT_VERSION_CONFLICT": "конфликт версии отчёта; существующий отчёт сохранён",
    "LLM_CAPABILITY_FAILED:timeout": "истекло время ожидания ответа модели",
    "LLM_CAPABILITY_FAILED:rate_limit": "провайдер модели ограничил частоту запросов",
    "LLM_CAPABILITY_FAILED:provider_unavailable": "провайдер модели недоступен",
    "LLM_CAPABILITY_FAILED:network": "сетевая ошибка при обращении к модели",
    "LEASE_EXPIRED_RECOVERED": "задача потеряла worker и была возвращена в очередь",
    "HTTP_429": "сервис ограничил частоту запросов",
    "HTTP_500": "внешний сервис вернул внутреннюю ошибку",
    "HTTP_502": "ошибка шлюза внешнего сервиса",
    "HTTP_503": "внешний сервис временно недоступен",
    "HTTP_504": "истекло время ожидания внешнего сервиса",
}


def timestamp(value):
    if not isinstance(value, str) or len(value) > 40:
        raise ValueError("invalid timestamp")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("missing timezone")
    return result


def integer(value):
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 1000000:
        raise ValueError("invalid count")
    return value


def validate_snapshot(raw, now):
    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        raise ValueError("unsupported snapshot")
    age = (now - timestamp(raw.get("generated_at"))).total_seconds()
    if not -30 <= age <= 90:
        raise ValueError("stale snapshot")
    sources = raw.get("sources", {})
    if not isinstance(sources, dict) or any(sources.get(k) not in {"ok", "unavailable", "truncated"}
                                             for k in ("docker", "database", "events")):
        raise ValueError("invalid sources")
    services, incidents = raw.get("services"), raw.get("incidents")
    if not isinstance(services, list) or len(services) > 100 or not isinstance(incidents, list) or len(incidents) > 1000:
        raise ValueError("invalid snapshot limits")
    clean = {"sources": {key: sources[key] for key in ("docker", "database", "events")},
             "services": [], "incidents": [], "host": {}, "stats": {}}
    for item in services:
        if not isinstance(item, dict) or item.get("name") not in SERVICES:
            continue
        clean["services"].append({"name": item["name"], "running": integer(item.get("running")),
                                  "unhealthy": integer(item.get("unhealthy")), "restarts": integer(item.get("restarts")),
                                  "oom": item.get("oom") is True})
    if sources["docker"] == "ok" and not clean["services"]:
        raise ValueError("missing service data")
    for item in incidents:
        if not isinstance(item, dict) or item.get("kind") not in {"task_failed", "run_failed"}:
            raise ValueError("invalid incident kind")
        identity = item.get("id")
        if not isinstance(identity, str) or not re.fullmatch(r"[a-f0-9]{64}", identity):
            raise ValueError("invalid incident identity")
        occurred = timestamp(item.get("occurred_at"))
        if (occurred - now).total_seconds() > 30:
            raise ValueError("future incident")
        clean["incidents"].append({"id": identity, "kind": item["kind"], "occurred_at": occurred.timestamp(),
                                   "stage": safe_stage(item.get("stage")), "code": safe_code(item.get("code")),
                                   "attempts": integer(item.get("attempts", 0))})
    for section, keys in (("host", ("disk_free_percent", "memory_used_percent")),
                          ("stats", ("oldest_progress_seconds",))):
        data = raw.get(section, {})
        if not isinstance(data, dict):
            continue
        for key in keys:
            value = data.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0:
                clean[section][key] = value
    return clean


async def read_snapshot(settings):
    host = str(ipaddress.ip_address(settings.hr_monitor_host))
    if not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", settings.hr_monitor_user):
        raise ValueError("invalid monitoring user")
    process = await asyncio.create_subprocess_exec(
        "ssh", "-i", settings.hr_monitor_key, "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=yes", "-o", f"UserKnownHostsFile={settings.hr_monitor_known_hosts}",
        "-o", "ConnectTimeout=8", "-o", "LogLevel=ERROR", "-T", f"{settings.hr_monitor_user}@{host}",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        body = await asyncio.wait_for(process.stdout.read(262145), timeout=12)
        if len(body) > 262144:
            raise ValueError("monitoring response too large")
        await asyncio.wait_for(process.wait(), timeout=2)
        if process.returncode:
            raise ValueError("monitoring read failed")
        return json.loads(body)
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


class HrIncidentMonitor:
    def __init__(self, settings, send):
        self.settings, self.send = settings, send

    def connect(self):
        Path(self.settings.state_path).parent.mkdir(parents=True, exist_ok=True)
        return sqlite3.connect(self.settings.state_path, timeout=5)

    def queue(self, db, key, body):
        link = self.settings.hr_monitor_url
        parsed = urlsplit(link)
        if parsed.scheme == "https" and parsed.hostname and not parsed.username and not parsed.password and not parsed.query and not parsed.fragment and len(link) <= 200:
            body += "\n" + link
        db.executemany("INSERT OR IGNORE INTO hr_incident_deliveries (identity,recipient,body) VALUES (?,?,?)",
                       [(key, admin, body) for admin in sorted(self.settings.allowed_admin_ids)])

    async def tick(self, now=None):
        if not self.settings.hr_monitor_enabled or not self.settings.hr_monitor_host:
            return 0
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            raise ValueError("missing timezone")
        with closing(self.connect()) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS hr_incident_state (key TEXT PRIMARY KEY,value TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS hr_incident_deliveries (identity TEXT,recipient INTEGER,body TEXT NOT NULL,"
                       "sent_at REAL,lease_until REAL,PRIMARY KEY(identity,recipient))")
            db.execute("INSERT OR IGNORE INTO hr_incident_state VALUES ('activated_at',?)", (str(now.timestamp()),))
            values = dict(db.execute("SELECT key,value FROM hr_incident_state").fetchall())
        conditions = json.loads(values.get("conditions", "{}"))
        restart_counts = json.loads(values.get("restart_counts", "{}"))
        failures = int(values.get("source_failures", "0"))
        updates, events = {}, []
        try:
            data = validate_snapshot(await read_snapshot(self.settings), now)
        except Exception as exc:  # noqa: BLE001 - sanitized unavailable source, preserve pending delivery
            logger.warning("hr_monitor_source_unavailable: %s", type(exc).__name__)
            failures += 1
            if failures >= 2:
                updates["monitor"] = (True, "мониторинг сервера кандидатов недоступен (нет свежего снимка)")
        else:
            failures = 0
            updates["monitor"] = (False, "связь с наблюдателем сервера кандидатов")
            updates["database"] = (data["sources"]["database"] != "ok", "БД обработки кандидатов недоступна")
            updates["docker"] = (data["sources"]["docker"] != "ok", "не удалось прочитать состояние контейнеров")
            updates["events"] = (data["sources"]["events"] != "ok", "история ошибок обработки недоступна или усечена")
            if data["sources"]["docker"] == "ok":
                for service in data["services"]:
                    name = service["name"]
                    updates["service:" + name] = (service["running"] == 0 or service["unhealthy"] > 0,
                                                  f"сервис {name} недоступен или нездоров")
                    updates["oom:" + name] = (service["oom"], f"нехватка памяти (OOM) у {name}")
                    if name in restart_counts and service["restarts"] > restart_counts[name]:
                        events.append((f"restart:{name}:{service['restarts']}:{now.timestamp()}",
                                       f"⚠️ Кандидаты: перезапуск сервиса {name}. Проверьте состояние обработки."))
                    restart_counts[name] = service["restarts"]
            if "disk_free_percent" in data["host"]:
                updates["disk"] = (data["host"]["disk_free_percent"] < 10, "на диске сервера кандидатов осталось менее 10%")
            if "memory_used_percent" in data["host"]:
                updates["memory"] = (data["host"]["memory_used_percent"] > 95, "сервер кандидатов использует более 95% памяти")
            if data["sources"]["database"] == "ok" and "oldest_progress_seconds" in data["stats"]:
                updates["stalled"] = (data["stats"]["oldest_progress_seconds"] > 3600,
                                      "активная обработка кандидата без прогресса более часа; проверьте этап")
            if data["sources"]["events"] in {"ok", "truncated"}:
                for incident in data["incidents"]:
                    if incident["occurred_at"] < float(values["activated_at"]):
                        continue
                    code = incident["code"]
                    final = incident["kind"] == "run_failed"
                    title = "⛔ Кандидаты: обработка завершилась ошибкой" if final else "⚠️ Кандидаты: ошибка этапа обработки"
                    reason = REASONS.get(code, "зафиксирована техническая ошибка обработки")
                    action = ("Откройте карточку со статусом ошибки; устраните причину и запустите повтор."
                              if final else "Проверьте статус в сервисе: автоматические повторы могли продолжиться.")
                    body = f"{title}\nЭтап: {STAGE_NAMES[incident['stage']]}.\nПричина: {reason} ({code}).\n{action}"
                    events.append(("incident:" + incident["id"], body))
        with closing(self.connect()) as db, db:
            for key, (active, description) in updates.items():
                previous = conditions.get(key, False)
                if active != previous:
                    recovered = {"monitor": "связь с наблюдателем восстановлена", "database": "БД снова доступна",
                                 "docker": "состояние контейнеров снова доступно", "events": "история ошибок снова доступна полностью",
                                 "disk": "на диске снова достаточно места", "memory": "загрузка памяти снова в допустимых пределах",
                                 "stalled": "обработка снова прогрессирует либо завершена; итог кандидата проверьте в сервисе"}
                    recovery = recovered.get(key, "проблема устранена")
                    if key.startswith("service:"):
                        recovery = f"сервис {key.split(':', 1)[1]} восстановлен и снова доступен"
                    elif key.startswith("oom:"):
                        recovery = f"признак OOM у {key.split(':', 1)[1]} снят"
                    body = f"⚠️ Кандидаты: {description}." if active else f"✅ Кандидаты: {recovery}."
                    self.queue(db, f"condition:{key}:{int(active)}:{now.timestamp()}", body)
                conditions[key] = active
            for identity, body in events:
                self.queue(db, identity, body)
            db.executemany("INSERT INTO hr_incident_state (key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                           [("conditions", json.dumps(conditions)), ("restart_counts", json.dumps(restart_counts)),
                            ("source_failures", str(failures))])
        return await self.deliver(now)

    async def deliver(self, now):
        with closing(self.connect()) as db:
            pending = db.execute("SELECT identity,recipient,body FROM hr_incident_deliveries WHERE sent_at IS NULL ORDER BY rowid LIMIT 50").fetchall()
        sent = 0
        for identity, recipient, body in pending:
            if recipient not in self.settings.allowed_admin_ids:
                continue
            with closing(self.connect()) as db, db:
                claimed = db.execute("UPDATE hr_incident_deliveries SET lease_until=? WHERE identity=? AND recipient=? "
                                     "AND sent_at IS NULL AND (lease_until IS NULL OR lease_until<=?)",
                                     (now.timestamp() + 60, identity, recipient, now.timestamp())).rowcount
            if not claimed:
                continue
            try:
                await self.send(recipient, body)
            except Exception as exc:  # noqa: BLE001 - retry explicit delivery failure independently per recipient
                logger.warning("hr_incident_delivery_failed: %s", type(exc).__name__)
                with closing(self.connect()) as db, db:
                    db.execute("UPDATE hr_incident_deliveries SET lease_until=NULL WHERE identity=? AND recipient=?", (identity, recipient))
            else:
                with closing(self.connect()) as db, db:
                    db.execute("UPDATE hr_incident_deliveries SET sent_at=?,lease_until=NULL WHERE identity=? AND recipient=?",
                               (now.timestamp(), identity, recipient))
                sent += 1
        return sent

    async def run(self):
        while True:
            try:
                await self.tick()
            except Exception as exc:  # noqa: BLE001 - observer must not interrupt the Telegram bot
                logger.error("hr_incident_monitor_failed: %s", type(exc).__name__)
            await asyncio.sleep(20)

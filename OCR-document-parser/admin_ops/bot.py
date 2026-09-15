from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import uuid
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from admin_ops.alerts import AlertTracker
from admin_ops.config import OpsSettings, get_ops_settings
from admin_ops.github import PublicationError, create_draft_pr
from admin_ops.release import (
    ReleaseError,
    deployment_result,
    dispatch_production_deploy,
    merge_approved_pr,
    reconciled_merge_sha,
)
from admin_ops.stats import load_daily_report, prometheus_snapshot

logger = logging.getLogger(__name__)


def init_state(path: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS proposals (id TEXT PRIMARY KEY, payload TEXT NOT NULL, status TEXT NOT NULL, pr_url TEXT)"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS bot_state (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )


def save_proposal(path: str, proposal: dict) -> str:
    proposal_id = uuid.uuid4().hex[:8]
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO proposals (id, payload, status) VALUES (?, ?, 'proposed')",
            (proposal_id, json.dumps(proposal, ensure_ascii=False)),
        )
    return proposal_id


def get_proposal(path: str, proposal_id: str) -> tuple[dict, str, str | None] | None:
    with sqlite3.connect(path) as connection:
        row = connection.execute(
            "SELECT payload, status, pr_url FROM proposals WHERE id = ?", (proposal_id,)
        ).fetchone()
    return (json.loads(row[0]), row[1], row[2]) if row else None


def set_proposal_status(path: str, proposal_id: str, status: str, pr_url: str | None = None) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE proposals SET status = ?, pr_url = ? WHERE id = ?",
            (status, pr_url, proposal_id),
        )


def save_proposal_progress(path: str, proposal_id: str, payload: dict, status: str, pr_url: str | None) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE proposals SET payload = ?, status = ?, pr_url = ? WHERE id = ?",
            (json.dumps(payload, ensure_ascii=False), status, pr_url, proposal_id),
        )


def pending_deployments(path: str) -> list[tuple[str, dict, str | None]]:
    with sqlite3.connect(path) as connection:
        rows = connection.execute(
            "SELECT id, payload, pr_url FROM proposals WHERE status IN ('deploy_queued', 'dispatching')"
        ).fetchall()
    return [(row[0], json.loads(row[1]), row[2]) for row in rows]


def get_offset(path: str) -> int:
    with sqlite3.connect(path) as connection:
        row = connection.execute("SELECT value FROM bot_state WHERE key = 'offset'").fetchone()
    return int(row[0]) if row else 0


def set_offset(path: str, offset: int) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO bot_state (key, value) VALUES ('offset', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(offset),),
        )


def format_daily(report: dict) -> str:
    totals = report["totals"]
    lines = [
        f"OCR за {report['date']} ({report['timezone']})",
        f"Уникальных документов: {totals['documents_unique']}; завершённых запусков: {totals['processed']}",
        f"Ошибок: {totals['errors']}, ретраев: {totals['retries']}",
        f"Сейчас в работе/очереди: {totals['in_progress']}",
    ]
    for kind, values in report["by_type"].items():
        average = values["avg_seconds"]
        time_text = f"{average} с" if average is not None else "нет данных"
        lines.append(
            f"{kind}: {values['processed']} завершено, {values['errors']} ошибок, "
            f"{values['retries']} ретраев, среднее {time_text}"
        )
    return "\n".join(lines)


def format_health(metrics: dict) -> str:
    def value(key: str, unit: str = "") -> str:
        number = metrics.get(key)
        return "нет данных" if number is None else f"{number:.2f}{unit}"

    return "\n".join(
        [
            f"API scrape: {value('api_up')}; worker scrape: {value('worker_up')}",
            f"Запросы за 5 мин: {value('request_rate_5m', ' req/s')}",
            f"Пик за 24 ч: {value('request_peak_24h', ' req/s')}; всего за 24 ч: {value('requests_24h')}",
            f"HTTP 5xx за 5 мин: {value('error_rate_5m', ' req/s')}",
            f"HTTP p95 за 5 мин: {value('http_p95_seconds_5m', ' с')}",
            f"RabbitMQ ready: {value('rabbitmq_ready')}",
        ]
    )


class AdminBot:
    def __init__(self, settings: OpsSettings):
        self.settings = settings
        self.history: dict[int, deque[dict[str, str]]] = defaultdict(lambda: deque(maxlen=6))
        self.alert_tracker = AlertTracker()
        self.client = httpx.AsyncClient(timeout=35.0)

    async def send(self, chat_id: int, text: str) -> None:
        # Each Telegram message has a 4096-character limit; keep a margin.
        for index in range(0, len(text), 3500):
            response = await self.client.post(
                f"https://api.telegram.org/bot{self.settings.telegram_token}/sendMessage",
                json={"chat_id": chat_id, "text": text[index:index + 3500], "disable_web_page_preview": True},
            )
            response.raise_for_status()

    async def worker(self, url: str, payload: dict) -> dict:
        response = await self.client.post(
            url, json=payload, headers={"X-Ops-Token": self.settings.internal_token}, timeout=330.0
        )
        response.raise_for_status()
        return response.json()

    async def snapshot(self) -> dict:
        daily, metrics = await asyncio.gather(
            asyncio.to_thread(load_daily_report, self.settings.database_url, self.settings.timezone),
            prometheus_snapshot(self.settings.prometheus_url),
        )
        return {"daily": daily, "metrics": metrics}

    async def start_deploy(self, proposal_id: str, proposal: dict, pr_url: str | None) -> None:
        proposal["deploy_requested_at"] = datetime.now(timezone.utc).isoformat()
        save_proposal_progress(self.settings.state_path, proposal_id, proposal, "dispatching", pr_url)
        result = await dispatch_production_deploy(
            token=self.settings.github_token, repo=self.settings.github_repo,
            base_branch=self.settings.github_base_branch,
            workflow=self.settings.github_deploy_workflow,
            merge_sha=proposal["merge_sha"], proposal_id=proposal_id,
            pr_number=proposal["pr_number"],
        )
        proposal["deploy_run_id"] = result["run_id"]
        proposal["deploy_run_url"] = result["run_url"]
        save_proposal_progress(self.settings.state_path, proposal_id, proposal, "deploy_queued", pr_url)

    async def refresh_deployment(self, proposal_id: str, proposal: dict, pr_url: str | None) -> str:
        result = await deployment_result(
            token=self.settings.github_token, repo=self.settings.github_repo,
            workflow=self.settings.github_deploy_workflow,
            merge_sha=proposal["merge_sha"], run_id=proposal.get("deploy_run_id"),
            proposal_id=proposal_id, requested_at=proposal.get("deploy_requested_at"),
        )
        if result.get("run_id") and not proposal.get("deploy_run_id"):
            proposal["deploy_run_id"] = result["run_id"]
        if result.get("url"):
            proposal["deploy_run_url"] = result["url"]
        if result.get("status") == "completed":
            status = "deployed" if result.get("conclusion") == "success" else "deploy_failed"
            save_proposal_progress(self.settings.state_path, proposal_id, proposal, status, pr_url)
            return status
        if result.get("status") == "not_found" and proposal.get("deploy_requested_at"):
            requested = datetime.fromisoformat(proposal["deploy_requested_at"])
            if datetime.now(timezone.utc) - requested > timedelta(minutes=15):
                save_proposal_progress(self.settings.state_path, proposal_id, proposal, "deploy_unknown", pr_url)
                return "deploy_unknown"
        save_proposal_progress(self.settings.state_path, proposal_id, proposal, "deploy_queued", pr_url)
        return result.get("status", "unknown")

    async def handle(self, update: dict) -> None:
        message = update.get("message") or {}
        sender = message.get("from") or {}
        chat = message.get("chat") or {}
        admin_id = sender.get("id")
        if (
            admin_id not in self.settings.allowed_admin_ids
            or chat.get("type") != "private"
            or chat.get("id") != admin_id
        ):
            return
        text = (message.get("text") or "").strip()
        if not text:
            return
        command, _, argument = text.partition(" ")
        command = command.split("@", 1)[0].lower()
        if command in {"/start", "/help"}:
            await self.send(admin_id, "Команды: /today, /health, /pulse вопрос, /fix задача, /diff ID, /approve ID, /confirm ID SHA8, /deploy ID, /status ID. /approve создаёт PR; только /confirm после CI разрешает merge и деплой.")
        elif command == "/today":
            daily = await asyncio.to_thread(load_daily_report, self.settings.database_url, self.settings.timezone)
            await self.send(admin_id, format_daily(daily))
        elif command == "/health":
            await self.send(admin_id, format_health(await prometheus_snapshot(self.settings.prometheus_url)))
        elif command == "/fix":
            if not self.settings.enable_fixer:
                await self.send(admin_id, "Наладчик выключен. Для включения нужен изолированный Codex worker и настройка OPS_ENABLE_FIXER=true.")
            elif not argument.strip():
                await self.send(admin_id, "Укажите задачу после /fix.")
            else:
                proposal = await self.worker(f"{self.settings.fixer_url}/fix", {"request": argument.strip()[:1500]})
                proposal_id = save_proposal(self.settings.state_path, proposal)
                await self.send(
                    admin_id,
                    f"Предложение {proposal_id} ({len(proposal['files'])} файлов).\n"
                    f"Синтаксис Python: {proposal.get('checks', {}).get('python_syntax', 'unknown')}\n"
                    f"{proposal['summary'][:1200]}\n\n"
                    f"Прочитайте полный diff: /diff {proposal_id}\n"
                    f"После проверки создайте черновик PR: /approve {proposal_id}",
                )
        elif command == "/diff":
            proposal_id = argument.strip().lower()
            found = get_proposal(self.settings.state_path, proposal_id)
            await self.send(admin_id, found[0]["diff"] if found else "Предложение не найдено.")
        elif command == "/approve":
            proposal_id = argument.strip().lower()
            found = get_proposal(self.settings.state_path, proposal_id)
            if not found:
                await self.send(admin_id, "Предложение не найдено.")
            elif found[1] == "published":
                await self.send(admin_id, f"Черновик PR уже создан: {found[2]}")
            elif found[1] != "proposed":
                await self.send(admin_id, "Предложение уже публикуется или требует ручной проверки.")
            else:
                set_proposal_status(self.settings.state_path, proposal_id, "publishing")
                try:
                    pr_details = await create_draft_pr(
                        proposal_id=proposal_id, proposal=found[0],
                        token=self.settings.github_token, repo=self.settings.github_repo,
                        base_branch=self.settings.github_base_branch,
                    )
                except PublicationError as exc:
                    set_proposal_status(self.settings.state_path, proposal_id, "needs_review")
                    await self.send(admin_id, f"PR не создан: {exc}. Требуется проверка состояния GitHub.")
                else:
                    proposal = found[0]
                    proposal["pr_number"] = pr_details["number"]
                    proposal["pr_head_sha"] = pr_details["head_sha"]
                    save_proposal_progress(
                        self.settings.state_path, proposal_id, proposal, "published", pr_details["url"]
                    )
                    await self.send(
                        admin_id,
                        f"Черновик PR создан: {pr_details['url']}\n"
                        f"Ревизия: {pr_details['head_sha'][:8]}. Слияния и деплоя пока не было.\n"
                        f"После просмотра PR и успешного CI: /confirm {proposal_id} {pr_details['head_sha'][:8]}",
                    )
        elif command == "/confirm":
            parts = argument.strip().lower().split()
            if len(parts) != 2:
                await self.send(admin_id, "Формат: /confirm ID SHA8. SHA8 указан в сообщении о PR.")
                return
            proposal_id, sha_prefix = parts
            found = get_proposal(self.settings.state_path, proposal_id)
            if not self.settings.enable_release:
                await self.send(admin_id, "Merge/deploy выключен. Настройте CI, production workflow и OPS_ENABLE_RELEASE=true.")
            elif not found or found[1] != "published":
                await self.send(admin_id, "Нужен опубликованный, ещё не подтверждённый PR.")
            elif sha_prefix != found[0].get("pr_head_sha", "")[:8]:
                await self.send(admin_id, "SHA не совпадает с проверенной ревизией PR.")
            else:
                proposal, _, pr_url = found
                save_proposal_progress(self.settings.state_path, proposal_id, proposal, "confirming", pr_url)
                try:
                    merge_sha = await merge_approved_pr(
                        proposal_id=proposal_id, proposal=proposal,
                        token=self.settings.github_token, repo=self.settings.github_repo,
                        base_branch=self.settings.github_base_branch,
                        required_check=self.settings.github_required_check,
                    )
                except ReleaseError as exc:
                    try:
                        merge_sha = await reconciled_merge_sha(
                            token=self.settings.github_token, repo=self.settings.github_repo,
                            pr_number=proposal["pr_number"], proposal_id=proposal_id,
                            proposal=proposal, base_branch=self.settings.github_base_branch,
                        )
                    except ReleaseError:
                        save_proposal_progress(self.settings.state_path, proposal_id, proposal, "needs_review", pr_url)
                        await self.send(admin_id, f"Merge не подтверждён и состояние PR неясно: {exc}. Проверьте PR вручную: {pr_url}")
                        return
                    if merge_sha is None:
                        save_proposal_progress(self.settings.state_path, proposal_id, proposal, "published", pr_url)
                        await self.send(admin_id, f"Merge не выполнен: {exc}. После исправления повторите /confirm {proposal_id} {proposal['pr_head_sha'][:8]}")
                        return
                proposal["merge_sha"] = merge_sha
                save_proposal_progress(self.settings.state_path, proposal_id, proposal, "merged_awaiting_deploy", pr_url)
                try:
                    await self.start_deploy(proposal_id, proposal, pr_url)
                except ReleaseError as exc:
                    await self.send(
                        admin_id,
                        f"PR слит ({merge_sha[:8]}), но ответ о запуске деплоя не получен: {exc}. "
                        f"Бот проверит GitHub Actions; не повторяйте запуск до /status {proposal_id}.",
                    )
                else:
                    await self.send(admin_id, f"PR слит ({merge_sha[:8]}). Деплой запущен, результат сообщу отдельно. /status {proposal_id}")
        elif command == "/deploy":
            proposal_id = argument.strip().lower()
            found = get_proposal(self.settings.state_path, proposal_id)
            if not self.settings.enable_release or not found or found[1] != "merged_awaiting_deploy":
                await self.send(admin_id, "Нет подтверждённого merge, ожидающего деплоя.")
            else:
                try:
                    await self.start_deploy(proposal_id, found[0], found[2])
                except ReleaseError as exc:
                    await self.send(admin_id, f"Деплой не запущен: {exc}")
                else:
                    await self.send(admin_id, f"Деплой запущен для {found[0]['merge_sha'][:8]}; итог сообщу отдельно.")
        elif command == "/status":
            proposal_id = argument.strip().lower()
            found = get_proposal(self.settings.state_path, proposal_id)
            if not found:
                await self.send(admin_id, "Предложение не найдено.")
            elif found[1] in {"deploy_queued", "dispatching"}:
                status = await self.refresh_deployment(proposal_id, found[0], found[2])
                await self.send(admin_id, f"{proposal_id}: {status}. Workflow: {found[0].get('deploy_run_url') or 'ожидается'}")
            elif found[1] == "confirming":
                proposal, _, pr_url = found
                try:
                    merge_sha = await reconciled_merge_sha(
                        token=self.settings.github_token, repo=self.settings.github_repo,
                        pr_number=proposal["pr_number"], proposal_id=proposal_id,
                        proposal=proposal, base_branch=self.settings.github_base_branch,
                    )
                except ReleaseError:
                    await self.send(admin_id, f"{proposal_id}: состояние merge неизвестно. Проверьте PR вручную: {pr_url}")
                else:
                    if merge_sha:
                        proposal["merge_sha"] = merge_sha
                        save_proposal_progress(self.settings.state_path, proposal_id, proposal, "merged_awaiting_deploy", pr_url)
                        await self.send(admin_id, f"{proposal_id}: merge выполнен ({merge_sha[:8]}), деплой ещё не запущен. /deploy {proposal_id}")
                    else:
                        save_proposal_progress(self.settings.state_path, proposal_id, proposal, "published", pr_url)
                        await self.send(admin_id, f"{proposal_id}: merge не выполнен. Повторите /confirm {proposal_id} {proposal['pr_head_sha'][:8]}")
            else:
                await self.send(admin_id, f"{proposal_id}: {found[1]}. PR: {found[2] or 'нет'}")
        else:
            question = argument.strip() if command == "/pulse" else text
            if not question:
                await self.send(admin_id, "Укажите вопрос после /pulse.")
                return
            answer = await self.worker(
                f"{self.settings.pulse_url}/pulse",
                {"question": question[:1500], "snapshot": await self.snapshot(), "history": list(self.history[admin_id])},
            )
            self.history[admin_id].append({"question": question[:1000], "answer": answer["answer"][:1500]})
            await self.send(admin_id, answer["answer"])

    async def run(self) -> None:
        init_state(self.settings.state_path)
        offset = get_offset(self.settings.state_path)
        alert_task = asyncio.create_task(self.monitor_alerts())
        deployment_task = asyncio.create_task(self.monitor_deployments())
        try:
            while True:
                try:
                    response = await self.client.get(
                        f"https://api.telegram.org/bot{self.settings.telegram_token}/getUpdates",
                        params={"offset": offset, "timeout": 25, "allowed_updates": json.dumps(["message"])},
                        timeout=35.0,
                    )
                    response.raise_for_status()
                    updates = response.json().get("result", [])
                    for update in updates:
                        offset = update["update_id"] + 1
                        set_offset(self.settings.state_path, offset)
                        try:
                            await self.handle(update)
                        except Exception as exc:  # noqa: BLE001 - keep polling after a failed command
                            logger.error("admin_bot_request_failed: %s", type(exc).__name__)
                            message = update.get("message") or {}
                            sender = (message.get("from") or {}).get("id")
                            if sender in self.settings.allowed_admin_ids:
                                try:
                                    await self.send(sender, f"Запрос не выполнен ({type(exc).__name__}). Попробуйте /health или /today.")
                                except httpx.HTTPError:
                                    logger.error("admin_bot_error_delivery_failed")
                except (httpx.HTTPError, ValueError) as exc:
                    logger.warning("telegram_poll_failed: %s", type(exc).__name__)
                    await asyncio.sleep(5)
        finally:
            alert_task.cancel()
            deployment_task.cancel()
            await self.client.aclose()

    async def monitor_alerts(self) -> None:
        while True:
            await asyncio.sleep(60)
            try:
                metrics = await prometheus_snapshot(self.settings.prometheus_url, include_history=False)
                for name in self.alert_tracker.observe(metrics):
                    for admin_id in self.settings.allowed_admin_ids:
                        await self.send(admin_id, f"⚠️ OCR: {name}. Проверьте /health и Grafana.")
            except Exception:  # noqa: BLE001 - alerts must survive transient failures
                logger.error("critical_alert_delivery_failed")

    async def monitor_deployments(self) -> None:
        while True:
            await asyncio.sleep(60)
            for proposal_id, proposal, pr_url in pending_deployments(self.settings.state_path):
                try:
                    status = await self.refresh_deployment(proposal_id, proposal, pr_url)
                    if status in {"deployed", "deploy_failed", "deploy_unknown"}:
                        message = (
                            f"{proposal_id}: production-деплой успешен ({proposal['merge_sha'][:8]})."
                            if status == "deployed" else
                            (f"⚠️ {proposal_id}: запуск production workflow не найден. Проверьте GitHub Actions и VPS; merge уже выполнен."
                             if status == "deploy_unknown" else
                             f"⚠️ {proposal_id}: production-деплой завершился ошибкой. Проверьте workflow и VPS; merge уже выполнен.")
                        )
                        for admin_id in self.settings.allowed_admin_ids:
                            await self.send(admin_id, f"{message}\n{proposal.get('deploy_run_url') or ''}")
                except Exception:  # noqa: BLE001 - deployment monitoring survives API outages
                    logger.error("deployment_monitor_failed: %s", proposal_id)


def main() -> None:
    settings = get_ops_settings()
    if not settings.telegram_token or not settings.allowed_admin_ids or len(settings.internal_token) < 32:
        raise SystemExit("OPS_TELEGRAM_TOKEN, OPS_ADMIN_IDS and a 32+ character OPS_INTERNAL_TOKEN are required")
    if not settings.database_url:
        raise SystemExit("OPS_DATABASE_URL (preferably a read-only PostgreSQL user) is required")
    if settings.enable_release and not settings.github_token:
        raise SystemExit("OPS_GITHUB_TOKEN is required when OPS_ENABLE_RELEASE=true")
    logging.basicConfig(level=logging.INFO)
    asyncio.run(AdminBot(settings).run())


if __name__ == "__main__":
    main()

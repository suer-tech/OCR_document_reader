import asyncio
import subprocess
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from admin_ops import ai, bot as bot_module
from admin_ops.bot import AdminBot
from admin_ops.config import OpsSettings


@pytest.fixture
def worker(monkeypatch, tmp_path):
    settings = OpsSettings(role="fixer", internal_token="x" * 32, work_root=str(tmp_path))
    monkeypatch.setattr(ai, "get_ops_settings", lambda: settings)

    def clone(repo, branch, root):
        root.mkdir()
        project = root / ai.PROJECT_DIR
        project.mkdir()
        (project / "example.py").write_text("value = 1\n", encoding="utf-8")
        for args in [
            ["init", "-b", branch], ["add", "."],
            ["-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "Initial"],
        ]:
            subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)

    monkeypatch.setattr(ai, "clone_source", clone)
    with TestClient(ai.app) as client:
        yield client, settings


def test_analysis_returns_read_only_answer_without_proposal(worker, monkeypatch):
    client, _ = worker
    seen = []

    async def codex(**kwargs):
        seen.append(kwargs)
        return "В example.py:1 значение равно 1."

    monkeypatch.setattr(ai, "run_codex", codex)
    monkeypatch.setattr(ai, "collect_proposal", lambda *args: pytest.fail("analysis must not create proposal"))
    response = client.post("/analyze", headers={"X-Ops-Token": "x" * 32}, json={"request": "Как работает?"})
    assert response.status_code == 200
    assert response.json()["answer"].startswith("В example.py:1")
    assert len(response.json()["base_sha"]) == 40
    assert "files" not in response.json()
    assert seen[0]["sandbox"] == "read_only"
    assert seen[0]["instructions"] == ai.ANALYZE_INSTRUCTIONS
    assert seen[0]["config"]["web_search"] == "disabled"
    assert not Path(seen[0]["cwd"]).exists()


def test_analysis_requires_internal_auth_and_fixer_role(worker, monkeypatch):
    client, settings = worker
    assert client.post("/analyze", json={"request": "test"}).status_code == 403
    settings.role = "pulse"
    assert client.post("/analyze", headers={"X-Ops-Token": "x" * 32}, json={"request": "test"}).status_code == 404


def test_analysis_rejects_unexpected_checkout_changes(worker, monkeypatch):
    client, _ = worker

    async def bad_codex(**kwargs):
        (Path(kwargs["cwd"]) / "example.py").write_text("value = 2\n", encoding="utf-8")
        return "done"

    monkeypatch.setattr(ai, "run_codex", bad_codex)
    response = client.post("/analyze", headers={"X-Ops-Token": "x" * 32}, json={"request": "test"})
    assert response.status_code == 409


def test_fix_without_changes_returns_answer_not_422(worker, monkeypatch):
    client, _ = worker

    async def codex(**kwargs):
        return "Объяснение без правок"

    monkeypatch.setattr(ai, "run_codex", codex)
    response = client.post("/fix", headers={"X-Ops-Token": "x" * 32}, json={"request": "Посмотри условия"})
    assert response.status_code == 200
    assert response.json()["status"] == "no_changes"
    assert "files" not in response.json()


def message(text, admin=123):
    return {"message": {"from": {"id": admin}, "chat": {"id": admin, "type": "private"}, "text": text}}


def test_analysis_is_background_read_only_route_with_no_proposal(monkeypatch):
    async def run():
        bot = AdminBot(OpsSettings(telegram_token="dummy", admin_ids="123"))
        sent, calls = [], []
        started, release = asyncio.Event(), asyncio.Event()

        async def send(admin, text):
            sent.append(text)

        async def worker(url, payload):
            calls.append((url, payload))
            started.set()
            await release.wait()
            return {"answer": "Анализ", "base_sha": "a" * 40, "repository": "owner/repo", "branch": "main"}

        async def typing(admin):
            await asyncio.Event().wait()

        bot.send, bot.worker, bot.show_typing = send, worker, typing
        monkeypatch.setattr(bot_module, "save_proposal", lambda *args: pytest.fail("unexpected proposal"))
        try:
            await bot.handle(message("/analyze Как вычисляется срок?", admin=999))
            assert not sent
            await bot.handle(message("/analyze"))
            assert not calls
            await bot.handle(message("/analyze Как вычисляется срок?"))
            assert "только чтения" in sent[-1]
            await asyncio.wait_for(started.wait(), 1)
            await asyncio.wait_for(bot.handle(message("/help")), 1)
            release.set()
            await asyncio.wait_for(asyncio.gather(*bot.dialog_tasks.values()), 1)
            assert calls == [(bot.settings.fixer_url + "/analyze", {"request": "Как вычисляется срок?"})]
            assert "aaaaaaaaaaaa" in sent[-1] and "VPS" in sent[-1]
            assert not bot.history[123]
        finally:
            await bot.aclose()

    asyncio.run(run())


def test_bot_no_changes_does_not_save_proposal(monkeypatch):
    async def run():
        bot = AdminBot(OpsSettings(telegram_token="dummy", admin_ids="123"))
        sent = []

        async def send(admin, text):
            sent.append(text)

        async def worker(*args):
            return {"status": "no_changes", "summary": "Объяснение"}

        bot.send, bot.worker = send, worker
        monkeypatch.setattr(bot_module, "save_proposal", lambda *args: pytest.fail("unexpected proposal"))
        try:
            await bot.handle(message("/fix Посмотри код"))
            assert "PR не созданы" in sent[-1] and "Объяснение" in sent[-1]
        finally:
            await bot.aclose()

    asyncio.run(run())


def test_analysis_http_failure_does_not_leak_response_or_block_queue():
    async def run():
        bot = AdminBot(OpsSettings(telegram_token="dummy", admin_ids="123"))
        sent = []

        async def send(admin, text):
            sent.append(text)

        async def worker(url, payload):
            response = httpx.Response(500, text="SECRET", request=httpx.Request("POST", url))
            response.raise_for_status()

        async def typing(admin):
            await asyncio.Event().wait()

        bot.send, bot.worker, bot.show_typing = send, worker, typing
        try:
            await bot.handle(message("/analyze вопрос"))
            await asyncio.gather(*bot.dialog_tasks.values())
            assert "HTTP 500" in sent[-1]
            assert "SECRET" not in str(sent)
            assert not bot.dialog_tasks
        finally:
            await bot.aclose()

    asyncio.run(run())

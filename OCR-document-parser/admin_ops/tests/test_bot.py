import asyncio

import pytest

from admin_ops import bot as bot_module
from admin_ops.bot import AdminBot
from admin_ops.config import OpsSettings


def test_release_enabled_requires_github_token_at_startup(monkeypatch) -> None:
    settings = OpsSettings(
        telegram_token="dummy", admin_ids="123", internal_token="x" * 32,
        database_url="sqlite+pysqlite:///:memory:", enable_release=True,
        github_token="",
    )
    monkeypatch.setattr(bot_module, "get_ops_settings", lambda: settings)
    with pytest.raises(SystemExit, match="OPS_GITHUB_TOKEN"):
        bot_module.main()


def test_bot_ignores_non_admin_and_group_messages() -> None:
    settings = OpsSettings(telegram_token="not-a-real-token", admin_ids="123", internal_token="x" * 32)
    bot = AdminBot(settings)
    sent = []

    async def fake_send(chat_id, text):
        sent.append((chat_id, text))

    bot.send = fake_send

    async def run():
        await bot.handle({"message": {"from": {"id": 321}, "chat": {"id": 321, "type": "private"}, "text": "/help"}})
        await bot.handle({"message": {"from": {"id": 123}, "chat": {"id": -1, "type": "group"}, "text": "/help"}})
        await bot.handle({"message": {"from": {"id": 123}, "chat": {"id": 123, "type": "private"}, "text": "/help"}})
        await bot.client.aclose()

    asyncio.run(run())
    assert len(sent) == 1
    assert sent[0][0] == 123


def test_second_confirmation_merges_once_then_dispatches(monkeypatch, tmp_path) -> None:
    state = str(tmp_path / "state.sqlite3")
    bot_module.init_state(state)
    proposal = {"pr_number": 7, "pr_head_sha": "b" * 40, "base_sha": "a" * 40}
    proposal_id = bot_module.save_proposal(state, proposal)
    bot_module.save_proposal_progress(state, proposal_id, proposal, "published", "https://github.com/pull/7")
    settings = OpsSettings(
        telegram_token="dummy", admin_ids="123", internal_token="x" * 32,
        enable_release=True, state_path=state, github_token="fake", github_repo="owner/repo",
    )
    bot = AdminBot(settings)
    calls = []
    sent = []

    async def fake_merge(**kwargs):
        calls.append("merge")
        return "c" * 40

    async def fake_dispatch(**kwargs):
        calls.append("dispatch")
        return {"run_id": 42, "run_url": "https://github.com/actions/runs/42"}

    async def fake_send(chat_id, text):
        sent.append(text)

    monkeypatch.setattr(bot_module, "merge_approved_pr", fake_merge)
    monkeypatch.setattr(bot_module, "dispatch_production_deploy", fake_dispatch)
    bot.send = fake_send

    async def run():
        update = {
            "message": {"from": {"id": 123}, "chat": {"id": 123, "type": "private"},
                        "text": f"/confirm {proposal_id} bbbbbbbb"}
        }
        await bot.handle(update)
        await bot.handle(update)
        await bot.client.aclose()

    asyncio.run(run())
    stored = bot_module.get_proposal(state, proposal_id)
    assert calls == ["merge", "dispatch"]
    assert stored[1] == "deploy_queued"
    assert stored[0]["merge_sha"] == "c" * 40
    assert any("Деплой запущен" in item for item in sent)


def test_dispatch_failure_keeps_merge_without_blind_retry(monkeypatch, tmp_path) -> None:
    state = str(tmp_path / "state.sqlite3")
    bot_module.init_state(state)
    proposal = {"pr_number": 7, "pr_head_sha": "b" * 40, "base_sha": "a" * 40}
    proposal_id = bot_module.save_proposal(state, proposal)
    bot_module.save_proposal_progress(state, proposal_id, proposal, "published", "https://github.com/pull/7")
    settings = OpsSettings(
        telegram_token="dummy", admin_ids="123", internal_token="x" * 32,
        enable_release=True, state_path=state, github_token="fake", github_repo="owner/repo",
    )
    bot = AdminBot(settings)
    dispatch_attempts = []

    async def fake_merge(**kwargs):
        return "c" * 40

    async def fake_dispatch(**kwargs):
        dispatch_attempts.append(1)
        if len(dispatch_attempts) == 1:
            raise bot_module.ReleaseError("unavailable")
        return {"run_id": 43, "run_url": "https://github.com/actions/runs/43"}

    async def fake_send(chat_id, text):
        pass

    monkeypatch.setattr(bot_module, "merge_approved_pr", fake_merge)
    monkeypatch.setattr(bot_module, "dispatch_production_deploy", fake_dispatch)
    bot.send = fake_send

    async def run():
        prefix = {"from": {"id": 123}, "chat": {"id": 123, "type": "private"}}
        await bot.handle({"message": {**prefix, "text": f"/confirm {proposal_id} bbbbbbbb"}})
        assert bot_module.get_proposal(state, proposal_id)[1] == "dispatching"
        await bot.handle({"message": {**prefix, "text": f"/deploy {proposal_id}"}})
        assert len(dispatch_attempts) == 1
        await bot.client.aclose()

    asyncio.run(run())
    assert len(dispatch_attempts) == 1
    assert bot_module.get_proposal(state, proposal_id)[1] == "dispatching"

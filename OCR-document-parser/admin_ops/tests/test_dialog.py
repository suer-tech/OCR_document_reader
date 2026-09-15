import asyncio

import httpx

from admin_ops.bot import AdminBot
from admin_ops.config import OpsSettings


def message(text, admin_id=123, chat_type="private"):
    return {"message": {"from": {"id": admin_id},
                        "chat": {"id": admin_id, "type": chat_type}, "text": text}}


def setup_bot():
    bot = AdminBot(OpsSettings(telegram_token="dummy", admin_ids="123,456"))
    sent = []

    async def send(admin_id, text):
        sent.append((admin_id, text))

    async def snapshot():
        return {"daily": {}, "metrics": {}}

    async def typing(admin_id):
        await asyncio.Event().wait()

    bot.send = send
    bot.snapshot = snapshot
    bot.show_typing = typing
    return bot, sent


def test_dialog_ack_is_immediate_and_followups_preserve_history():
    async def run():
        bot, sent = setup_bot()
        started = asyncio.Event()
        release = asyncio.Event()
        calls = []

        async def worker(url, payload):
            calls.append((url, payload))
            started.set()
            await release.wait()
            return {"answer": f"Ответ {len(calls)}"}

        bot.worker = worker
        try:
            await bot.handle(message("Сколько документов сегодня?"))
            assert "Принял вопрос" in sent[0][1]
            await asyncio.wait_for(started.wait(), 1)
            await asyncio.wait_for(bot.handle(message("А сколько ошибок?")), 1)
            # Fast commands remain available while the AI response is pending.
            await asyncio.wait_for(bot.handle(message("/help")), 1)
            assert any("/pulse не обязателен" in text for _, text in sent)
            assert len(calls) == 1
            release.set()
            await asyncio.wait_for(asyncio.gather(*bot.dialog_tasks.values()), 1)
            assert calls[1][1]["history"] == [{"question": "Сколько документов сегодня?", "answer": "Ответ 1"}]
            assert [text for _, text in sent if text.startswith("Ответ")] == ["Ответ 1", "Ответ 2"]
            assert not bot.dialog_tasks and not bot.dialog_queues
        finally:
            await bot.aclose()

    asyncio.run(run())


def test_plain_edit_request_cannot_route_to_fixer_and_unknown_command_is_not_ai():
    async def run():
        bot, sent = setup_bot()
        calls = []

        async def worker(url, payload):
            calls.append(url)
            return {"answer": "Для правок используйте /fix."}

        bot.worker = worker
        try:
            await bot.handle(message("Исправь код и задеплой"))
            await asyncio.gather(*bot.dialog_tasks.values())
            await bot.handle(message("/fx исправь код"))
            await bot.handle(message("Привет!"))
            await bot.handle(message("/pulse"))
            await bot.handle(message("текст", 999))
            await bot.handle(message("текст", 123, "group"))
            assert calls == ["http://awg-gateway:8080/pulse"]
            assert any("Неизвестная команда" in text for _, text in sent)
            assert any(text.startswith("Привет!") for _, text in sent)
            assert all(admin_id == 123 for admin_id, _ in sent)
        finally:
            await bot.aclose()

    asyncio.run(run())


def test_queue_is_bounded_and_failure_does_not_block_next_question():
    async def run():
        bot, sent = setup_bot()
        release = asyncio.Event()
        calls = []

        async def worker(url, payload):
            calls.append(payload)
            await release.wait()
            if len(calls) == 1:
                raise httpx.ConnectError("private-details-must-not-be-logged")
            return {"answer": "ok"}

        bot.worker = worker
        try:
            for index in range(4):
                await bot.handle(message(f"вопрос {index}"))
            assert len(bot.dialog_queues[123]) == 3
            assert "отправьте следующий ещё раз" in sent[-1][1]
            release.set()
            await asyncio.gather(*bot.dialog_tasks.values())
            assert len(calls) == 3
            assert calls[1]["history"] == []
            assert any("ConnectError" in text for _, text in sent)
            assert all("private-details" not in text for _, text in sent)
            assert len(bot.history[123]) == 2
        finally:
            await bot.aclose()

    asyncio.run(run())


def test_dialogs_are_isolated_between_admins_and_cancelled_on_shutdown():
    async def run():
        bot, sent = setup_bot()
        started = asyncio.Event()
        calls = []

        async def worker(url, payload):
            calls.append(payload)
            if len(calls) == 2:
                started.set()
            await asyncio.Event().wait()

        bot.worker = worker
        await bot.handle(message("вопрос один", 123))
        await bot.handle(message("вопрос два", 456))
        await asyncio.wait_for(started.wait(), 1)
        assert all(payload["history"] == [] for payload in calls)
        await bot.aclose()
        assert not bot.dialog_tasks and not bot.dialog_queues
        assert bot.client.is_closed and bot.telegram_client.is_closed

    asyncio.run(run())


def test_typing_uses_telegram_client_and_failure_is_nonfatal():
    async def run():
        bot = AdminBot(OpsSettings(telegram_token="dummy"))
        called = asyncio.Event()

        async def post(url, **kwargs):
            assert url.endswith("/sendChatAction")
            assert kwargs["json"] == {"chat_id": 123, "action": "typing"}
            called.set()
            raise httpx.ConnectError("offline")

        bot.telegram_client.post = post
        task = asyncio.create_task(bot.show_typing(123))
        try:
            await asyncio.wait_for(called.wait(), 1)
            assert not task.done()
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await bot.aclose()

    asyncio.run(run())


def test_explicit_fix_command_still_routes_to_fixer(monkeypatch):
    from admin_ops import bot as bot_module

    async def run():
        bot, sent = setup_bot()
        calls = []

        async def worker(url, payload):
            calls.append((url, payload))
            return {"files": ["src/ocr_platform/example.py"], "summary": "test", "checks": {}}

        bot.worker = worker
        monkeypatch.setattr(bot_module, "save_proposal", lambda *args: "abc12345")
        try:
            await bot.handle(message("/fix Исправь регулярку"))
            assert calls == [("http://awg-gateway:8081/fix", {"request": "Исправь регулярку"})]
            assert not bot.dialog_tasks
            assert any("/approve abc12345" in text for _, text in sent)
        finally:
            await bot.aclose()

    asyncio.run(run())


def test_slow_queue_ack_cannot_restart_finished_dialog():
    async def run():
        bot, sent = setup_bot()
        release = asyncio.Event()
        started = asyncio.Event()
        normal_send = bot.send

        async def worker(url, payload):
            started.set()
            await release.wait()
            return {"answer": "ok"}

        async def send(admin_id, text):
            await normal_send(admin_id, text)
            if "в очередь" in text:
                release.set()
                await bot.dialog_tasks[admin_id]

        bot.worker = worker
        bot.send = send
        try:
            await bot.handle(message("первый вопрос"))
            await asyncio.wait_for(started.wait(), 1)
            await asyncio.wait_for(bot.handle(message("второй вопрос")), 1)
            assert not bot.dialog_tasks and not bot.dialog_queues
            assert len(bot.history[123]) == 2
        finally:
            await bot.aclose()

    asyncio.run(run())

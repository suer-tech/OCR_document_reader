import asyncio
import logging

import httpx
import pytest
from pydantic import ValidationError

from admin_ops import bot as bot_module
from admin_ops.config import OpsSettings


def test_blank_proxy_cannot_enable_direct_telegram_access():
    with pytest.raises(ValidationError):
        OpsSettings(telegram_proxy_url="")


def test_telegram_uses_separate_proxy_for_send_and_poll(monkeypatch, caplog):
    requests = {"telegram": [], "direct": []}
    options = []
    original_client = httpx.AsyncClient

    def factory(**kwargs):
        options.append(kwargs)
        route = "telegram" if "proxy" in kwargs else "direct"

        def respond(request):
            requests[route].append(request)
            return httpx.Response(200, json={"result": [], "answer": "ok"})

        return original_client(transport=httpx.MockTransport(respond), trust_env=False)

    monkeypatch.setattr(bot_module.httpx, "AsyncClient", factory)
    caplog.set_level(logging.INFO)
    bot = bot_module.AdminBot(OpsSettings(telegram_token="secret-test-token"))

    async def run():
        try:
            await bot.send(123, "hello")
            assert await bot.poll_updates(42) == []
            await bot.worker("http://awg-gateway:8080/pulse", {})
        finally:
            await bot.aclose()

    asyncio.run(run())
    assert options == [
        {"timeout": 35.0, "trust_env": False},
        {"timeout": 35.0, "proxy": "http://awg-gateway:8888", "trust_env": False},
    ]
    assert [r.url.path for r in requests["telegram"]] == [
        "/botsecret-test-token/sendMessage", "/botsecret-test-token/getUpdates",
    ]
    assert requests["telegram"][1].url.params["offset"] == "42"
    assert [r.url.host for r in requests["direct"]] == ["awg-gateway"]
    assert "secret-test-token" not in caplog.text
    assert bot.telegram_client.is_closed and bot.client.is_closed


def test_proxy_failure_does_not_retry_on_direct_client(monkeypatch):
    original_client = httpx.AsyncClient
    direct_requests = []

    def factory(**kwargs):
        def respond(request):
            if "proxy" in kwargs:
                raise httpx.ProxyError("proxy unavailable")
            direct_requests.append(request)
            return httpx.Response(200)
        return original_client(transport=httpx.MockTransport(respond), trust_env=False)

    monkeypatch.setattr(bot_module.httpx, "AsyncClient", factory)
    bot = bot_module.AdminBot(OpsSettings(telegram_token="dummy"))

    async def run():
        try:
            with pytest.raises(httpx.ProxyError):
                await bot.send(123, "test")
            with pytest.raises(httpx.ProxyError):
                await bot.poll_updates(0)
        finally:
            await bot.aclose()

    asyncio.run(run())
    assert direct_requests == []

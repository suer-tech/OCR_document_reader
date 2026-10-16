"""Tests may use ASGI transports and mocks, never live network providers."""
import httpx
import pytest


@pytest.fixture(autouse=True)
def no_live_http(monkeypatch):
    def deny(*args, **kwargs):
        raise AssertionError("Unexpected real HTTP request in tests; mock the provider")

    async def deny_async(*args, **kwargs):
        raise AssertionError("Unexpected real HTTP request in tests; mock the provider")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", deny)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", deny_async)

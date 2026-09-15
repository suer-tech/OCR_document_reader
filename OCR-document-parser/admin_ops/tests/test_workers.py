import asyncio
import sys
from types import SimpleNamespace

from fastapi.testclient import TestClient

from admin_ops import ai
from admin_ops.config import OpsSettings


def test_codex_sdk_uses_denied_escalations_and_role_sandbox(monkeypatch) -> None:
    seen = []

    class FakeThread:
        async def run(self, prompt):
            seen.append(prompt)
            return SimpleNamespace(final_response="ok")

    class FakeCodex:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def thread_start(self, **kwargs):
            seen.append(kwargs)
            return FakeThread()

    fake_sdk = SimpleNamespace(
        ApprovalMode=SimpleNamespace(deny_all="deny_all"),
        AsyncCodex=FakeCodex,
        Sandbox=SimpleNamespace(read_only="read_only", workspace_write="workspace_write"),
    )
    monkeypatch.setitem(sys.modules, "openai_codex", fake_sdk)
    monkeypatch.setattr(ai, "get_ops_settings", lambda: OpsSettings(codex_model=""))
    assert asyncio.run(ai.run_codex(cwd="/tmp", sandbox="read_only", prompt="health")) == "ok"
    assert seen[0]["sandbox"] == "read_only"
    assert seen[0]["approval_mode"] == "deny_all"
    seen.clear()
    assert asyncio.run(ai.run_codex(cwd="/tmp", sandbox="workspace_write", prompt="fix")) == "ok"
    assert seen[0]["sandbox"] == "workspace_write"


def test_pulse_rejects_missing_internal_token(monkeypatch) -> None:
    settings = OpsSettings(role="pulse", internal_token="x" * 32)
    monkeypatch.setattr(ai, "get_ops_settings", lambda: settings)
    with TestClient(ai.app) as client:
        response = client.post("/pulse", json={"question": "Сколько?", "snapshot": {"total": 3}})
    assert response.status_code == 403

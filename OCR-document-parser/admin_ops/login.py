"""Interactive one-time ChatGPT device-code login for a Codex SDK worker."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from openai_codex import AsyncCodex


async def login() -> None:
    if os.environ.get("OPS_ROLE") in {"pulse", "fixer"}:
        resolver = Path("/etc/resolv.conf").read_text(encoding="utf-8")
        entries = {line.strip() for line in resolver.splitlines()}
        if (
            not {"nameserver 1.1.1.1", "nameserver 8.8.8.8"}.issubset(entries)
            or "127.0.0.11" in resolver
        ):
            raise RuntimeError("AI resolver is not the VPN-only configuration; refusing Codex login")
    async with AsyncCodex() as codex:
        handle = await codex.login_chatgpt_device_code()
        print(f"Open: {handle.verification_url}", flush=True)
        print(f"Enter one-time code: {handle.user_code}", flush=True)
        await handle.wait()
        print("ChatGPT login completed. Do not copy or commit the Codex auth directory.", flush=True)


if __name__ == "__main__":
    asyncio.run(login())

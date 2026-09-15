"""Interactive one-time ChatGPT device-code login for a Codex SDK worker."""

from __future__ import annotations

import asyncio

from openai_codex import AsyncCodex


async def login() -> None:
    async with AsyncCodex() as codex:
        handle = await codex.login_chatgpt_device_code()
        print(f"Open: {handle.verification_url}", flush=True)
        print(f"Enter one-time code: {handle.user_code}", flush=True)
        await handle.wait()
        print("ChatGPT login completed. Do not copy or commit the Codex auth directory.", flush=True)


if __name__ == "__main__":
    asyncio.run(login())

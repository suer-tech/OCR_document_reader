"""Reverse tool relay: Pulse never connects out to the database/monitoring network."""
from __future__ import annotations

import asyncio
import json
import secrets
from dataclasses import dataclass, field


@dataclass
class ReadJob:
    token: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    calls: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=4))
    waiting: dict = field(default_factory=dict)
    task: asyncio.Task | None = None
    result: dict | None = None
    count: int = 0

    async def ask(self, name: str, arguments: dict) -> dict:
        self.count += 1
        if self.count > 12 or len(self.waiting) >= 4:
            return {"status": "unavailable", "reason": "tool_budget_exceeded"}
        call_id = secrets.token_hex(16)
        future = asyncio.get_running_loop().create_future()
        self.waiting[call_id] = future
        try:
            self.calls.put_nowait({"status": "tool", "call_id": call_id, "name": name, "arguments": arguments})
            return await asyncio.wait_for(future, 35)
        except TimeoutError:
            return {"status": "unavailable", "reason": "reader_timeout"}
        finally:
            self.waiting.pop(call_id, None)

    def deliver(self, call_id: str, result: dict) -> bool:
        if len(json.dumps(result, allow_nan=False)) > 60000:
            raise ValueError("tool output too large")
        future = self.waiting.get(call_id)
        if future is None:
            return False
        if not future.done():
            future.set_result(result)
        return True


class ReadJobs:
    def __init__(self):
        self.jobs: dict[str, ReadJob] = {}

    def start(self, run) -> str:
        if len(self.jobs) >= 8:
            raise ValueError("too many active jobs")
        job_id = secrets.token_hex(16)
        job = ReadJob()
        self.jobs[job_id] = job

        async def execute():
            try:
                async with asyncio.timeout(310):
                    job.result = {"status": "done", **await run(job_id, job.token)}
            except Exception as exc:
                job.result = {"status": "error", "error": type(exc).__name__}

        job.task = asyncio.create_task(execute())
        asyncio.get_running_loop().call_later(420, self.remove, job_id)
        return job_id

    def remove(self, job_id: str):
        job = self.jobs.pop(job_id, None)
        if job:
            if job.task and not job.task.done():
                job.task.cancel()
            for future in job.waiting.values():
                if not future.done():
                    future.cancel()

    async def poll(self, job_id: str) -> dict:
        job = self.jobs[job_id]
        if job.result is not None:
            return job.result
        try:
            return await asyncio.wait_for(job.calls.get(), 1)
        except TimeoutError:
            return job.result or {"status": "running"}

"""Real SDK HH edit-tools smoke with denied Linux namespaces, without inference."""
import asyncio
import ctypes
import errno
import json
import subprocess
import tempfile
from pathlib import Path

from openai_codex import ApprovalMode, AsyncCodex, CodexConfig, Sandbox
from openai_codex.generated.v2_all import (
    ListMcpServerStatusResponse,
    McpServerToolCallResponse,
)

from admin_ops.ai import code_config, collect_proposal
from admin_ops.code_read import CODE_TOOLS
from admin_ops.code_write import WRITE_TOOLS


async def main():
    libc = ctypes.CDLL(None, use_errno=True)
    assert libc.unshare(0x10000000) == -1 and ctypes.get_errno() == errno.EPERM
    with tempfile.TemporaryDirectory(prefix="hh-write-sdk-", ignore_cleanup_errors=True) as folder:
        base = Path(folder)
        root = base / "repo"
        relative = "web/server/example.ts"
        source = root / relative
        source.parent.mkdir(parents=True)
        source.write_text("export const value = 1;\n", encoding="utf-8")
        def git(*args):
            return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout.strip()
        git("init")
        git("add", ".")
        git("-c", "user.name=Synthetic smoke", "-c", "user.email=smoke@example.invalid", "commit", "-m", "synthetic source")
        sha = git("rev-parse", "HEAD")
        manifest = base / "inventory.json"
        manifest.write_text(json.dumps([relative]), encoding="utf-8")
        (base / "codex").mkdir()
        async with AsyncCodex(CodexConfig(env={"CODEX_HOME": str(base / "codex")})) as codex:
            thread = await codex.thread_start(cwd=str(root), ephemeral=True, sandbox=Sandbox.read_only,
                approval_mode=ApprovalMode.deny_all, config=code_config(root, manifest, "hh", writable=True))
            found = None
            for _ in range(20):
                response = await codex._client.request("mcpServerStatus/list", {"threadId": thread.id}, response_model=ListMcpServerStatusResponse)
                found = next((item for item in response.data if item.name == "ocr_code" and item.tools), None)
                if found:
                    break
                await asyncio.sleep(1)
            assert found and set(found.tools) == set(CODE_TOOLS + WRITE_TOOLS)
            async def call(tool, args):
                response = await codex._client.request("mcpServer/tool/call", {"threadId": thread.id, "server": "ocr_code", "tool": tool, "arguments": args}, response_model=McpServerToolCallResponse)
                assert not response.is_error
                return response.structured_content or json.loads(response.content[0]["text"])
            result = await call("replace_code_text", {"path": relative, "old_text": "value = 1", "new_text": "value = 2"})
            assert result["status"] == "ok" and source.read_text() == "export const value = 2;\n"
            for number in range(4):
                result = await call("write_code_file", {"path": f"web/tests/synthetic-{number}.mjs", "content": "export const synthetic = true;\n"})
                assert result["status"] == "ok"
            assert (await call("read_code_file", {"path": "web/tests/synthetic-0.mjs"}))["status"] == "ok"
            assert (await call("write_code_file", {"path": "web/tests/sixth.mjs", "content": "export {};\n"}))["status"] == "unavailable"
            assert (await call("write_code_file", {"path": "../../etc/passwd", "content": "synthetic"}))["status"] == "unavailable"
            proposal = collect_proposal(root, sha, "Synthetic SDK smoke", "hh")
            assert len(proposal["files"]) == 5 and "value = 2" in proposal["diff"]
            print("HH SDK write smoke passed: bounded file creation/replacement/read, real source diff, no shell, denied namespaces, no login/inference")


if __name__ == "__main__":
    asyncio.run(asyncio.wait_for(main(), 90))

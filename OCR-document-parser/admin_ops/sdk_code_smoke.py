"""Real SDK source-reader smoke in restricted Linux Docker, without inference/login."""
import asyncio
import ctypes
import errno
import json
import tempfile
from pathlib import Path

from openai_codex import ApprovalMode, AsyncCodex, CodexConfig, Sandbox
from openai_codex.generated.v2_all import ListMcpServerStatusResponse, McpServerToolCallResponse

from admin_ops.ai import ANALYZE_INSTRUCTIONS, code_config
from admin_ops.code_read import CODE_TOOLS


async def main():
    # Reproduce the production boundary: the fix must work even with user namespaces forbidden.
    libc = ctypes.CDLL(None, use_errno=True)
    assert libc.unshare(0x10000000) == -1 and ctypes.get_errno() == errno.EPERM, "Expected namespace creation to be denied"
    # The disposable CI container owns final cleanup if SDK background plugin writes race it.
    with tempfile.TemporaryDirectory(prefix="code-sdk-smoke-", ignore_cleanup_errors=True) as folder:
        base = Path(folder)
        root = base / "repo"
        relative = "OCR-document-parser/src/example.py"
        source = root / relative
        source.parent.mkdir(parents=True)
        source.write_text("DEFAULT_ADVANCE_DAYS = 10\nearly_report_deadline = None\n", encoding="utf-8")
        manifest = base / "inventory.json"
        manifest.write_text(json.dumps([relative]), encoding="utf-8")
        (base / "codex").mkdir()
        async with AsyncCodex(CodexConfig(env={"CODEX_HOME": str(base / "codex")})) as codex:
            thread = await codex.thread_start(
                cwd=str(root), ephemeral=True, sandbox=Sandbox.read_only,
                approval_mode=ApprovalMode.deny_all, developer_instructions=ANALYZE_INSTRUCTIONS,
                config=code_config(root, manifest),
            )
            found = None
            for _ in range(20):
                response = await codex._client.request(
                    "mcpServerStatus/list", {"threadId": thread.id}, response_model=ListMcpServerStatusResponse,
                )
                found = next((item for item in response.data if item.name == "ocr_code" and item.tools), None)
                if found:
                    break
                await asyncio.sleep(1)
            assert found and set(found.tools) == set(CODE_TOOLS), "Missing code reader tools"

            async def call(tool, args):
                response = await codex._client.request(
                    "mcpServer/tool/call", {"threadId": thread.id, "server": "ocr_code", "tool": tool, "arguments": args},
                    response_model=McpServerToolCallResponse,
                )
                assert not response.is_error, response
                return response.structured_content or json.loads(response.content[0]["text"])

            assert (await call("list_code_files", {}))["files"] == [relative]
            found = await call("search_code", {"query": "early_report_deadline"})
            assert found["matches"][0]["line"] == 2
            lines = await call("read_code_file", {"path": relative})
            assert lines["lines"][0]["text"] == "DEFAULT_ADVANCE_DAYS = 10"
            assert (await call("read_code_file", {"path": "../../etc/passwd"}))["status"] == "unavailable"
            assert source.read_text(encoding="utf-8") == "DEFAULT_ADVANCE_DAYS = 10\nearly_report_deadline = None\n"
            print("Codex code-reader smoke passed: list/search/read via MCP with user namespaces denied; no login/inference")


if __name__ == "__main__":
    asyncio.run(asyncio.wait_for(main(), 90))

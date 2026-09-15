"""Check pinned SDK/runtime MCP discovery and invocation without login or inference.

Run in the disposable CI admin image. No production credentials or backend reads.
"""
import asyncio
import json
import tempfile

from openai_codex import ApprovalMode, AsyncCodex, CodexConfig, Sandbox
from openai_codex.generated.v2_all import ListMcpServerStatusResponse, McpServerToolCallResponse

from admin_ops.ai import PULSE_INSTRUCTIONS, pulse_config
from admin_ops.read_tools import TOOL_MODELS


async def main():
    with tempfile.TemporaryDirectory(prefix="pulse-sdk-smoke-") as folder:
        async with AsyncCodex(CodexConfig(env={"CODEX_HOME": folder})) as codex:
            thread = await codex.thread_start(
                cwd=folder, ephemeral=True, sandbox=Sandbox.read_only,
                approval_mode=ApprovalMode.deny_all, developer_instructions=PULSE_INSTRUCTIONS,
                config=pulse_config("a" * 32, "synthetic-test-capability"),
            )
            # Low-level pinned SDK transport is used only by this integration test.
            found = None
            for _ in range(20):
                response = await codex._client.request(
                    "mcpServerStatus/list", {"threadId": thread.id}, response_model=ListMcpServerStatusResponse,
                )
                found = next((item for item in response.data if item.name == "ocr_read" and item.tools), None)
                if found:
                    break
                await asyncio.sleep(1)
            assert found is not None, "MCP tools were not discovered by the pinned Codex runtime"
            assert set(found.tools) == set(TOOL_MODELS), found.tools
            response = await codex._client.request(
                "mcpServer/tool/call", {"threadId": thread.id, "server": "ocr_read", "tool": "system_overview", "arguments": {}},
                response_model=McpServerToolCallResponse,
            )
            # The synthetic job has no reader HTTP server: safe unavailability is expected.
            assert not response.is_error, response
            result = response.structured_content
            if result is None:
                result = json.loads(response.content[0]["text"])
            assert result["status"] == "unavailable", result
            print("Pinned Codex SDK MCP smoke passed: four read-only tools discovered and invoked; no login/inference")


if __name__ == "__main__":
    asyncio.run(asyncio.wait_for(main(), 90))

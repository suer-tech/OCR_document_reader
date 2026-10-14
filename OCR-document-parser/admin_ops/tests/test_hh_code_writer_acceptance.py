"""Independent acceptance of the bounded HH file MCP in namespace-denied hosts."""
import asyncio
import importlib
import json
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from mcp.server.fastmcp.exceptions import ToolError

from admin_ops import ai
from admin_ops.config import OpsSettings

SOURCE = "web/server/candidate-pipeline/synthetic.ts"
ORIGINAL = "// keep first line\nexport const organizational = 'missing';\n// keep final line\n"
UPDATED = "// keep first line\nexport const organizational = 'from materials';\n// keep final line\n"


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def isolated_source(tmp_path):
    seed = tmp_path / "seed"
    source = seed / SOURCE
    source.parent.mkdir(parents=True)
    source.write_text(ORIGINAL, encoding="utf-8")
    git(seed, "init", "-b", "production")
    git(seed, "add", ".")
    git(seed, "-c", "user.name=Synthetic", "-c", "user.email=synthetic@example.invalid", "commit", "-m", "synthetic source")
    clone = tmp_path / "isolated-clone"
    subprocess.run(["git", "clone", "--local", str(seed), str(clone)], check=True, capture_output=True)
    return seed, clone


def writer(root):
    module = importlib.import_module("admin_ops.code_write")
    return module.CodeWriter(root, git(root, "ls-files", "-z").split("\0")[:-1])


def test_namespace_denied_hh_fix_uses_bounded_file_tools_and_produces_real_proposal(monkeypatch, tmp_path, isolated_source):
    seed, _ = isolated_source
    settings = OpsSettings(role="fixer", internal_token="x" * 32, work_root=str(tmp_path))
    monkeypatch.setattr(ai, "get_ops_settings", lambda: settings)
    seen = []

    def clone(repo, branch, root):
        assert repo == "suer-tech/ai-candidate-screening" and branch == "production"
        subprocess.run(["git", "clone", "--local", str(seed), str(root)], check=True, capture_output=True)

    async def namespace_denied_runner(**kwargs):
        seen.append(kwargs)
        # This reproduces the host restriction at the runner boundary without
        # calling a real provider or allowing a shell to touch source files.
        if (kwargs.get("config") or {}).get("features.shell_tool") is not False:
            return "bwrap: No permissions to create a new namespace"
        config = kwargs["config"]["mcp_servers.ocr_code"]
        assert {"write_code_file", "replace_code_text"}.issubset(config["enabled_tools"])
        assert kwargs["sandbox"] == "read_only", "native SDK writes must remain disabled"
        controlled = writer(Path(kwargs["cwd"]))
        controlled.replace_code_text(SOURCE, "'missing'", "'from materials'")
        return "Synthetic bounded MCP fix"

    monkeypatch.setattr(ai, "clone_source", clone)
    monkeypatch.setattr(ai, "run_codex", namespace_denied_runner)
    with TestClient(ai.app) as client:
        response = client.post("/fix", headers={"X-Ops-Token": "x" * 32}, json={"target": "hh", "request": "Fill synthetic organizational conditions"})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result.get("files") == {SOURCE: UPDATED}, (
        "HH fix must produce the bounded source diff when shell namespaces are unavailable; " + result.get("summary", "")
    )
    assert result["target"] == "hh"
    assert "-'missing'" not in result["diff"]  # replacement is part of an unchanged source line
    assert "+export const organizational = 'from materials';" in result["diff"]
    assert seen and not Path(seen[0]["cwd"]).exists()
    assert (seed / SOURCE).read_text(encoding="utf-8") == ORIGINAL, "runner must never change the original source"


def test_exact_patch_preserves_all_other_text_and_original_clone(isolated_source):
    seed, clone = isolated_source
    controlled = writer(clone)
    result = controlled.replace_code_text(SOURCE, "'missing'", "'from materials'")
    assert isinstance(result, dict)
    assert (clone / SOURCE).read_text(encoding="utf-8") == UPDATED
    assert (seed / SOURCE).read_text(encoding="utf-8") == ORIGINAL
    collected = ai.collect_proposal(clone, git(clone, "rev-parse", "HEAD"), "synthetic patch", target="hh")
    assert collected["files"] == {SOURCE: UPDATED}


@pytest.mark.parametrize("old_text", ["absent fragment", "keep", ""])
def test_missing_ambiguous_or_empty_patch_anchor_rejected_before_mutation(isolated_source, old_text):
    _, clone = isolated_source
    controlled = writer(clone)
    with pytest.raises(ValueError):
        controlled.replace_code_text(SOURCE, old_text, "replacement")
    assert (clone / SOURCE).read_text(encoding="utf-8") == ORIGINAL
    assert git(clone, "status", "--porcelain") == ""


def test_write_new_source_and_repeated_edit_count_as_one_distinct_file(isolated_source):
    _, clone = isolated_source
    controlled = writer(clone)
    for index in range(5):
        result = controlled.write_code_file(f"web/tests/synthetic{index}.test.ts", f"export const value = {index};\n")
        assert isinstance(result, dict)
    # Changing an existing permitted file again does not consume a sixth slot.
    controlled.write_code_file("web/tests/synthetic0.test.ts", "export const value = 99;\n")
    sixth = clone / "web/tests/synthetic5.test.ts"
    with pytest.raises(ValueError):
        controlled.write_code_file("web/tests/synthetic5.test.ts", "export {};\n")
    assert not sixth.exists()
    assert (clone / "web/tests/synthetic0.test.ts").read_text(encoding="utf-8") == "export const value = 99;\n"


@pytest.mark.parametrize("path", [
    "../outside.ts", "/web/server/example.ts", "C:/web/server/example.ts",
    "web\\server\\example.ts", "web/server/./example.ts", "web/server/../../example.ts",
    "web/server/secrets/key.json", "web/.runtime/credentials/key.json", "web/server/candidate/person.json",
    "web/tests/fixtures/candidate.json", "web/tests/acceptance/evidence/result.json",
    "web/package.json", "web/scripts/hh-ops-policy.mjs", "deploy/ops/deploy-approved.sh",
    ".github/workflows/hh-production-deploy.yml", "web/server/example.py",
])
def test_writer_rejects_protected_or_escaped_paths_before_effect(isolated_source, path):
    _, clone = isolated_source
    controlled = writer(clone)
    with pytest.raises(ValueError):
        controlled.write_code_file(path, "synthetic content")
    assert git(clone, "status", "--porcelain", "--untracked-files=all") == ""
    assert (clone / SOURCE).read_text(encoding="utf-8") == ORIGINAL


@pytest.mark.parametrize("content", ["x" * 500001, "я" * 250001, "synthetic\0binary"], ids=["oversized-ascii", "oversized-utf8", "binary-nul"])
def test_writer_rejects_oversized_or_binary_existing_content_before_effect(isolated_source, content):
    _, clone = isolated_source
    controlled = writer(clone)
    with pytest.raises(ValueError):
        controlled.write_code_file(SOURCE, content)
    assert (clone / SOURCE).read_text(encoding="utf-8") == ORIGINAL


def test_invalid_json_does_not_create_file_or_consume_a_distinct_slot(isolated_source):
    _, clone = isolated_source
    controlled = writer(clone)
    with pytest.raises(ValueError):
        controlled.write_code_file("web/tests/synthetic.json", '{"unfinished":')
    assert not (clone / "web/tests/synthetic.json").exists()
    for index in range(5):
        controlled.write_code_file(f"web/tests/synthetic{index}.json", json.dumps({"synthetic": index}))
    assert len(list((clone / "web/tests").glob("*.json"))) == 5


def test_writer_rejects_non_git_root(tmp_path):
    module = importlib.import_module("admin_ops.code_write")
    plain = tmp_path / "not-a-clone"
    plain.mkdir()
    with pytest.raises(ValueError):
        module.CodeWriter(plain, [])


@pytest.mark.parametrize("kind", ["file", "parent"])
def test_writer_cannot_follow_file_or_parent_symlink(isolated_source, tmp_path, kind):
    _, clone = isolated_source
    outside = tmp_path / "outside-source"
    outside.mkdir()
    external = outside / "synthetic.ts"
    external.write_text("synthetic outside must survive\n", encoding="utf-8")
    if kind == "file":
        link = clone / SOURCE
        link.unlink()
        destination = external
        path = SOURCE
    else:
        link = clone / "web/tests/linked"
        link.parent.mkdir(parents=True)
        destination = outside
        path = "web/tests/linked/synthetic.ts"
    try:
        link.symlink_to(destination, target_is_directory=kind == "parent")
    except OSError:
        pytest.skip("host cannot create synthetic symlinks")
    controlled = writer(clone)
    with pytest.raises(ValueError):
        controlled.write_code_file(path, "overwrite attempt\n")
    assert external.read_text(encoding="utf-8") == "synthetic outside must survive\n"


def tool_result(value):
    if isinstance(value, dict):
        return value
    if isinstance(value, tuple) and isinstance(value[-1], dict):
        return value[-1]
    for block in value:
        if getattr(block, "type", None) == "text":
            return json.loads(block.text)
    pytest.fail("MCP returned no structured tool result")


@pytest.mark.parametrize("target,writable", [("hh", False), ("ocr", False), ("ocr", True)])
def test_read_only_or_ocr_mcp_never_exposes_write_tools(isolated_source, target, writable):
    _, clone = isolated_source
    mcp_module = importlib.import_module("admin_ops.mcp_code")
    if target != "hh" and writable:
        with pytest.raises(ValueError):
            mcp_module.create_server(clone, [SOURCE], target, writable=writable)
        assert (clone / SOURCE).read_text(encoding="utf-8") == ORIGINAL
        return
    server = mcp_module.create_server(clone, [SOURCE], target, writable=writable)

    async def run():
        tools = await server.list_tools()
        names = {tool.name for tool in tools}
        assert names == {"list_code_files", "search_code", "read_code_file"}
        with pytest.raises(ToolError):
            await server.call_tool("write_code_file", {"path": SOURCE, "content": UPDATED})
        assert (clone / SOURCE).read_text(encoding="utf-8") == ORIGINAL
    asyncio.run(run())


def test_trusted_hh_write_mcp_edits_and_can_read_back_existing_and_new_files(isolated_source):
    _, clone = isolated_source
    mcp_module = importlib.import_module("admin_ops.mcp_code")
    server = mcp_module.create_server(clone, [SOURCE], "hh", writable=True)

    async def run():
        tools = {tool.name: tool for tool in await server.list_tools()}
        assert set(tools) == {"list_code_files", "search_code", "read_code_file", "write_code_file", "replace_code_text"}
        assert set(tools["write_code_file"].inputSchema["properties"]) == {"path", "content"}
        assert set(tools["replace_code_text"].inputSchema["properties"]) == {"path", "old_text", "new_text"}
        patched = tool_result(await server.call_tool("replace_code_text", {"path": SOURCE, "old_text": "'missing'", "new_text": "'from materials'"}))
        assert patched.get("status", "ok") == "ok"
        read = tool_result(await server.call_tool("read_code_file", {"path": SOURCE, "start_line": 2, "line_count": 1}))
        assert read["lines"] == [{"line": 2, "text": "export const organizational = 'from materials';"}]
        new_path = "web/tests/new-synthetic.test.ts"
        created = tool_result(await server.call_tool("write_code_file", {"path": new_path, "content": "export const synthetic = true;\n"}))
        assert created.get("status", "ok") == "ok"
        read = tool_result(await server.call_tool("read_code_file", {"path": new_path}))
        assert read["lines"][0]["text"] == "export const synthetic = true;"
        listed = tool_result(await server.call_tool("list_code_files", {}))
        assert new_path in listed["files"]
        with pytest.raises(ToolError):
            await server.call_tool("run_command", {"command": "touch /tmp/untrusted"})
    asyncio.run(run())


def test_write_config_is_bound_to_trusted_hh_target_and_does_not_allow_sdk_shell(tmp_path):
    root = tmp_path / "clone"
    root.mkdir()
    manifest = tmp_path / "code-files.json"
    manifest.write_text("[]", encoding="utf-8")
    for target, writable in [("hh", True), ("hh", False), ("ocr", False), ("ocr", True)]:
        if target != "hh" and writable:
            with pytest.raises(ValueError):
                ai.code_config(root, manifest, target, writable=writable)
            continue
        config = ai.code_config(root, manifest, target, writable=writable)
        assert config["features.shell_tool"] is False
        assert config["web_search"] == "disabled"
        assert config["agents.enabled"] is False
        server = config["mcp_servers.ocr_code"]
        enabled = set(server["enabled_tools"])
        if target == "hh" and writable:
            assert server["env"]["OPS_CODE_WRITE"] == "true"
            assert enabled == {"list_code_files", "search_code", "read_code_file", "write_code_file", "replace_code_text"}
        else:
            assert server["env"].get("OPS_CODE_WRITE") != "true"
            assert enabled == {"list_code_files", "search_code", "read_code_file"}
        assert server["env"]["OPS_CODE_TARGET"] == target

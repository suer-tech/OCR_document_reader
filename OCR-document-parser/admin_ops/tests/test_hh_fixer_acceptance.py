"""Independent acceptance for explicit candidate-service fixes in the admin bot.

Synthetic Telegram updates, local SQLite and temporary Git repositories only.
GitHub, Codex and worker transports are isolated at their external boundaries.
"""
import asyncio
import sqlite3
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from admin_ops import ai, release
from admin_ops import bot as bot_module
from admin_ops.bot import AdminBot
from admin_ops.code_read import CodeReader
from admin_ops.config import OpsSettings
from admin_ops.paths import project_path, repo_path

HH_REPO = "suer-tech/ai-candidate-screening"
HH_BRANCH = "production"
BASE_SHA = "a" * 40
HEAD_SHA = "b" * 40
MERGE_SHA = "c" * 40


def message(text, *, admin=123, chat_type="private"):
    return {"message": {"from": {"id": admin}, "chat": {"id": admin, "type": chat_type}, "text": text}}


def proposal(**overrides):
    return {
        "base_sha": BASE_SHA, "summary": "Synthetic organizational conditions fix",
        "files": {"web/server/candidate-pipeline/example.ts": "export const value = 2;\n"},
        "diff": "synthetic diff", "checks": {}, **overrides,
    }


def only_proposal_id(state):
    with sqlite3.connect(state) as connection:
        rows = connection.execute("SELECT id FROM proposals").fetchall()
    assert len(rows) == 1, "explicit /fix_hh must save exactly one reviewable proposal"
    return rows[0][0]


def test_fix_hh_routes_to_candidate_target_and_persists_identity(tmp_path):
    state = str(tmp_path / "state.sqlite3")
    bot_module.init_state(state)

    async def run():
        bot = AdminBot(OpsSettings(telegram_token="synthetic", admin_ids="123", state_path=state))
        calls, sent = [], []

        async def send(admin, text):
            sent.append(text)

        async def worker(url, payload):
            calls.append((url, payload))
            return proposal(target="hh", repository=HH_REPO, branch=HH_BRANCH)

        bot.send, bot.worker = send, worker
        try:
            await bot.handle(message("/fix_hh Заполни организационные моменты"))
            if bot.dialog_tasks:
                await asyncio.gather(*bot.dialog_tasks.values())
            assert calls == [(bot.settings.fixer_url + "/fix", {"request": "Заполни организационные моменты", "target": "hh"})], (
                "candidate command must reach the fixer with target=hh; " + " | ".join(sent)
            )
        finally:
            await bot.aclose()

    asyncio.run(run())
    saved = bot_module.get_proposal(state, only_proposal_id(state))[0]
    assert saved["target"] == "hh"
    assert saved["repository"] == HH_REPO
    assert saved["branch"] == HH_BRANCH


def test_hh_commands_ignore_nonadmins_and_groups_and_plain_text_cannot_fix(tmp_path):
    state = str(tmp_path / "state.sqlite3")
    bot_module.init_state(state)

    async def run():
        bot = AdminBot(OpsSettings(telegram_token="synthetic", admin_ids="123", state_path=state))
        calls, sent = [], []

        async def send(admin, text):
            sent.append(text)

        async def worker(url, payload):
            calls.append((url, payload))
            return {"answer": "synthetic read-only dialogue", "snapshot": {}}

        async def no_typing(admin):
            await asyncio.Event().wait()

        bot.send, bot.worker, bot.show_typing = send, worker, no_typing
        async def synthetic_snapshot():
            return {"synthetic": True}

        bot.snapshot = synthetic_snapshot
        try:
            for command in ["/fix_hh fix", "/analyze_hh explain"]:
                await bot.handle(message(command, admin=999))
                await bot.handle(message(command, chat_type="group"))
            assert calls == sent == []
            # A conversational request, however imperative, cannot select the fixer.
            await bot.handle(message("Исправь код кандидатов и выпусти его"))
            if bot.dialog_tasks:
                await asyncio.gather(*bot.dialog_tasks.values())
            assert all(not url.endswith("/fix") for url, _ in calls)
        finally:
            await bot.aclose()
    asyncio.run(run())
    with sqlite3.connect(state) as connection:
        assert connection.execute("SELECT COUNT(*) FROM proposals").fetchone()[0] == 0


def test_legacy_fix_remains_ocr_and_worker_cannot_redirect_it(tmp_path):
    state = str(tmp_path / "state.sqlite3")
    bot_module.init_state(state)

    async def run():
        bot = AdminBot(OpsSettings(telegram_token="synthetic", admin_ids="123", state_path=state))
        calls = []

        async def send(*args):
            pass

        async def worker(url, payload):
            calls.append((url, payload))
            return proposal(files={"src/ocr_platform/example.py": "VALUE = 2\n"},
                            target="hh", repository="attacker/other", branch="attacker")

        bot.send, bot.worker = send, worker
        try:
            await bot.handle(message("/fix Synthetic OCR task"))
            if bot.dialog_tasks:
                await asyncio.gather(*bot.dialog_tasks.values())
            assert len(calls) == 1
            assert calls[0][0] == bot.settings.fixer_url + "/fix"
            assert calls[0][1].get("target", "ocr") == "ocr"
        finally:
            await bot.aclose()
    asyncio.run(run())
    with sqlite3.connect(state) as connection:
        ids = [row[0] for row in connection.execute("SELECT id FROM proposals").fetchall()]
    # Rejection is safe, as is binding the response to the requested OCR target.
    for proposal_id in ids:
        saved = bot_module.get_proposal(state, proposal_id)[0]
        assert saved.get("target", "ocr") == "ocr"
        assert saved.get("repository", OpsSettings().github_repo) == OpsSettings().github_repo
        assert saved.get("branch", OpsSettings().github_base_branch) == OpsSettings().github_base_branch


def test_hh_worker_response_cannot_choose_another_target_or_repository(tmp_path):
    state = str(tmp_path / "state.sqlite3")
    bot_module.init_state(state)

    async def run():
        bot = AdminBot(OpsSettings(telegram_token="synthetic", admin_ids="123", state_path=state))
        calls = []

        async def send(*args):
            pass

        async def worker(url, payload):
            calls.append(payload)
            return proposal(target="ocr", repository="attacker/other", branch="attacker")

        bot.send, bot.worker = send, worker
        try:
            await bot.handle(message("/fix_hh Synthetic candidate task"))
            if bot.dialog_tasks:
                await asyncio.gather(*bot.dialog_tasks.values())
            assert calls and calls[0]["target"] == "hh"
        finally:
            await bot.aclose()
    asyncio.run(run())
    with sqlite3.connect(state) as connection:
        ids = [row[0] for row in connection.execute("SELECT id FROM proposals").fetchall()]
    for proposal_id in ids:
        saved = bot_module.get_proposal(state, proposal_id)[0]
        assert saved["target"] == "hh"
        assert saved["repository"] == HH_REPO
        assert saved["branch"] == HH_BRANCH


def test_analyze_hh_routes_read_only_and_never_saves_proposal(monkeypatch, tmp_path):
    async def run():
        bot = AdminBot(OpsSettings(telegram_token="synthetic", admin_ids="123", state_path=str(tmp_path / "state.sqlite3")))
        calls, sent = [], []

        async def send(admin, text):
            sent.append(text)

        async def worker(url, payload):
            calls.append((url, payload))
            return {"answer": "Synthetic candidate source analysis", "base_sha": BASE_SHA,
                    "target": "hh", "repository": HH_REPO, "branch": HH_BRANCH}

        async def no_typing(admin):
            await asyncio.Event().wait()

        bot.send, bot.worker, bot.show_typing = send, worker, no_typing
        monkeypatch.setattr(bot_module, "save_proposal", lambda *args: pytest.fail("read-only analysis created a proposal"))
        try:
            await bot.handle(message("/analyze_hh Explain organizational conditions"))
            if bot.dialog_tasks:
                await asyncio.gather(*bot.dialog_tasks.values())
            assert calls == [(bot.settings.fixer_url + "/analyze", {"request": "Explain organizational conditions", "target": "hh"})]
            assert any(HH_REPO in text and HH_BRANCH in text and BASE_SHA[:12] in text for text in sent)
        finally:
            await bot.aclose()
    asyncio.run(run())


def test_hh_proposal_lifecycle_keeps_target_and_exact_sha(monkeypatch, tmp_path):
    state = str(tmp_path / "state.sqlite3")
    bot_module.init_state(state)
    proposal_id = bot_module.save_proposal(state, proposal(target="hh", repository=HH_REPO, branch=HH_BRANCH))
    calls = []

    async def publish(**kwargs):
        calls.append(("approve", kwargs))
        return {"url": "https://github.com/suer-tech/ai-candidate-screening/pull/7", "number": 7, "head_sha": HEAD_SHA}

    async def merge(**kwargs):
        calls.append(("confirm", kwargs))
        return MERGE_SHA

    async def dispatch(**kwargs):
        calls.append(("deploy", kwargs))
        return {"run_id": 42, "run_url": "https://github.com/suer-tech/ai-candidate-screening/actions/runs/42"}

    async def status(**kwargs):
        calls.append(("status", kwargs))
        return {"status": "in_progress", "run_id": 42}

    monkeypatch.setattr(bot_module, "create_draft_pr", publish)
    monkeypatch.setattr(bot_module, "merge_approved_pr", merge)
    monkeypatch.setattr(bot_module, "dispatch_production_deploy", dispatch)
    monkeypatch.setattr(bot_module, "deployment_result", status)

    async def run():
        bot = AdminBot(OpsSettings(telegram_token="synthetic", admin_ids="123", state_path=state, github_token="synthetic"))

        async def send(*args):
            pass

        bot.send = send
        try:
            await bot.handle(message(f"/approve {proposal_id}"))
            assert [name for name, _ in calls] == ["approve"], "approve must only create the reviewable PR"
            await bot.handle(message(f"/confirm {proposal_id} deadbeef"))
            assert [name for name, _ in calls] == ["approve"], "a different SHA must not authorize merge"
            await bot.handle(message(f"/confirm {proposal_id} {HEAD_SHA[:8]}"))
            await bot.handle(message(f"/confirm {proposal_id} {HEAD_SHA[:8]}"))
            await bot.handle(message(f"/status {proposal_id}"))
            assert [name for name, _ in calls] == ["approve", "confirm", "deploy", "status"]
            for name, kwargs in calls:
                assert kwargs["repo"] == HH_REPO, f"{name} switched to OCR or an untrusted repository"
                if name != "status":
                    assert kwargs["base_branch"] == HH_BRANCH
            assert calls[1][1]["proposal"]["pr_head_sha"] == HEAD_SHA
            assert calls[1][1]["required_check"] == "validate-hh"
            assert calls[2][1]["merge_sha"] == MERGE_SHA
            assert calls[2][1]["workflow"] == "hh-production-deploy.yml"
            assert calls[3][1]["merge_sha"] == MERGE_SHA
            assert calls[3][1]["workflow"] == "hh-production-deploy.yml"
        finally:
            await bot.aclose()
    asyncio.run(run())
    saved = bot_module.get_proposal(state, proposal_id)[0]
    assert saved["target"] == "hh" and saved["merge_sha"] == MERGE_SHA


def test_hh_manual_deploy_uses_stored_target_after_bot_restart(monkeypatch, tmp_path):
    state = str(tmp_path / "state.sqlite3")
    bot_module.init_state(state)
    saved = proposal(target="hh", repository=HH_REPO, branch=HH_BRANCH,
                     pr_number=7, pr_head_sha=HEAD_SHA, merge_sha=MERGE_SHA)
    proposal_id = bot_module.save_proposal(state, saved)
    bot_module.save_proposal_progress(state, proposal_id, saved, "merged_awaiting_deploy", "https://github.com/suer-tech/ai-candidate-screening/pull/7")
    calls = []

    async def dispatch(**kwargs):
        calls.append(kwargs)
        return {"run_id": 42, "run_url": "https://github.com/suer-tech/ai-candidate-screening/actions/runs/42"}

    monkeypatch.setattr(bot_module, "dispatch_production_deploy", dispatch)

    async def run():
        bot = AdminBot(OpsSettings(telegram_token="synthetic", admin_ids="123", state_path=state, github_token="synthetic"))

        async def send(*args):
            pass

        bot.send = send
        try:
            await bot.handle(message(f"/deploy {proposal_id}"))
            assert len(calls) == 1
            assert calls[0]["repo"] == HH_REPO and calls[0]["base_branch"] == HH_BRANCH
            assert calls[0]["workflow"] == "hh-production-deploy.yml"
            assert calls[0]["merge_sha"] == MERGE_SHA
        finally:
            await bot.aclose()
    asyncio.run(run())


def test_target_model_defaults_to_ocr_and_rejects_arbitrary_targets():
    assert ai.FixRequest(request="synthetic").target == "ocr"
    assert ai.FixRequest(request="synthetic", target="hh").target == "hh"
    with pytest.raises(ValidationError):
        ai.FixRequest(request="synthetic", target="attacker/other")


@pytest.mark.parametrize("path", [
    "web/server/candidate-pipeline/example.ts", "web/app/routes/example.tsx",
    "web/tests/example.test.mjs", "web/server/example.js", "web/app/example.json",
])
def test_hh_policy_accepts_only_requested_source_types(path):
    assert ai.validate_change_path(path, target="hh")
    assert repo_path(path, target="hh") == path
    assert project_path(path, target="hh") == path
    assert not ai.validate_change_path(path), "OCR's existing scope must stay separate"


@pytest.mark.parametrize("path", [
    "web/package.json", "web/.runtime/credentials/key.json", "web/server/.env",
    "web/server/secrets/key.json", "web/tests/fixtures/candidate.json", "web/tests/evidence/result.json",
    "web/tests/acceptance/evidence/result.json", "web/server/candidate/person.json",
    "candidate/person.json", "web/tests/data.fixture.pdf", "deploy/ubuntu/install.sh",
    "web/app/../../secret.json", "web\\server\\example.ts", "/web/server/example.ts", "web//server/example.ts",
    "src/ocr_platform/example.py", ".github/workflows/production.yml",
])
def test_hh_policy_rejects_materials_secrets_escape_and_deployment_paths(path):
    assert not ai.validate_change_path(path, target="hh")


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def hh_checkout(tmp_path):
    root = tmp_path / "hh"
    source = root / "web/server/example.ts"
    source.parent.mkdir(parents=True)
    source.write_text("export const value = 1;\n", encoding="utf-8")
    git(root, "init", "-b", HH_BRANCH)
    git(root, "add", ".")
    git(root, "-c", "user.name=Synthetic", "-c", "user.email=synthetic@example.invalid", "commit", "-m", "synthetic base")
    return root, source, git(root, "rev-parse", "HEAD")


def test_hh_proposal_collects_real_diff_and_keeps_root_paths(hh_checkout):
    root, source, base = hh_checkout
    source.write_text("export const value = 2;\n", encoding="utf-8")
    collected = ai.collect_proposal(root, base, "synthetic fix", target="hh")
    assert collected["target"] == "hh"
    assert collected["base_sha"] == base
    assert collected["files"] == {"web/server/example.ts": "export const value = 2;\n"}
    assert "web/server/example.ts" in collected["diff"]
    assert "-export const value = 1;" in collected["diff"]
    assert "+export const value = 2;" in collected["diff"]


def test_hh_proposal_rejects_deletion(hh_checkout):
    root, source, base = hh_checkout
    source.unlink()
    with pytest.raises(ValueError, match="Disallowed"):
        ai.collect_proposal(root, base, "delete", target="hh")


def test_hh_proposal_rejects_six_files(hh_checkout):
    root, _, base = hh_checkout
    for index in range(6):
        (root / f"web/server/new{index}.ts").write_text("export {};\n", encoding="utf-8")
    with pytest.raises(ValueError, match="between 1 and 5"):
        ai.collect_proposal(root, base, "six files", target="hh")


def test_hh_proposal_rejects_oversized_file(hh_checkout):
    root, source, base = hh_checkout
    source.write_text("x" * 500001, encoding="utf-8")
    with pytest.raises(ValueError, match="too large"):
        ai.collect_proposal(root, base, "large", target="hh")


def test_hh_existing_large_runtime_file_allows_a_small_reviewable_diff(hh_checkout):
    root, source, _ = hh_checkout
    text = "export const value = 1;\n" + "// synthetic context\n" * 11000
    source.write_text(text, encoding="utf-8")
    git(root, "add", ".")
    git(root, "-c", "user.name=Synthetic", "-c", "user.email=synthetic@example.invalid", "commit", "-m", "large synthetic runtime")
    base = git(root, "rev-parse", "HEAD")
    changed = text.replace("value = 1", "value = 2", 1)
    source.write_text(changed, encoding="utf-8")
    assert 100000 < source.stat().st_size < 500000
    collected = ai.collect_proposal(root, base, "small fix in existing large runtime", target="hh")
    assert collected["files"]["web/server/example.ts"] == changed
    assert len(collected["diff"]) < 50000


def test_default_ocr_proposal_keeps_original_100000_byte_file_limit(tmp_path):
    root = tmp_path / "ocr"
    project = root / "OCR-document-parser"
    source = project / "src/ocr_platform/example.py"
    source.parent.mkdir(parents=True)
    source.write_text("VALUE = 1\n", encoding="utf-8")
    git(root, "init", "-b", "main")
    git(root, "add", ".")
    git(root, "-c", "user.name=Synthetic", "-c", "user.email=synthetic@example.invalid", "commit", "-m", "synthetic OCR base")
    source.write_text("# " + "x" * 100001, encoding="utf-8")
    with pytest.raises(ValueError, match="too large"):
        ai.collect_proposal(project, git(root, "rev-parse", "HEAD"), "oversized OCR file")


@pytest.fixture
def hh_worker(monkeypatch, tmp_path):
    settings = OpsSettings(role="fixer", internal_token="x" * 32, work_root=str(tmp_path))
    monkeypatch.setattr(ai, "get_ops_settings", lambda: settings)
    clones = []

    def clone(repo, branch, root):
        clones.append((repo, branch))
        source = root / "web/server/example.ts"
        source.parent.mkdir(parents=True)
        source.write_text("export const value = 1;\n", encoding="utf-8")
        git(root, "init", "-b", branch)
        git(root, "add", ".")
        git(root, "-c", "user.name=Synthetic", "-c", "user.email=synthetic@example.invalid", "commit", "-m", "synthetic base")

    monkeypatch.setattr(ai, "clone_source", clone)
    with TestClient(ai.app) as client:
        yield client, clones


def test_hh_worker_fix_clones_configured_target_and_returns_real_diff(hh_worker, monkeypatch):
    client, clones = hh_worker
    seen = []

    async def codex(**kwargs):
        seen.append(kwargs)
        from admin_ops.code_write import CodeWriter
        config = kwargs["config"]
        assert kwargs["sandbox"] == "read_only"
        assert config["features.shell_tool"] is False
        assert config["mcp_servers.ocr_code"]["env"]["OPS_CODE_WRITE"] == "true"
        controlled = CodeWriter(Path(kwargs["cwd"]), ["web/server/example.ts"])
        controlled.replace_code_text("web/server/example.ts", "value = 1", "value = 2")
        return "synthetic fix"

    monkeypatch.setattr(ai, "run_codex", codex)
    response = client.post("/fix", headers={"X-Ops-Token": "x" * 32}, json={
        "request": "Synthetic candidate fix", "target": "hh", "repository": "attacker/other", "branch": "attacker",
    })
    assert response.status_code == 200, response.text
    result = response.json()
    assert clones == [(HH_REPO, HH_BRANCH)]
    assert result["target"] == "hh" and result["repository"] == HH_REPO and result["branch"] == HH_BRANCH
    assert result["files"] == {"web/server/example.ts": "export const value = 2;\n"}
    assert seen[0]["sandbox"] == "read_only"
    assert not Path(seen[0]["cwd"]).exists(), "isolated candidate checkout must be removed"


def test_hh_worker_analysis_is_read_only_and_rejects_any_checkout_change(hh_worker, monkeypatch):
    client, clones = hh_worker
    seen = []

    async def codex(**kwargs):
        seen.append(kwargs)
        return "web/server/example.ts:1 contains a synthetic constant"

    monkeypatch.setattr(ai, "run_codex", codex)
    monkeypatch.setattr(ai, "collect_proposal", lambda *args, **kwargs: pytest.fail("analysis collected a writable proposal"))
    response = client.post("/analyze", headers={"X-Ops-Token": "x" * 32}, json={"request": "Explain", "target": "hh"})
    assert response.status_code == 200, response.text
    assert clones == [(HH_REPO, HH_BRANCH)]
    assert response.json()["repository"] == HH_REPO and response.json()["target"] == "hh"
    assert "files" not in response.json()
    assert seen[0]["sandbox"] == "read_only"
    assert seen[0]["config"]["features.shell_tool"] is False
    assert seen[0]["config"]["web_search"] == "disabled"

    async def changing_codex(**kwargs):
        (Path(kwargs["cwd"]) / "web/server/example.ts").write_text("export const value = 999;\n", encoding="utf-8")
        return "changed"

    monkeypatch.setattr(ai, "run_codex", changing_codex)
    response = client.post("/analyze", headers={"X-Ops-Token": "x" * 32}, json={"request": "Explain", "target": "hh"})
    assert response.status_code == 409


def test_hh_worker_rejects_missing_auth_and_unknown_target(hh_worker):
    client, clones = hh_worker
    assert client.post("/fix", json={"request": "Fix", "target": "hh"}).status_code == 403
    assert client.post("/fix", headers={"X-Ops-Token": "x" * 32}, json={"request": "Fix", "target": "attacker"}).status_code == 422
    assert clones == []


def test_hh_analysis_reader_exposes_numbered_source_without_candidate_materials(tmp_path):
    source = "web/server/candidate-pipeline/example.ts"
    blocked = [
        "web/.runtime/credentials/key.json", "web/server/secrets/key.json",
        "web/server/candidate/person.json", "web/tests/fixtures/candidate.json",
        "web/tests/acceptance/evidence/result.json",
    ]
    for path in [source, *blocked]:
        file = tmp_path / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text("export const organizationalConditions = 1;\n", encoding="utf-8")
    reader = CodeReader(tmp_path, [source, *blocked], target="hh")
    assert reader.list_code_files()["files"] == [source]
    found = reader.search_code("organizationalConditions")
    assert found["matches"] == [{"path": source, "line": 1, "text": "export const organizationalConditions = 1;"}]
    assert reader.read_code_file(source, start_line=1, line_count=1)["lines"] == [{"line": 1, "text": "export const organizationalConditions = 1;"}]
    for path in blocked:
        with pytest.raises(ValueError):
            reader.read_code_file(path)


def test_target_settings_are_fixed_configuration_and_do_not_mutate_ocr_defaults():
    settings = OpsSettings()
    assert settings.hh_github_repo == HH_REPO
    assert settings.hh_github_base_branch == HH_BRANCH
    scoped = settings.for_target("hh")
    assert scoped.github_repo == HH_REPO
    assert scoped.github_base_branch == HH_BRANCH
    assert scoped.github_required_check == "validate-hh"
    assert scoped.github_deploy_workflow == "hh-production-deploy.yml"
    assert settings.github_repo == "suer-tech/OCR_document_reader"
    assert settings.github_base_branch == "main"


@pytest.mark.parametrize("conclusion", ["failure", "cancelled", None])
def test_hh_release_refuses_non_green_ci_before_any_github_mutation(monkeypatch, conclusion):
    proposal_id = "abcdef12"
    saved = proposal(target="hh", repository=HH_REPO, branch=HH_BRANCH, pr_number=7, pr_head_sha=HEAD_SHA)
    calls = []
    pr = {
        "number": 7, "state": "open", "merged": False, "draft": True, "node_id": "synthetic-node",
        "head": {"ref": f"codex/ops-{proposal_id}", "sha": HEAD_SHA, "repo": {"full_name": HH_REPO}},
        "base": {"ref": HH_BRANCH},
    }

    class Response:
        status_code = 200
        content = b"{}"

        def __init__(self, payload):
            self.payload = payload

        def json(self):
            return self.payload

    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def request(self, method, url, **kwargs):
            calls.append((method, url, kwargs))
            assert method == "GET", "a failed CI gate attempted an external write"
            assert url.startswith(f"https://api.github.com/repos/{HH_REPO}/")
            if url.endswith("/pulls/7"):
                return Response(pr)
            if url.endswith(f"/git/ref/heads/{HH_BRANCH}"):
                return Response({"object": {"sha": BASE_SHA}})
            if url.endswith("/pulls/7/files"):
                return Response([{"filename": "web/server/candidate-pipeline/example.ts", "status": "modified"}])
            if url.endswith(f"/commits/{HEAD_SHA}/check-runs"):
                assert kwargs["params"]["check_name"] == "validate-hh"
                return Response({"check_runs": [{"name": "validate-hh", "app": {"slug": "github-actions"}, "status": "completed" if conclusion else "in_progress", "conclusion": conclusion}]})
            pytest.fail(f"unexpected GitHub URL: {url}")

    monkeypatch.setattr(release.httpx, "AsyncClient", Client)
    with pytest.raises(release.ReleaseError, match="has not passed"):
        asyncio.run(release.merge_approved_pr(proposal_id=proposal_id, proposal=saved, token="synthetic",
                    repo=HH_REPO, base_branch=HH_BRANCH, required_check="validate-hh"))
    assert len(calls) == 4


def test_hh_credential_scope_uses_dedicated_token_without_mutating_settings():
    settings = OpsSettings(github_token="synthetic-ocr-token", hh_github_token="synthetic-hh-token")
    before = settings.model_dump()
    selected = settings.for_target("hh")
    assert selected.github_token == "synthetic-hh-token"
    assert selected.github_repo == HH_REPO
    assert selected.github_base_branch == HH_BRANCH
    assert settings.model_dump() == before


def test_hh_credential_scope_preserves_legacy_token_fallback_when_unconfigured():
    settings = OpsSettings(github_token="synthetic-legacy-token", hh_github_token="")
    before = settings.model_dump()
    selected = settings.for_target("hh")
    assert selected.github_token == "synthetic-legacy-token"
    assert selected.github_repo == HH_REPO
    assert settings.model_dump() == before


def test_ocr_credential_scope_keeps_original_token_even_when_hh_token_is_set():
    settings = OpsSettings(github_token="synthetic-ocr-token", hh_github_token="synthetic-hh-token")
    before = settings.model_dump()
    selected = settings.for_target("ocr")
    assert selected.github_token == "synthetic-ocr-token"
    assert selected.github_repo == "suer-tech/OCR_document_reader"
    assert selected.github_base_branch == "main"
    assert settings.model_dump() == before

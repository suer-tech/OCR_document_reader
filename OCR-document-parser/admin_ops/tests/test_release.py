import asyncio

import pytest

from admin_ops import release
from admin_ops.verify_release import verify_release

BASE_SHA = "a" * 40
HEAD_SHA = "b" * 40
MERGE_SHA = "c" * 40
PROPOSAL = {
    "base_sha": BASE_SHA,
    "pr_head_sha": HEAD_SHA,
    "pr_number": 7,
    "files": {"src/ocr_platform/example.py": "VALUE = 2\n"},
}


def pr(*, draft: bool) -> dict:
    return {
        "number": 7, "node_id": "PR-node", "state": "open", "merged": False,
        "draft": draft, "mergeable": not draft,
        "mergeable_state": "draft" if draft else "clean",
        "head": {"ref": "codex/ops-abcdef12", "sha": HEAD_SHA, "repo": {"full_name": "owner/repo"}},
        "base": {"ref": "main"},
    }


def fake_http(monkeypatch, replies, calls, client_headers=None):
    class FakeResponse:
        status_code = 200
        content = b"{}"

        def __init__(self, payload):
            self.payload = payload

        def json(self):
            return self.payload

    class FakeClient:
        def __init__(self, **kwargs):
            self.headers = kwargs.get("headers") or {}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def request(self, method, url, **kwargs):
            calls.append((method, url, kwargs.get("json")))
            if client_headers is not None:
                client_headers[url] = self.headers
            return FakeResponse(next(replies))

    monkeypatch.setattr(release.httpx, "AsyncClient", FakeClient)


def test_merge_requires_exact_head_successful_ci_and_clean_pr(monkeypatch) -> None:
    calls = []
    client_headers = {}
    replies = iter(
        [
            pr(draft=True),
            {"object": {"sha": BASE_SHA}},
            [{"filename": "OCR-document-parser/src/ocr_platform/example.py", "status": "modified"}],
            {"check_runs": [{"name": "validate", "app": {"slug": "github-actions"},
                             "status": "completed", "conclusion": "success"}]},
            {"data": {"markPullRequestReadyForReview": {"pullRequest": {"isDraft": False}}}},
            pr(draft=False),
            {"object": {"sha": BASE_SHA}},
            {"merged": True, "sha": MERGE_SHA},
        ]
    )
    fake_http(monkeypatch, replies, calls, client_headers)
    result = asyncio.run(
        release.merge_approved_pr(
            proposal_id="abcdef12", proposal=PROPOSAL, token="fake", repo="owner/repo",
            base_branch="main", required_check="validate",
        )
    )
    assert result == MERGE_SHA
    check_url = f"https://api.github.com/repos/owner/repo/commits/{HEAD_SHA}/check-runs"
    assert "Authorization" not in client_headers[check_url]
    assert any("/graphql" in call[1] for call in calls)
    assert calls[-1][0] == "PUT"
    assert calls[-1][2]["sha"] == HEAD_SHA


def test_merge_refuses_failed_ci_before_any_mutation(monkeypatch) -> None:
    calls = []
    replies = iter(
        [
            pr(draft=True),
            {"object": {"sha": BASE_SHA}},
            [{"filename": "OCR-document-parser/src/ocr_platform/example.py", "status": "modified"}],
            {"check_runs": [{"name": "validate", "app": {"slug": "github-actions"},
                             "status": "completed", "conclusion": "failure"}]},
        ]
    )
    fake_http(monkeypatch, replies, calls)
    with pytest.raises(release.ReleaseError, match="has not passed"):
        asyncio.run(
            release.merge_approved_pr(
                proposal_id="abcdef12", proposal=PROPOSAL, token="fake", repo="owner/repo",
                base_branch="main", required_check="validate",
            )
        )
    assert all(call[0] == "GET" for call in calls)


def test_pr_file_policy_requires_ocr_project_prefix() -> None:
    with pytest.raises(release.ReleaseError, match="Disallowed PR change"):
        release.validate_pr_files(
            [{"filename": "src/ocr_platform/example.py", "status": "modified"}], PROPOSAL,
        )


def test_dispatch_only_for_exact_merged_main(monkeypatch) -> None:
    calls = []
    replies = iter(
        [
            {"object": {"sha": MERGE_SHA}},
            {"merged": True, "merge_commit_sha": MERGE_SHA},
            {"workflow_run_id": 42, "html_url": "https://github.com/owner/repo/actions/runs/42"},
        ]
    )
    fake_http(monkeypatch, replies, calls)
    result = asyncio.run(
        release.dispatch_production_deploy(
            token="fake", repo="owner/repo", base_branch="main",
            workflow="ocr-production-deploy.yml", merge_sha=MERGE_SHA,
            proposal_id="abcdef12", pr_number=7,
        )
    )
    assert result["run_id"] == 42
    assert calls[-1][2]["inputs"]["sha"] == MERGE_SHA


def test_reconcile_lost_merge_response(monkeypatch) -> None:
    calls = []
    merged_pr = pr(draft=False)
    merged_pr.update({"state": "closed", "merged": True, "merge_commit_sha": MERGE_SHA})
    fake_http(monkeypatch, iter([merged_pr]), calls)
    result = asyncio.run(release.reconciled_merge_sha(
        token="fake", repo="owner/repo", pr_number=7,
        proposal_id="abcdef12", proposal=PROPOSAL, base_branch="main",
    ))
    assert result == MERGE_SHA
    assert calls == [("GET", "https://api.github.com/repos/owner/repo/pulls/7", None)]


def test_workflow_verifier_rejects_stale_main() -> None:
    merged_pr = {
        "merged": True, "merge_commit_sha": MERGE_SHA,
        "head": {"ref": "codex/ops-abcdef12", "repo": {"full_name": "owner/repo"}},
        "base": {"ref": "main"},
    }
    with pytest.raises(ValueError, match="main moved"):
        verify_release(
            merged_pr, {"object": {"sha": BASE_SHA}}, sha=MERGE_SHA,
            proposal_id="abcdef12", pr_number=7, repo="owner/repo",
        )

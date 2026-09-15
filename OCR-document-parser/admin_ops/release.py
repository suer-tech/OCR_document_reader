"""Human-gated GitHub merge and exact-SHA production workflow dispatch."""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timedelta, timezone

import httpx

from admin_ops.ai import validate_change_path
from admin_ops.paths import project_path


class ReleaseError(RuntimeError):
    pass


def github_headers(token: str) -> dict[str, str]:
    if not token:
        raise ReleaseError("GitHub token is not configured")
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def repo_url(repo: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise ReleaseError("Invalid GitHub owner/repository")
    return f"https://api.github.com/repos/{repo}"


async def request_json(
    client: httpx.AsyncClient, base_url: str, method: str, path: str,
    *, allowed: tuple[int, ...] = (200,), **kwargs,
) -> dict | list:
    try:
        response = await client.request(method, f"{base_url}{path}", **kwargs)
    except httpx.HTTPError as exc:
        raise ReleaseError("GitHub is unavailable") from exc
    if response.status_code not in allowed:
        raise ReleaseError(f"GitHub {method} {path}: HTTP {response.status_code}")
    try:
        return response.json() if response.content else {}
    except ValueError as exc:
        raise ReleaseError("GitHub returned invalid JSON") from exc


def validate_pr(pr: dict, proposal_id: str, proposal: dict, base_branch: str, repo: str) -> str:
    expected_head = proposal.get("pr_head_sha")
    if not re.fullmatch(r"[a-f0-9]{40}", expected_head or ""):
        raise ReleaseError("Stored PR head SHA is missing")
    if pr.get("state") != "open" or pr.get("merged"):
        raise ReleaseError("PR is not open and unmerged")
    if pr.get("head", {}).get("ref") != f"codex/ops-{proposal_id}":
        raise ReleaseError("PR branch does not match the proposal")
    if pr.get("head", {}).get("sha") != expected_head:
        raise ReleaseError("PR changed after administrator approval")
    if (pr.get("head", {}).get("repo") or {}).get("full_name") != repo:
        raise ReleaseError("PR head is not in the configured repository")
    if pr.get("base", {}).get("ref") != base_branch:
        raise ReleaseError("PR targets a different base branch")
    return expected_head


def validate_checks(check_runs: list[dict], required_check: str) -> None:
    matching = [
        run for run in check_runs
        if run.get("name") == required_check and run.get("app", {}).get("slug") == "github-actions"
    ]
    if not matching or any(
        run.get("status") != "completed" or run.get("conclusion") != "success"
        for run in matching
    ):
        raise ReleaseError(f"Required GitHub Actions check '{required_check}' has not passed")


async def public_check_runs(base_url: str, head_sha: str, required_check: str) -> dict:
    """Read CI without the PAT; GitHub exposes checks for public repositories."""
    async with httpx.AsyncClient(
        headers={
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        timeout=20.0,
    ) as client:
        return await request_json(
            client, base_url, "GET", f"/commits/{head_sha}/check-runs",
            params={"check_name": required_check, "filter": "latest", "per_page": 100},
        )


def validate_pr_files(files: list[dict], proposal: dict) -> None:
    expected = set(proposal.get("files", {}))
    if not isinstance(files, list) or not expected or len(files) != len(expected) or len(files) > 5:
        raise ReleaseError("PR file list changed or exceeds policy")
    for file in files:
        if not isinstance(file, dict):
            raise ReleaseError("GitHub returned an invalid PR file")
        path = file.get("filename", "")
        relative = project_path(path)
        if relative is None or relative not in expected or not validate_change_path(relative) or file.get("status") not in {"modified", "added"}:
            raise ReleaseError(f"Disallowed PR change: {path}")


async def merge_approved_pr(
    *, proposal_id: str, proposal: dict, token: str, repo: str,
    base_branch: str, required_check: str,
) -> str:
    """Fail closed unless the exact reviewed revision and CI are still current."""
    if not re.fullmatch(r"[a-f0-9]{8}", proposal_id):
        raise ReleaseError("Invalid proposal ID")
    number = proposal.get("pr_number")
    if not isinstance(number, int) or number < 1:
        raise ReleaseError("Stored PR number is missing")
    base_url = repo_url(repo)
    async with httpx.AsyncClient(headers=github_headers(token), timeout=20.0) as client:
        pr = await request_json(client, base_url, "GET", f"/pulls/{number}")
        head_sha = validate_pr(pr, proposal_id, proposal, base_branch, repo)
        base = await request_json(client, base_url, "GET", f"/git/ref/heads/{base_branch}")
        if base.get("object", {}).get("sha") != proposal.get("base_sha"):
            raise ReleaseError("Base branch changed since proposal; create a new proposal")
        files = await request_json(client, base_url, "GET", f"/pulls/{number}/files", params={"per_page": 100})
        validate_pr_files(files, proposal)
        checks = await public_check_runs(base_url, head_sha, required_check)
        validate_checks(checks.get("check_runs", []), required_check)

        if pr.get("draft"):
            node_id = pr.get("node_id")
            if not node_id:
                raise ReleaseError("PR node ID is missing")
            graphql = await request_json(
                client, "https://api.github.com", "POST", "/graphql",
                json={
                    "query": "mutation($id: ID!) { markPullRequestReadyForReview(input: {pullRequestId: $id}) { pullRequest { isDraft } } }",
                    "variables": {"id": node_id},
                },
            )
            ready = ((graphql.get("data") or {}).get("markPullRequestReadyForReview") or {}).get("pullRequest") or {}
            if graphql.get("errors") or ready.get("isDraft") is not False:
                raise ReleaseError("Could not mark draft PR ready for review")

        for attempt in range(5):
            pr = await request_json(client, base_url, "GET", f"/pulls/{number}")
            validate_pr(pr, proposal_id, proposal, base_branch, repo)
            if pr.get("draft") is False and pr.get("mergeable") is True and pr.get("mergeable_state") == "clean":
                break
            if attempt < 4:
                await asyncio.sleep(2)
        else:
            raise ReleaseError("PR is not cleanly mergeable; review branch rules and checks")
        # Recheck base immediately before the irreversible merge.
        base = await request_json(client, base_url, "GET", f"/git/ref/heads/{base_branch}")
        if base.get("object", {}).get("sha") != proposal.get("base_sha"):
            raise ReleaseError("Base branch moved before merge")
        merged = await request_json(
            client, base_url, "PUT", f"/pulls/{number}/merge",
            json={"sha": head_sha, "merge_method": "squash", "commit_title": f"OCR ops fix {proposal_id}"},
        )
        merge_sha = merged.get("sha")
        if merged.get("merged") is not True or not re.fullmatch(r"[a-f0-9]{40}", merge_sha or ""):
            raise ReleaseError("GitHub did not confirm the merge")
        return merge_sha


async def dispatch_production_deploy(
    *, token: str, repo: str, base_branch: str, workflow: str,
    merge_sha: str, proposal_id: str, pr_number: int,
) -> dict:
    if not re.fullmatch(r"[a-f0-9]{40}", merge_sha):
        raise ReleaseError("Invalid merge SHA")
    if not re.fullmatch(r"[a-f0-9]{8}", proposal_id) or not isinstance(pr_number, int) or pr_number < 1:
        raise ReleaseError("Invalid deployment PR reference")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+\.ya?ml", workflow):
        raise ReleaseError("Invalid deployment workflow name")
    base_url = repo_url(repo)
    async with httpx.AsyncClient(headers=github_headers(token), timeout=20.0) as client:
        current = await request_json(client, base_url, "GET", f"/git/ref/heads/{base_branch}")
        if current.get("object", {}).get("sha") != merge_sha:
            raise ReleaseError("Base branch is no longer at the approved merge SHA")
        pr = await request_json(client, base_url, "GET", f"/pulls/{pr_number}")
        if pr.get("merged") is not True or pr.get("merge_commit_sha") != merge_sha:
            raise ReleaseError("Deployment SHA is not the approved PR merge")
        result = await request_json(
            client, base_url, "POST", f"/actions/workflows/{workflow}/dispatches",
            allowed=(200, 204),
            json={
                "ref": base_branch,
                "inputs": {"sha": merge_sha, "proposal_id": proposal_id, "pr_number": str(pr_number)},
            },
        )
        return {"run_id": result.get("workflow_run_id"), "run_url": result.get("html_url")}


async def reconciled_merge_sha(
    *, token: str, repo: str, pr_number: int, proposal_id: str,
    proposal: dict, base_branch: str,
) -> str | None:
    """Read-only recovery when the merge HTTP response was lost."""
    base_url = repo_url(repo)
    async with httpx.AsyncClient(headers=github_headers(token), timeout=20.0) as client:
        pr = await request_json(client, base_url, "GET", f"/pulls/{pr_number}")
        head = pr.get("head") or {}
        if (head.get("ref") != f"codex/ops-{proposal_id}"
            or head.get("sha") != proposal.get("pr_head_sha")
            or (head.get("repo") or {}).get("full_name") != repo
            or (pr.get("base") or {}).get("ref") != base_branch):
            raise ReleaseError("PR identity changed; manual review required")
        if not pr.get("merged"):
            return None
        sha = pr.get("merge_commit_sha")
        if not re.fullmatch(r"[a-f0-9]{40}", sha or ""):
            raise ReleaseError("Merged PR has no valid merge SHA")
        return sha


async def deployment_result(
    *, token: str, repo: str, workflow: str, merge_sha: str, run_id: int | None,
    proposal_id: str, requested_at: str | None,
) -> dict:
    base_url = repo_url(repo)
    async with httpx.AsyncClient(headers=github_headers(token), timeout=20.0) as client:
        if run_id:
            run = await request_json(client, base_url, "GET", f"/actions/runs/{run_id}")
        else:
            runs = await request_json(
                client, base_url, "GET", f"/actions/workflows/{workflow}/runs",
                params={"event": "workflow_dispatch", "head_sha": merge_sha, "per_page": 10},
            )
            earliest = (
                datetime.fromisoformat(requested_at) - timedelta(seconds=30)
                if requested_at else datetime.min.replace(tzinfo=timezone.utc)
            )
            candidates = [
                item for item in runs.get("workflow_runs", [])
                if item.get("head_sha") == merge_sha
                and proposal_id in item.get("display_title", "")
                and datetime.fromisoformat(item["created_at"].replace("Z", "+00:00")) >= earliest
            ]
            if not candidates:
                return {"status": "not_found"}
            run = candidates[0]
        if run.get("head_sha") != merge_sha or run.get("event") != "workflow_dispatch":
            raise ReleaseError("Deployment run is for another revision")
        return {
            "status": run.get("status"),
            "conclusion": run.get("conclusion"),
            "url": run.get("html_url"),
            "run_id": run.get("id"),
        }

from __future__ import annotations

import base64
import re

import httpx

from admin_ops.ai import validate_change_path
from admin_ops.paths import repo_path


class PublicationError(RuntimeError):
    pass


async def create_draft_pr(
    *, proposal_id: str, proposal: dict, token: str, repo: str, base_branch: str
) -> dict:
    """Publish a reviewed proposal only after an administrator issues /approve."""
    if not token or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise PublicationError("GitHub token or owner/repository is not configured")
    if not re.fullmatch(r"[a-f0-9]{8}", proposal_id):
        raise PublicationError("Invalid proposal id")
    files = proposal.get("files", {})
    if not files or len(files) > 5 or any(not validate_change_path(path) for path in files):
        raise PublicationError("Proposal contains disallowed files")
    branch = f"codex/ops-{proposal_id}"
    base_url = f"https://api.github.com/repos/{repo}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    async with httpx.AsyncClient(headers=headers, timeout=20.0) as client:
        async def call(method: str, path: str, **kwargs) -> dict:
            response = await client.request(method, f"{base_url}{path}", **kwargs)
            if response.status_code >= 400:
                raise PublicationError(f"GitHub {method} {path}: HTTP {response.status_code}")
            return response.json()

        current = await call("GET", f"/git/ref/heads/{base_branch}")
        base_sha = current["object"]["sha"]
        if base_sha != proposal.get("base_sha"):
            raise PublicationError("Base branch changed; create a new proposal before publishing")
        base_commit = await call("GET", f"/git/commits/{base_sha}")
        tree_entries = []
        for path, content in files.items():
            if not isinstance(content, str) or len(content.encode("utf-8")) > 100_000:
                raise PublicationError(f"Unsafe file content: {path}")
            blob = await call(
                "POST", "/git/blobs",
                json={"content": base64.b64encode(content.encode("utf-8")).decode("ascii"), "encoding": "base64"},
            )
            tree_entries.append({"path": repo_path(path), "mode": "100644", "type": "blob", "sha": blob["sha"]})
        tree = await call(
            "POST", "/git/trees",
            json={"base_tree": base_commit["tree"]["sha"], "tree": tree_entries},
        )
        commit = await call(
            "POST", "/git/commits",
            json={
                "message": f"ops: administrator-approved OCR fix {proposal_id}",
                "tree": tree["sha"],
                "parents": [base_sha],
            },
        )
        await call("POST", "/git/refs", json={"ref": f"refs/heads/{branch}", "sha": commit["sha"]})
        pr = await call(
            "POST", "/pulls",
            json={
                "title": f"OCR maintenance proposal {proposal_id}",
                "head": branch,
                "base": base_branch,
                "draft": True,
                "body": f"Approved by an authorized Telegram administrator.\n\n{proposal.get('summary', '')[:1500]}\n\nPython syntax: {proposal.get('checks', {}).get('python_syntax', 'unknown')}. No production deployment or merge performed.",
            },
        )
        return {
            "url": pr["html_url"],
            "number": pr["number"],
            "head_sha": commit["sha"],
            "base_sha": base_sha,
        }

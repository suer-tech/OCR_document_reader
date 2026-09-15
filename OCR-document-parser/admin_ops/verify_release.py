"""Independent workflow-side verification before SSH credentials are used."""

from __future__ import annotations

import json
import os
import re
from urllib.request import Request, urlopen


def fetch_github(path: str, *, repo: str, token: str) -> dict:
    request = Request(
        f"https://api.github.com/repos/{repo}{path}",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urlopen(request, timeout=15) as response:
        return json.load(response)


def verify_release(pr: dict, main_ref: dict, *, sha: str, proposal_id: str, pr_number: int, repo: str) -> None:
    if not re.fullmatch(r"[a-f0-9]{40}", sha):
        raise ValueError("Invalid release SHA")
    if not re.fullmatch(r"[a-f0-9]{8}", proposal_id) or pr_number < 1:
        raise ValueError("Invalid proposal reference")
    if pr.get("merged") is not True or pr.get("merge_commit_sha") != sha:
        raise ValueError("SHA is not the approved merged PR")
    if pr.get("head", {}).get("ref") != f"codex/ops-{proposal_id}":
        raise ValueError("PR branch differs from proposal")
    if (pr.get("head", {}).get("repo") or {}).get("full_name") != repo:
        raise ValueError("PR head repository differs from release repository")
    if pr.get("base", {}).get("ref") != "main":
        raise ValueError("PR was not merged to main")
    if main_ref.get("object", {}).get("sha") != sha:
        raise ValueError("main moved after merge; refuse stale deployment")


def main() -> None:
    sha = os.environ["OPS_RELEASE_SHA"].lower()
    proposal_id = os.environ["OPS_PROPOSAL_ID"].lower()
    pr_number = int(os.environ["OPS_PR_NUMBER"])
    repo = os.environ["GITHUB_REPOSITORY"]
    token = os.environ["GITHUB_TOKEN"]
    pr = fetch_github(f"/pulls/{pr_number}", repo=repo, token=token)
    main_ref = fetch_github("/git/ref/heads/main", repo=repo, token=token)
    verify_release(pr, main_ref, sha=sha, proposal_id=proposal_id, pr_number=pr_number, repo=repo)
    print(f"Verified release {sha[:8]} from PR #{pr_number}")


if __name__ == "__main__":
    main()

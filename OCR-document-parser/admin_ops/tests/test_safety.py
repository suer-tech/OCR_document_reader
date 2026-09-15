import subprocess
from pathlib import Path

import pytest

from admin_ops.ai import clone_source, collect_proposal, validate_change_path
from admin_ops.alerts import AlertTracker


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def test_path_policy_rejects_delete_target_and_secrets() -> None:
    assert validate_change_path("src/ocr_platform/services/example.py")
    assert not validate_change_path("src/ocr_platform/../../.env")
    assert not validate_change_path("src/ocr_platform/secrets.env")
    assert not validate_change_path("docs/README.md")


def test_collect_proposal_accepts_code_but_rejects_deletions(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    project = repo / "OCR-document-parser"
    (project / "src/ocr_platform").mkdir(parents=True)
    file = project / "src/ocr_platform/example.py"
    file.write_text("VALUE = 1\n", encoding="utf-8")
    git(repo, "init")
    git(repo, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "add", ".")
    git(repo, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "base")
    file.write_text("VALUE = 2\n", encoding="utf-8")
    proposal = collect_proposal(project, "base", "changed value")
    assert proposal["files"] == {"src/ocr_platform/example.py": "VALUE = 2\n"}
    assert proposal["checks"]["python_syntax"] == "passed"
    file.write_text("def broken(\n", encoding="utf-8")
    with pytest.raises(ValueError, match="syntax check failed"):
        collect_proposal(project, "base", "broken value")
    file.unlink()
    with pytest.raises(ValueError, match="Disallowed change"):
        collect_proposal(project, "base", "delete value")


def test_collect_proposal_rejects_changes_outside_project(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    project = repo / "OCR-document-parser"
    project.mkdir(parents=True)
    outside = repo / "unrelated.py"
    outside.write_text("VALUE = 1\n", encoding="utf-8")
    git(repo, "init")
    git(repo, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "add", ".")
    git(repo, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "base")
    outside.write_text("VALUE = 2\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Disallowed change"):
        collect_proposal(project, "base", "outside change")


def test_clone_source_uses_only_configured_public_github_repo(monkeypatch, tmp_path: Path) -> None:
    seen = []

    def fake_run(args, **kwargs):
        seen.append(args)

    monkeypatch.setattr(subprocess, "run", fake_run)
    clone_source("suer-tech/OCR_document_reader", "main", tmp_path / "clone")
    assert seen[0][-2] == "https://github.com/suer-tech/OCR_document_reader.git"
    assert "--branch" in seen[0]
    with pytest.raises(ValueError, match="Invalid configured GitHub repository"):
        clone_source("other/repo;evil", "main", tmp_path / "clone")


def test_alert_tracker_requires_two_hits_and_cooldown() -> None:
    tracker = AlertTracker(cooldown_seconds=100)
    metrics = {"api_up": 0, "worker_up": 1, "request_rate_5m": 1, "error_rate_5m": 0}
    assert tracker.observe(metrics, now=0) == []
    assert tracker.observe(metrics, now=60) == ["API metrics endpoint down"]
    assert tracker.observe(metrics, now=120) == []
    assert tracker.observe(metrics, now=180) == ["API metrics endpoint down"]

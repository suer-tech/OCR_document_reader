"""Policy and lint gate for Codex-produced branches in GitHub Actions."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from admin_ops.ai import validate_change_path
from admin_ops.paths import PROJECT_DIR, project_path


def changed_files() -> list[str]:
    result = subprocess.run(
        ["git", "diff", "--name-status", "--no-renames", "HEAD^", "HEAD"],
        check=True, capture_output=True, text=True,
    )
    changes = [line.split("\t", 1) for line in result.stdout.splitlines()]
    if not changes or len(changes) > 5:
        raise ValueError("Expected 1-5 changed files")
    files = []
    for status, path in changes:
        relative = project_path(path)
        if status not in {"A", "M"} or relative is None or not validate_change_path(relative):
            raise ValueError(f"Disallowed CI change: {status} {path}")
        target = Path(path)
        if target.is_symlink() or not target.is_file() or target.stat().st_size > 100_000:
            raise ValueError(f"Unsafe CI file: {path}")
        files.append(path)
    return files


def main() -> None:
    files = changed_files()
    python_files = [path for path in files if path.endswith(".py")]
    for path in python_files:
        compile(Path(path).read_text(encoding="utf-8"), path, "exec")
    if python_files:
        subprocess.run(
            [sys.executable, "-m", "ruff", "check", *(project_path(path) for path in python_files)],
            check=True, cwd=PROJECT_DIR,
        )
    print(f"Ops change policy passed for {len(files)} files")


if __name__ == "__main__":
    main()

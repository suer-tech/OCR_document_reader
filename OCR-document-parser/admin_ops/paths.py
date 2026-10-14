"""Translate project-relative proposal paths to GitHub repository paths."""

from pathlib import PurePosixPath

PROJECT_DIR = "OCR-document-parser"


def repo_path(project_path: str, target: str = "ocr") -> str:
    if target == "hh":
        return project_path
    if target != "ocr":
        raise ValueError("Unknown maintenance target")
    return f"{PROJECT_DIR}/{project_path}"


def project_path(repository_path: str, target: str = "ocr") -> str | None:
    if target == "hh":
        return repository_path
    if target != "ocr":
        raise ValueError("Unknown maintenance target")
    prefix = f"{PROJECT_DIR}/"
    return repository_path[len(prefix):] if repository_path.startswith(prefix) else None


def validate_change_path(path: str, target: str = "ocr") -> bool:
    candidate = PurePosixPath(path)
    if candidate.is_absolute() or ".." in candidate.parts or "\\" in path or ":" in path or "//" in path:
        return False
    if target == "hh":
        blocked = {"fixtures", "evidence", "candidate", "credentials", "secrets", "node_modules", "test-results", "playwright-report"}
        return (path.startswith(("web/server/", "web/app/", "web/tests/"))
                and candidate.suffix in {".ts", ".tsx", ".mjs", ".js", ".json"}
                and not any(part.startswith(".") or part.lower() in blocked for part in candidate.parts)
                and not any(word in candidate.name.lower() for word in ("secret", "credential", ".env")))
    if target != "ocr" or not path.startswith(("src/ocr_platform/", "tests/")):
        return False
    if path.endswith((".env", ".db", ".sqlite3")):
        return False
    return candidate.suffix in {".py", ".yaml", ".yml", ".json", ".toml"}

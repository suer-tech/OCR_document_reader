"""Translate project-relative proposal paths to GitHub repository paths."""

PROJECT_DIR = "OCR-document-parser"


def repo_path(project_path: str) -> str:
    return f"{PROJECT_DIR}/{project_path}"


def project_path(repository_path: str) -> str | None:
    prefix = f"{PROJECT_DIR}/"
    return repository_path[len(prefix):] if repository_path.startswith(prefix) else None

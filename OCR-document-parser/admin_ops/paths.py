"""Translate project-relative proposal paths to GitHub repository paths."""

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

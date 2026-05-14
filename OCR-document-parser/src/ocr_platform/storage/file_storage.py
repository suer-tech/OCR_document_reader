from __future__ import annotations

import base64
from pathlib import Path
from typing import Literal

from ocr_platform.config.settings import get_settings


FileType = Literal["pdf", "image", "text"]


def _ensure_storage_dir() -> Path:
    settings = get_settings()
    path = Path(settings.storage_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def save_document_content(document_id: str, content_base64: str, content_type: FileType) -> str:
    storage_dir = _ensure_storage_dir()
    suffix = ".pdf" if content_type == "pdf" else ".bin"
    file_path = storage_dir / f"{document_id}{suffix}"
    raw = base64.b64decode(content_base64)
    file_path.write_bytes(raw)
    return str(file_path)


def save_document_bytes(document_id: str, content: bytes, content_type: FileType) -> str:
    storage_dir = _ensure_storage_dir()
    suffix = ".pdf" if content_type == "pdf" else ".bin"
    file_path = storage_dir / f"{document_id}{suffix}"
    file_path.write_bytes(content)
    return str(file_path)


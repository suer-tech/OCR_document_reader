from __future__ import annotations

from inference.postprocess import normalize_whitespace


def prepare_text(text: str) -> str:
    return normalize_whitespace(text)

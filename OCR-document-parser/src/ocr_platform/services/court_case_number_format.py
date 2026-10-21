"""Presentation normalization without changing the selected source case."""
import re


def normalize_court_case_number(value: str) -> str:
    value = re.sub(r"\s+", "", value)
    value = re.sub(r"[‐‑‒–—−]", "-", value)
    value = re.sub(r"^A(?=\d)", "А", value, flags=re.I)
    # The service's modern court-case convention: /26 -> /2026. Keep any
    # following dispute suffix. A malformed 3/5-digit year is not truncated.
    return re.sub(r'/(\d{2})(?=$|[-(«"])', r"/20\1", value)

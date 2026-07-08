from __future__ import annotations

import re
from pathlib import Path
from typing import Any


def parse_dataset_md(file_path: str | Path) -> dict[str, dict[str, str]]:
    content = Path(file_path).read_text(encoding="utf-8")
    lines = content.splitlines()
    dataset: dict[str, dict[str, str]] = {}
    current_file: str | None = None
    for line in lines:
        line_stripped = line.strip()
        if not line_stripped:
            continue
        if line.startswith(" ") or line.startswith("\t"):
            if ":" in line_stripped:
                key, val = line_stripped.split(":", 1)
                key = key.strip()
                val = val.strip()
                if current_file:
                    dataset[current_file][key] = val
        else:
            current_file = line_stripped
            dataset[current_file] = {}
    return dataset


def normalize_amount(val: Any) -> float | Any | None:
    if val is None:
        return None
    val_str = str(val).replace(" ", "").replace("\xa0", "").replace(",", ".")
    try:
        return float(val_str)
    except ValueError:
        return val_str


def normalize_name(name: Any) -> str:
    if not name:
        return ""
    name = str(name).lower().replace("ё", "е")
    name = re.sub(r'["\'«»\u201c\u201d]', "", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


def compare_values(field: str, expected: Any, extracted: Any) -> bool:
    if field == "claims_amount":
        exp_norm = normalize_amount(expected)
        ext_norm = normalize_amount(extracted)
        if exp_norm is None or ext_norm is None:
            return exp_norm == ext_norm
        if isinstance(exp_norm, float) and isinstance(ext_norm, float):
            return abs(exp_norm - ext_norm) < 0.01
        return exp_norm == ext_norm
    elif field in ("creditor", "grounds"):
        return normalize_name(expected) == normalize_name(extracted)
    else:
        exp_str = str(expected or "").strip()
        ext_str = str(extracted or "").strip()
        return exp_str == ext_str


RTK_FIELDS = ["creditor_inn", "creditor", "claims_amount", "grounds"]


def _extract_value(extracted_fields: dict[str, Any], field: str) -> Any:
    field_data = extracted_fields.get(field, {})
    if isinstance(field_data, dict):
        return field_data.get("value")
    return field_data


def make_rtk_evaluators():
    """Return evaluator functions for each RTK field + overall accuracy."""

    def _evaluate(input, output, expected_output=None, metadata=None, **kwargs):
        if expected_output is None:
            return []
        scores = []
        for field in RTK_FIELDS:
            exp = expected_output.get(field)
            ext = _extract_value(output or {}, field)
            is_ok = compare_values(field, exp, ext)
            scores.append(
                {
                    "name": f"{field}_accuracy",
                    "value": 1.0 if is_ok else 0.0,
                    "comment": f"expected={exp}, got={ext}",
                }
            )
        return scores

    return _evaluate


def make_rtk_composite_evaluator():
    """Return a composite evaluator that computes overall accuracy."""

    def _composite(
        input, output, expected_output=None, metadata=None, evaluations=None, **kwargs
    ):
        if not evaluations:
            return []
        field_scores = [
            e["value"] for e in evaluations if e["name"].endswith("_accuracy")
        ]
        if not field_scores:
            return []
        overall = sum(field_scores) / len(field_scores)
        errors = sum(1 for s in field_scores if s < 0.5)
        return [
            {
                "name": "overall_accuracy",
                "value": overall,
                "comment": f"{int(sum(field_scores))}/{len(field_scores)} fields correct",
            },
            {
                "name": "field_errors",
                "value": errors,
                "comment": f"{errors} out of {len(field_scores)} fields incorrect",
            },
        ]

    return _composite

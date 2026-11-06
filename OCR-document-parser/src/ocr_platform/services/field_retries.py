"""Application-level retries of missing values, separate from SDK HTTP retries."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

MAX_EMPTY_FIELD_RETRIES = 3


def required_names(fields_config: dict) -> set[str]:
    return {name for name, cfg in fields_config.items()
            if isinstance(cfg, dict) and cfg.get("required") is True}


def empty_value(value: Any) -> bool:
    """Zero and False are findings; whitespace and empty containers are not."""
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (dict, list)):
        values = value.values() if isinstance(value, dict) else value
        return not value or all(empty_value(item) for item in values)
    return False


def has_empty_value(value: Any) -> bool:
    if empty_value(value):
        return True
    if isinstance(value, dict):
        return any(has_empty_value(item) for item in value.values())
    if isinstance(value, list):
        return any(has_empty_value(item) for item in value)
    return False


def preserves_values(previous: Any, candidate: Any) -> bool:
    """Do not change populated leaves or join/reorder unrelated claim rows."""
    if empty_value(previous):
        return True
    if isinstance(previous, dict):
        return isinstance(candidate, dict) and all(
            key in candidate and preserves_values(value, candidate[key])
            for key, value in previous.items()
        )
    if isinstance(previous, list):
        return (isinstance(candidate, list) and len(previous) == len(candidate)
                and all(preserves_values(old, new) for old, new in zip(previous, candidate)))
    return type(previous) is type(candidate) and previous == candidate


def merge_missing(results: dict, candidate: dict, requested: set[str]) -> set[str]:
    """Accept a retry only for requested missing fields, including partial rows."""
    accepted = set()
    for name in requested:
        old = results.get(name, {})
        new = candidate.get(name)
        if not isinstance(new, dict) or not isinstance(old, dict):
            continue
        if (preserves_values(old.get("value"), new.get("value"))
                and not empty_value(new.get("value"))):
            results[name] = deepcopy(new)
            accepted.add(name)
    return accepted

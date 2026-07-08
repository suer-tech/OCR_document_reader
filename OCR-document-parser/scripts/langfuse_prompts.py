#!/usr/bin/env python3
"""
Langfuse Prompt Management CLI.

Commands:
  sync    – Push all current prompts to Langfuse as v1 (initial sync)
  push    – Push prompts as new version + label "production"
  status  – Show which prompts are in Langfuse vs local
  list    – List all versions of a prompt

Usage:
  python scripts/langfuse_prompts.py sync
  python scripts/langfuse_prompts.py push --name extraction_system
  python scripts/langfuse_prompts.py status
  python scripts/langfuse_prompts.py list --name extraction_system
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from langfuse import Langfuse

from ocr_platform.observability.langfuse_prompts import (
    SYNC_PROMPTS,
    get_field_instruction,
)
from ocr_platform.observability.langfuse_datasets import parse_dataset_md

BASE_DIR = Path(__file__).resolve().parent.parent
DATASET_DIR = BASE_DIR.parent / "документы" / "test_rtk" / "решения_датасет"
PROFILES_DIR = (
    Path(__file__).resolve().parent.parent
    / "src"
    / "ocr_platform"
    / "config"
    / "pipelines"
    / "profiles"
)


def _get_langfuse() -> Langfuse:
    pk = os.environ.get("LANGFUSE_PUBLIC_KEY")
    sk = os.environ.get("LANGFUSE_SECRET_KEY")
    if not pk or not sk:
        print("ERROR: LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY must be set")
        sys.exit(1)
    return Langfuse()


def _load_yaml_profile(profile_id: str) -> dict | None:
    import yaml

    path = PROFILES_DIR / f"{profile_id}.yaml"
    if not path.exists():
        return None
    raw = path.read_text(encoding="utf-8")
    return yaml.safe_load(raw)


def _collect_field_instructions() -> dict[str, tuple[str, str]]:
    prompts: dict[str, tuple[str, str]] = {}
    for profile_id in ("rtk", "court_decision_ru"):
        profile = _load_yaml_profile(profile_id)
        if not profile:
            continue
        fields = profile.get("fields_llm", {})
        if not fields:
            fields = profile.get("fields", {})
        for field_name, cfg in fields.items():
            if isinstance(cfg, dict):
                instruction = cfg.get("prompt_instruction", "")
                web_search = cfg.get("prompt_instruction_inn_web_search", "")
                description = cfg.get("description_ru", f"{profile_id}/{field_name}")
                if instruction:
                    key = f"field_instruction_{profile_id}_{field_name}"
                    prompts[key] = (instruction, description)
                if web_search:
                    key = f"field_instruction_{profile_id}_{field_name}_web_search"
                    prompts[key] = (web_search, f"{description} (web search template)")
    return prompts


def _sync_one(
    langfuse: Langfuse, name: str, content: str, description: str, labels: list[str]
) -> None:
    try:
        existing = langfuse.get_prompt(name, type="text")
        if existing:
            print(f"  {name:60s} v{existing.version} (exists, skip)")
            return
    except Exception:
        existing = None

    try:
        langfuse.create_prompt(
            name=name,
            prompt=content,
            labels=labels,
            tags=["rtk", "ocr-platform"],
            commit_message=description,
        )
        print(f"  {name:60s} v1 (created)")
    except Exception as exc:
        print(f"  {name:60s} ERROR: {exc}")


def cmd_sync(args: argparse.Namespace) -> None:
    langfuse = _get_langfuse()

    all_prompts = dict(SYNC_PROMPTS)
    all_prompts.update(_collect_field_instructions())
    labels = ["production"] if args.production else []

    print(f"Syncing {len(all_prompts)} prompts to Langfuse...")
    for name, (content, desc) in sorted(all_prompts.items()):
        _sync_one(langfuse, name, content, desc, labels)
    print("Done.")


def cmd_push(args: argparse.Namespace) -> None:
    langfuse = _get_langfuse()

    all_prompts = dict(SYNC_PROMPTS)
    all_prompts.update(_collect_field_instructions())

    if args.name:
        names_to_push = [n for n in all_prompts if n == args.name]
        if not names_to_push:
            print(f"Unknown prompt name: {args.name}")
            sys.exit(1)
    else:
        names_to_push = sorted(all_prompts.keys())

    for name in names_to_push:
        content, desc = all_prompts[name]
        try:
            langfuse.create_prompt(
                name=name,
                prompt=content,
                labels=["production"],
                tags=["rtk", "ocr-platform"],
                commit_message=desc,
            )
            p = langfuse.get_prompt(name, type="text")
            print(f"  {name:60s} v{p.version} (pushed, label=production)")
        except Exception as exc:
            print(f"  {name:60s} ERROR: {exc}")


def cmd_status(args: argparse.Namespace) -> None:
    langfuse = _get_langfuse()
    all_prompts = dict(SYNC_PROMPTS)
    all_prompts.update(_collect_field_instructions())

    print(f"{'Prompt Name':60s} {'Local':8s} {'Langfuse':8s}")
    print("-" * 80)
    for name in sorted(all_prompts.keys()):
        try:
            p = langfuse.get_prompt(name, type="text")
            langfuse_ver = f"v{p.version}"
        except Exception:
            langfuse_ver = "missing"
        print(f"  {name:60s} {'yes':8s} {langfuse_ver:8s}")


def cmd_list(args: argparse.Namespace) -> None:
    langfuse = _get_langfuse()

    all_prompts = dict(SYNC_PROMPTS)
    all_prompts.update(_collect_field_instructions())

    if args.name:
        names = [n for n in all_prompts if n == args.name]
        if not names:
            print(f"Unknown prompt name: {args.name}")
            sys.exit(1)
    else:
        names = sorted(all_prompts.keys())

    for name in names:
        try:
            p = langfuse.get_prompt(name, type="text")
            print(f"  {name} v{p.version} labels={p.labels}")
        except Exception:
            print(f"  {name} --- (not in Langfuse)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Langfuse Prompt Management CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p_sync = sub.add_parser("sync", help="Create prompts in Langfuse (skip if exists)")
    p_sync.add_argument("--production", action="store_true", help="Label as production")
    p_sync.set_defaults(func=cmd_sync)

    p_push = sub.add_parser("push", help="Push prompts as new production version")
    p_push.add_argument("--name", "-n", default="", help="Prompt name (omit for all)")
    p_push.set_defaults(func=cmd_push)

    p_status = sub.add_parser("status", help="Compare local vs Langfuse prompts")
    p_status.set_defaults(func=cmd_status)

    p_list = sub.add_parser("list", help="List prompt versions")
    p_list.add_argument("--name", "-n", default="", help="Prompt name (omit for all)")
    p_list.set_defaults(func=cmd_list)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

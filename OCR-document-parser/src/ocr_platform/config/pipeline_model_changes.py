"""Report model and provider changes in the OCR pipeline YAML files."""

import argparse
from collections.abc import Iterable
from html import escape
from io import BytesIO
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile

import yaml

from ocr_platform.config.financial_manager_report_profile import CONFIG_ROOT, check_profile_config

REPOSITORY_ROOT = CONFIG_ROOT.parents[4]
PIPELINES_PATH = CONFIG_ROOT.relative_to(REPOSITORY_ROOT).as_posix()
GIT_SHA = re.compile(r"[a-f0-9]{40}")


def model_assignments(documents: Iterable[tuple[str, str]]) -> dict[tuple[str, str], tuple[str, str]]:
    assignments = {}
    for filename, contents in documents:
        config = yaml.safe_load(contents)
        if filename == "system/router.yaml":
            profile = "router"
            stages = {"detection.llm": config.get("detection", {}).get("llm", {})}
        else:
            profile = config["profile_id"]
            stages = config.get("models", {})
        for stage, settings in stages.items():
            if "model" in settings or "provider" in settings:
                assignments[(profile, stage)] = str(settings.get("provider") or ""), str(settings.get("model") or "")
    return assignments


def _current_yaml(root: Path) -> Iterable[tuple[str, str]]:
    for path in [root / "system" / "router.yaml", *sorted((root / "profiles").glob("*.yaml"))]:
        yield path.relative_to(root).as_posix(), path.read_text(encoding="utf-8")


def _baseline_yaml(sha: str, repository_root: Path) -> Iterable[tuple[str, str]]:
    if not GIT_SHA.fullmatch(sha):
        raise ValueError("Для сравнения конфигурации нужен полный SHA коммита.")
    archive = subprocess.check_output(
        ["git", "archive", "--format=tar", sha, PIPELINES_PATH], cwd=repository_root,
    )
    folder = Path(PIPELINES_PATH)
    with tarfile.open(fileobj=BytesIO(archive)) as snapshot:
        for item in snapshot:
            if not item.isfile():
                continue
            path = Path(item.name).relative_to(folder)
            if (path.as_posix() == "system/router.yaml"
                    or path.parent.as_posix() == "profiles" and path.suffix == ".yaml"):
                with snapshot.extractfile(item) as stream:
                    yield path.as_posix(), stream.read().decode("utf-8")


def describe_changes(previous: dict, current: dict) -> list[str]:
    changes = []
    for profile, stage in sorted(previous.keys() | current.keys()):
        key = profile, stage
        old = previous.get(key)
        new = current.get(key)
        if old != new:
            before = f"{old[0]!r} / {old[1]!r}" if old else "отсутствовала"
            after = f"{new[0]!r} / {new[1]!r}" if new else "удалена"
            changes.append(f"{profile!r} · {stage!r} — {before} → {after}")
    return changes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", help="Full Git SHA for the comparison")
    args = parser.parse_args()
    try:
        check_profile_config(CONFIG_ROOT)
        current = model_assignments(_current_yaml(CONFIG_ROOT))
        if not args.base:
            print(f"Проверено настроек модели: {len(current)}.")
            return 0
        previous = model_assignments(_baseline_yaml(args.base, REPOSITORY_ROOT))
        changes = describe_changes(previous, current)
    except (OSError, ValueError, TypeError, KeyError, AttributeError, yaml.YAMLError, subprocess.CalledProcessError) as exc:
        print(f"Ошибка проверки OCR-конфигурации: {exc}", file=sys.stderr)
        return 1

    if not changes:
        print("Модели в конфигурации не менялись.")
        return 0
    for change in changes:
        if os.getenv("GITHUB_ACTIONS") == "true":
            print("::warning title=Изменена модель OCR::" + change.replace("%", "%25"))
        else:
            print(change)
    if summary := os.getenv("GITHUB_STEP_SUMMARY"):
        with Path(summary).open("a", encoding="utf-8") as stream:
            stream.write("### Изменения моделей OCR\n\n")
            stream.writelines(f"- {escape(change)}\n" for change in changes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

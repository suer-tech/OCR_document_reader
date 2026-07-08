#!/usr/bin/env python3
"""
Langfuse Dataset + Experiment CLI for the RTK benchmark.

Commands:
  sync   – Create/update Langfuse Dataset from dataset.md
  run    – Run an experiment via Langfuse run_experiment (traces + scores)
  list   – List past experiment runs from Langfuse

Usage:
  python scripts/langfuse_dataset.py sync
  python scripts/langfuse_dataset.py run --api-url http://localhost:8000 --limit 10
  python scripts/langfuse_dataset.py list
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import os
import sys
import time
import json
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from langfuse import Langfuse
from langfuse.experiment import Evaluation

from ocr_platform.observability.langfuse_datasets import (
    compare_values,
    make_rtk_evaluators,
    make_rtk_composite_evaluator,
    parse_dataset_md,
    RTK_FIELDS,
)

BASE_DIR = Path(__file__).resolve().parent.parent
DATASET_DIR = BASE_DIR.parent / "документы" / "test_rtk" / "решения_датасет"
DATASET_MD = DATASET_DIR / "dataset.md"

DATASET_NAME = "rtk_benchmark"


def _get_langfuse() -> Langfuse:
    pk = os.environ.get("LANGFUSE_PUBLIC_KEY")
    sk = os.environ.get("LANGFUSE_SECRET_KEY")
    if not pk or not sk:
        print("ERROR: LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY must be set")
        sys.exit(1)
    return Langfuse()


def _filename_to_item_id(filename: str) -> str:
    return filename.replace("/", "_").replace(" ", "_").replace(".pdf", "")


def _build_item_input(filename: str) -> dict:
    clean_name = (
        filename.replace("2/", "", 1) if filename.startswith("2/") else filename
    )
    return {
        "filename": clean_name,
        "dataset_path": str(DATASET_DIR / clean_name),
    }


def _extract_fields_from_api_result(resp_data: dict) -> dict:
    fields_data = resp_data.get("extracted_fields") or resp_data.get("fields", {})
    extracted = {}
    if isinstance(fields_data, dict):
        for f_name, f_val in fields_data.items():
            if isinstance(f_val, dict):
                extracted[f_name] = {
                    "value": f_val.get("value"),
                    "reasoning": f_val.get("reasoning"),
                }
            else:
                extracted[f_name] = {"value": f_val, "reasoning": None}
    return extracted


# ---------------------------------------------------------------------------
# COMMAND: sync
# ---------------------------------------------------------------------------


def cmd_sync(args: argparse.Namespace) -> None:
    client = _get_langfuse()
    ground_truth = parse_dataset_md(DATASET_MD)
    print(f"Loaded {len(ground_truth)} ground truth entries from {DATASET_MD}")

    dataset = client.create_dataset(
        name=DATASET_NAME,
        description="RTK benchmark: 52 PDF-документов с полями creditor_inn, creditor, claims_amount, grounds",
        metadata={
            "source": "test_rtk/решения_датасет",
            "total_docs": len(ground_truth),
        },
    )
    print(f"Dataset '{DATASET_NAME}' ready (id={dataset.id})")

    for idx, (filename, expected) in enumerate(ground_truth.items(), 1):
        item_id = _filename_to_item_id(filename)
        item_input = _build_item_input(filename)

        try:
            client.create_dataset_item(
                dataset_name=DATASET_NAME,
                id=item_id,
                input=item_input,
                expected_output=dict(expected),
                metadata={"filename": filename, "index": idx},
            )
            print(f"  [{idx}/{len(ground_truth)}] {filename} -> {item_id}")
        except Exception as exc:
            print(f"  [{idx}/{len(ground_truth)}] ERROR: {filename}: {exc}")

    print(f"\nDone. Dataset '{DATASET_NAME}' has {len(ground_truth)} items.")


# ---------------------------------------------------------------------------
# COMMAND: run
# ---------------------------------------------------------------------------


async def _process_one(client: httpx.AsyncClient, api_url: str, filepath: str) -> dict:
    doc_bytes = Path(filepath).read_bytes()
    b64 = base64.b64encode(doc_bytes).decode("utf-8")
    filename = Path(filepath).name

    ingest_payload = {
        "content_base64": b64,
        "content_type": "pdf",
        "source_type": "portal",
        "document_type": "rtk",
        "idempotency_key": f"langfuse-exp-{filename}-{time.time_ns()}",
    }

    r = await client.post(f"{api_url}/documents/ingest", json=ingest_payload)
    if r.status_code not in (200, 202):
        return {"error": f"Ingest failed: {r.status_code} {r.text}"}

    data = r.json()
    doc_id = data.get("document_id") or data.get("id")
    run_id = data.get("pipeline_run_id")

    deadline = time.time() + 1800
    while time.time() < deadline:
        r2 = await client.get(f"{api_url}/pipeline-runs/{run_id}")
        if r2.status_code == 200:
            status = r2.json().get("status")
            if status in ("done", "failed"):
                break
        await asyncio.sleep(2)
    else:
        return {"error": "timeout"}

    if status == "failed":
        return {"error": "pipeline_failed"}

    res = await client.get(f"{api_url}/documents/{doc_id}/result")
    if res.status_code != 200:
        return {"error": f"result_fetch_failed: {res.status_code}"}

    return res.json()


def cmd_run(args: argparse.Namespace) -> None:
    client = _get_langfuse()
    dataset = client.get_dataset(DATASET_NAME)

    if not dataset.items:
        print(f"ERROR: Dataset '{DATASET_NAME}' has no items. Run 'sync' first.")
        sys.exit(1)

    items = dataset.items
    if args.limit and args.limit < len(items):
        items = items[: args.limit]

    print(f"Running experiment on {len(items)} dataset items")
    print(f"API URL: {args.api_url}")

    async def task(*, item, **kwargs):
        input_data = item.input
        if isinstance(input_data, str):
            input_data = json.loads(input_data)
        filepath = input_data.get("dataset_path")
        if not filepath or not Path(filepath).exists():
            return {"error": f"file not found: {filepath}"}

        async with httpx.AsyncClient(base_url=args.api_url, timeout=120.0) as hx:
            result = await _process_one(hx, args.api_url, filepath)

        if "error" in result:
            return {"error": result["error"]}

        return _extract_fields_from_api_result(result)

    evaluator = make_rtk_evaluators()
    composite = make_rtk_composite_evaluator()

    result = dataset.run_experiment(
        name=args.name,
        description=args.description or f"RTK benchmark on {args.api_url}",
        task=task,
        evaluators=[evaluator],
        composite_evaluator=composite,
        max_concurrency=args.concurrency,
        metadata={"api_url": args.api_url, "profile": "rtk"},
    )

    print("\n" + "=" * 60)
    print(result.format())
    print("=" * 60)
    print(f"\nView results: {result.get('dataset_run_url', 'Langfuse dashboard')}")


# ---------------------------------------------------------------------------
# COMMAND: list
# ---------------------------------------------------------------------------


def cmd_list(args: argparse.Namespace) -> None:
    client = _get_langfuse()
    runs = client.get_dataset_runs(dataset_name=DATASET_NAME, limit=args.limit)
    print(f"Dataset: {DATASET_NAME}")
    print(f"Runs found: {len(runs.data)}")
    print()
    for run in runs.data:
        created = run.created_at.isoformat() if hasattr(run, "created_at") else "?"
        print(f"  {run.run_name:50s}  {created}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Langfuse Dataset management for RTK benchmark"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # sync
    p_sync = sub.add_parser(
        "sync", help="Create/update Langfuse Dataset from dataset.md"
    )
    p_sync.set_defaults(func=cmd_sync)

    # run
    p_run = sub.add_parser("run", help="Run experiment on the dataset")
    p_run.add_argument("--name", default="RTK Experiment", help="Experiment name")
    p_run.add_argument("--description", default="", help="Experiment description")
    p_run.add_argument("--api-url", default="http://localhost:8000", help="OCR API URL")
    p_run.add_argument("--limit", type=int, default=0, help="Limit items to process")
    p_run.add_argument(
        "--concurrency", type=int, default=3, help="Max concurrent items"
    )
    p_run.set_defaults(func=cmd_run)

    # list
    p_list = sub.add_parser("list", help="List past experiment runs")
    p_list.add_argument("--limit", type=int, default=10, help="Max runs to list")
    p_list.set_defaults(func=cmd_list)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

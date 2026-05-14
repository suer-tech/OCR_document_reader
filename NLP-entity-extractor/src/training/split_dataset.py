from __future__ import annotations

import argparse
import random
from pathlib import Path

from .dataset import load_jsonl, write_jsonl


def main() -> None:
    parser = argparse.ArgumentParser(description="Split approved JSONL records into train/valid/test files")
    parser.add_argument("input", type=Path, help="Path to bootstrap or labeled JSONL file")
    parser.add_argument("output_dir", type=Path, help="Directory for train/valid/test JSONL files")
    parser.add_argument("--valid-ratio", type=float, default=0.15)
    parser.add_argument("--test-ratio", type=float, default=0.15)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    rows = load_jsonl(args.input)
    approved = [row for row in rows if row.get("review_status") == "approved"]
    skipped = [row for row in rows if row.get("review_status") == "skip"]

    if not approved:
        raise SystemExit("No approved records found")

    random.Random(args.seed).shuffle(approved)
    total = len(approved)
    test_count = int(total * args.test_ratio)
    valid_count = int(total * args.valid_ratio)

    if total >= 3:
        if test_count == 0:
            test_count = 1
        if valid_count == 0:
            valid_count = 1
        if test_count + valid_count >= total:
            test_count = max(1, test_count)
            valid_count = max(1, min(valid_count, total - test_count - 1))

    train_end = total - valid_count - test_count
    train_rows = approved[:train_end]
    valid_rows = approved[train_end:train_end + valid_count]
    test_rows = approved[train_end + valid_count:]

    for row in train_rows:
        row["split"] = "train"
    for row in valid_rows:
        row["split"] = "valid"
    for row in test_rows:
        row["split"] = "test"

    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(args.output_dir / "train.jsonl", train_rows)
    write_jsonl(args.output_dir / "valid.jsonl", valid_rows)
    write_jsonl(args.output_dir / "test.jsonl", test_rows)
    write_jsonl(args.output_dir / "skipped.jsonl", skipped)

    print(
        {
            "approved": total,
            "train": len(train_rows),
            "valid": len(valid_rows),
            "test": len(test_rows),
            "skipped": len(skipped),
            "output_dir": str(args.output_dir),
        }
    )


if __name__ == "__main__":
    main()
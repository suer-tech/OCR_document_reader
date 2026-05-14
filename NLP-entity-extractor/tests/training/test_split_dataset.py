from pathlib import Path

from training.dataset import load_jsonl, write_jsonl
from training.split_dataset import main


def test_split_dataset_creates_expected_files(tmp_path: Path, monkeypatch) -> None:
    input_path = tmp_path / "bootstrap.jsonl"
    output_dir = tmp_path / "splits"
    rows = [
        {"document_id": "1", "review_status": "approved"},
        {"document_id": "2", "review_status": "approved"},
        {"document_id": "3", "review_status": "approved"},
        {"document_id": "4", "review_status": "skip"},
    ]
    write_jsonl(input_path, rows)

    monkeypatch.setattr(
        "sys.argv",
        [
            "split_dataset.py",
            str(input_path),
            str(output_dir),
            "--valid-ratio",
            "0.2",
            "--test-ratio",
            "0.2",
            "--seed",
            "1",
        ],
    )
    main()

    train_rows = load_jsonl(output_dir / "train.jsonl")
    valid_rows = load_jsonl(output_dir / "valid.jsonl")
    test_rows = load_jsonl(output_dir / "test.jsonl")
    skipped_rows = load_jsonl(output_dir / "skipped.jsonl")

    assert len(train_rows) == 1
    assert len(valid_rows) == 1
    assert len(test_rows) == 1
    assert len(skipped_rows) == 1
    assert {row["split"] for row in train_rows} == {"train"}
    assert {row["split"] for row in valid_rows} == {"valid"}
    assert {row["split"] for row in test_rows} == {"test"}
from pathlib import Path

from training.dataset import load_jsonl, write_jsonl


def test_roundtrip_jsonl(tmp_path: Path) -> None:
    path = tmp_path / 'sample.jsonl'
    rows = [{'document_id': '1', 'text': 'abc'}]
    write_jsonl(path, rows)
    assert load_jsonl(path) == rows

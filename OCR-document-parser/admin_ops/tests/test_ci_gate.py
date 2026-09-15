from types import SimpleNamespace

import pytest

from admin_ops import ci_gate


def test_ci_gate_rejects_deletion_even_when_file_policy_allows_path(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        ci_gate.subprocess, "run",
        lambda *args, **kwargs: SimpleNamespace(stdout="D\tOCR-document-parser/src/ocr_platform/example.py\n"),
    )
    with pytest.raises(ValueError, match="Disallowed CI change"):
        ci_gate.changed_files()


def test_ci_gate_accepts_only_small_allowed_file(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    file = tmp_path / "OCR-document-parser/src/ocr_platform/example.py"
    file.parent.mkdir(parents=True)
    file.write_text("VALUE = 2\n", encoding="utf-8")
    monkeypatch.setattr(
        ci_gate.subprocess, "run",
        lambda *args, **kwargs: SimpleNamespace(stdout="M\tOCR-document-parser/src/ocr_platform/example.py\n"),
    )
    assert ci_gate.changed_files() == ["OCR-document-parser/src/ocr_platform/example.py"]


def test_ci_gate_rejects_same_named_folder_at_repository_root(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        ci_gate.subprocess, "run",
        lambda *args, **kwargs: SimpleNamespace(stdout="M\tsrc/ocr_platform/example.py\n"),
    )
    with pytest.raises(ValueError, match="Disallowed CI change"):
        ci_gate.changed_files()

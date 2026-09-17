import pytest

from admin_ops import code_read
from admin_ops.code_read import CodeReader, allowed_code_path


@pytest.fixture
def reader(tmp_path):
    paths = ["OCR-document-parser/src/rules.py", "OCR-document-parser/tests/test_rules.py"]
    for path in paths:
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("DEFAULT_ADVANCE_DAYS = 10\nearly_report_deadline = None\n", encoding="utf-8")
    return CodeReader(tmp_path, paths)


def test_list_search_and_numbered_read(reader):
    assert reader.list_code_files(limit=1)["next_offset"] == 1
    assert reader.list_code_files(path_contains="tests")["total"] == 1
    found = reader.search_code("EARLY_REPORT_DEADLINE", path_contains="src/")
    assert found["matches"] == [{"path": "OCR-document-parser/src/rules.py", "line": 2,
                                  "text": "early_report_deadline = None"}]
    assert not found["truncated"]
    result = reader.read_code_file(found["matches"][0]["path"], start_line=2, line_count=1)
    assert result["lines"] == [{"line": 2, "text": "early_report_deadline = None"}]
    assert reader.search_code(".*")["matches"] == []  # literal, never a regex


@pytest.mark.parametrize("path", [
    "../auth.json", "/etc/passwd", "C:/Users/auth.json", "OCR-document-parser/src/../.env",
    "OCR-document-parser/src//rules.py", "OCR-document-parser/src/.env", "OCR-document-parser/src/secrets.json",
    "OCR-document-parser/tests/fixtures/document.json", "OCR-document-parser/reports/data.json",
    "OCR-document-parser/models/model.py", "OCR-document-parser/src/.hidden/code.py", ".git/config",
    "OCR-document-parser/src/credentials.json", "OCR-document-parser/src/file.pdf", "OCR-document-parser\\src\\rules.py",
])
def test_path_boundary(reader, path):
    assert not allowed_code_path(path)
    with pytest.raises(ValueError):
        reader.read_code_file(path)


def test_untracked_files_cannot_be_read(reader):
    path = "OCR-document-parser/src/untracked.py"
    (reader.root / path).write_text("secret", encoding="utf-8")
    with pytest.raises(ValueError):
        reader.read_code_file(path)


def test_symlinks_cannot_escape_reader(reader, tmp_path):
    path = reader.root / "OCR-document-parser/src/rules.py"
    path.unlink()
    outside = tmp_path / "secret.py"
    outside.write_text("PRIVATE", encoding="utf-8")
    try:
        path.symlink_to(outside)
    except OSError:
        pytest.skip("Windows user cannot create symlinks")
    with pytest.raises(ValueError):
        reader.read_code_file("OCR-document-parser/src/rules.py")
    assert not reader.search_code("PRIVATE")["matches"]


def test_bounds_and_binary_source(reader, monkeypatch):
    with pytest.raises(ValueError):
        reader.read_code_file("OCR-document-parser/src/rules.py", line_count=201)
    with pytest.raises(ValueError):
        reader.search_code("x", limit=100)
    with pytest.raises(ValueError):
        reader.list_code_files(offset=-1)
    assert reader.search_code("early", limit=1)["truncated"]
    monkeypatch.setattr(code_read, "MAX_SEARCH_BYTES", 1)
    assert reader.search_code("early")["truncated"]
    (reader.root / "OCR-document-parser/src/rules.py").write_bytes(b"binary\0content")
    with pytest.raises(ValueError):
        reader.read_code_file("OCR-document-parser/src/rules.py")


def test_long_file_and_long_line_are_bounded(reader, monkeypatch):
    path = "OCR-document-parser/src/rules.py"
    (reader.root / path).write_text("x" * 20000, encoding="utf-8")
    result = reader.read_code_file(path)
    assert result["truncated"] and len(result["lines"][0]["text"]) == 16000
    monkeypatch.setattr(code_read, "MAX_FILE_BYTES", 10)
    with pytest.raises(ValueError):
        reader.read_code_file(path)

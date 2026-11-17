from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from ocr_platform.services import doc_service as doc
from ocr_platform.services.document_content_validation import validate_document_content


@pytest.mark.parametrize("content", [b"", b"not Word", b"%PDF-1.7", b"{\\rtf1 text}", b"PK\x03\x04"])
def test_reject_mislabeled_content_without_starting_process(monkeypatch, content):
    monkeypatch.setattr(doc.subprocess, "run", lambda *a, **k: pytest.fail("must not run"))
    with pytest.raises(ValueError, match="binary Word DOC"):
        validate_document_content(content, "doc")


def test_size_limit_before_process(monkeypatch):
    monkeypatch.setattr(doc, "MAX_DOC_BYTES", 8)
    with pytest.raises(ValueError, match="size limit"):
        doc.extract_doc_text(doc.OLE_HEADER + b"x")


@pytest.mark.parametrize("failure", ["timeout", "invalid", "empty", "encoding", "large"])
def test_reader_failures_are_explicit_and_clean_up(monkeypatch, failure):
    monkeypatch.setattr(doc.shutil, "which", lambda _: "/usr/bin/antiword")
    paths = []
    def run(args, **kwargs):
        paths.append(Path(args[-1]))
        assert paths[-1].exists()
        assert args[1:6] == ["-t", "-m", "UTF-8.txt", "-w", "0"]
        assert kwargs["timeout"] == doc.DOC_TIMEOUT_SECONDS
        if failure == "timeout":
            raise subprocess.TimeoutExpired(args, 20)
        if failure == "invalid":
            return SimpleNamespace(returncode=1)
        kwargs["stdout"].write({"empty": b" \n", "encoding": b"\xff", "large": b"a" * 20}[failure])
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(doc.subprocess, "run", run)
    monkeypatch.setattr(doc, "MAX_TEXT_BYTES", 10)
    with pytest.raises(ValueError):
        doc.extract_doc_text(doc.OLE_HEADER + b"x")
    assert paths and not paths[0].exists()


def test_missing_reader_is_service_configuration_error(monkeypatch):
    monkeypatch.setattr(doc.shutil, "which", lambda _: None)
    with pytest.raises(RuntimeError, match="install antiword"):
        doc.extract_doc_text(doc.OLE_HEADER)


@pytest.mark.skipif(not shutil.which("antiword"), reason="requires antiword")
def test_real_binary_doc_cyrillic_table_and_worker_text():
    from ocr_platform.services.ocr_service import extract_text_at_ingest
    source = Path(__file__).parents[1] / "fixtures" / "legacy_doc" / "russian.doc"
    text, used, corrected, engine = extract_text_at_ingest(str(source), "doc")
    assert engine == "doc" and used is False and corrected is None
    for expected in ["Тестова", "Анна Ивановна", "01 02 001234", "001-002", "123456", "Таблица: контроль", "12345"]:
        assert expected in text
    assert text.index("Должник:") < text.index("Представителя:") < text.index("Кредиторы:")


@pytest.mark.skipif(not shutil.which("antiword"), reason="requires antiword")
def test_corrupt_ole_rejected_by_real_reader():
    with pytest.raises(ValueError, match="damaged DOC"):
        doc.extract_doc_text(doc.OLE_HEADER + b"\x00" * 1024)

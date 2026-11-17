"""Read legacy binary Word documents locally; no Office macros are executed."""
from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import tempfile

MAX_DOC_BYTES = 32 * 1024 * 1024
MAX_TEXT_BYTES = 32 * 1024 * 1024
DOC_TIMEOUT_SECONDS = 20
OLE_HEADER = bytes.fromhex("d0cf11e0a1b11ae1")


def extract_doc_text(content: bytes) -> str:
    if len(content) > MAX_DOC_BYTES:
        raise ValueError("DOC exceeds the 32 MiB size limit")
    if not content.startswith(OLE_HEADER):
        raise ValueError("Invalid binary Word DOC; save the document as DOCX or PDF")
    executable = shutil.which("antiword")
    if executable is None:
        raise RuntimeError("DOC reader unavailable: install antiword")
    with tempfile.TemporaryDirectory(prefix="ocr-doc-") as directory:
        source = Path(directory) / "input.doc"
        source.write_bytes(content)
        # Keep the original file name and document contents out of command logs.
        with tempfile.TemporaryFile() as output:
            try:
                result = subprocess.run(
                    [executable, "-t", "-m", "UTF-8.txt", "-w", "0", str(source)],
                    stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.DEVNULL,
                    timeout=DOC_TIMEOUT_SECONDS, check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise ValueError("DOC reading timed out; export the document to DOCX or PDF") from exc
            if result.returncode != 0:
                raise ValueError("Unreadable, encrypted or damaged DOC; save an unprotected DOCX or PDF")
            if output.tell() > MAX_TEXT_BYTES:
                raise ValueError("DOC extracted text exceeds the size limit")
            output.seek(0)
            try:
                text = output.read().decode("utf-8").strip()
            except UnicodeDecodeError as exc:
                raise ValueError("DOC reader returned invalid text encoding") from exc
    if not text:
        raise ValueError("DOC has no readable text; export image-only documents to PDF")
    return text


def read_doc_text(file_path: str) -> str:
    with Path(file_path).open("rb") as stream:
        return extract_doc_text(stream.read(MAX_DOC_BYTES + 1))

from __future__ import annotations


def validate_document_content(content: bytes, content_type: str) -> None:
    """Reject a mislabeled PDF before storing or sending it to OCR providers."""
    if content_type != "pdf":
        return

    prefix = content[:1024].lstrip().lower()
    if prefix.startswith((b"<!doctype html", b"<html")):
        raise ValueError(
            "file contains an HTML page instead of a PDF; download the actual "
            "document after completing the source site's verification"
        )
    # PDF readers permit a small preamble before the header.
    if b"%PDF-" in content[:1024]:
        return
    raise ValueError("file content is not a PDF (missing %PDF- header)")

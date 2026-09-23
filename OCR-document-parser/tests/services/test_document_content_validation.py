import pytest

from ocr_platform.services.document_content_validation import validate_document_content


def test_html_challenge_mislabeled_as_pdf_is_rejected() -> None:
    saved_challenge = b"\r\n\r\n<!DOCTYPE html><html><form id='tokenFrom'></form></html>"
    with pytest.raises(ValueError, match="HTML page instead of a PDF"):
        validate_document_content(saved_challenge, "pdf")


def test_real_pdf_header_is_accepted() -> None:
    validate_document_content(b"%PDF-1.7\n1 0 obj\n", "pdf")


def test_other_content_is_unchanged() -> None:
    validate_document_content(b"ordinary text", "text")

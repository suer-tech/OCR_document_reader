from __future__ import annotations

import base64
import io

from pypdf import PdfReader


class PdfDecodeError(ValueError):
    pass


class PdfTextExtractionError(ValueError):
    pass


def decode_pdf_base64(payload: str) -> bytes:
    try:
        return base64.b64decode(payload, validate=True)
    except Exception as exc:
        raise PdfDecodeError("Invalid Base64 PDF payload") from exc


def extract_text_from_pdf_bytes(pdf_bytes: bytes) -> str:
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        text_parts: list[str] = []
        for page in reader.pages:
            text_parts.append(page.extract_text() or "")
        text = "\n".join(part for part in text_parts if part)
    except Exception as exc:
        raise PdfTextExtractionError("Unable to extract text from PDF") from exc

    if not text.strip():
        raise PdfTextExtractionError("PDF contains no extractable text")
    return text

"""Bounded, local extraction of visible Word paragraphs in document order."""
from __future__ import annotations

import io
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
MAX_EXPANDED_BYTES = 32 * 1024 * 1024


def extract_docx_text(content: bytes) -> str:
    try:
        with ZipFile(io.BytesIO(content)) as archive:
            entries = archive.infolist()
            if len(entries) > 2000 or sum(x.file_size for x in entries) > MAX_EXPANDED_BYTES:
                raise ValueError("DOCX archive exceeds the expanded size limit")
            if any(x.flag_bits & 1 for x in entries):
                raise ValueError("Encrypted DOCX is not supported")
            if "[Content_Types].xml" not in archive.namelist():
                raise ValueError("DOCX package is missing content types")
            xml = archive.read("word/document.xml")
        root = ET.fromstring(xml)
    except (BadZipFile, KeyError, ET.ParseError, RuntimeError) as exc:
        raise ValueError("Invalid DOCX document") from exc
    if root.tag != W + "document":
        raise ValueError("Invalid DOCX document root")
    body = root.find(W + "body")
    if body is None:
        raise ValueError("DOCX document has no body")

    def visible(node):
        if node.tag in {W + "del", W + "moveFrom"}:
            return ""
        if node.tag == W + "t":
            return node.text or ""
        if node.tag in {W + "tab", W + "br", W + "cr"}:
            return " "
        return "".join(visible(child) for child in node)

    def paragraphs(node):
        if node.tag in {W + "del", W + "moveFrom"}:
            return
        if node.tag == W + "p":
            yield visible(node)
            return
        for child in node:
            yield from paragraphs(child)

    # Paragraphs inside table cells remain in their original reading order.
    text = "\n".join(paragraphs(body)).strip()
    if not text:
        raise ValueError("DOCX has no readable text; export image-only documents to PDF")
    return text


def read_docx_text(file_path: str) -> str:
    return extract_docx_text(Path(file_path).read_bytes())

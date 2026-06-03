from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ocr_platform.observability.logging import get_logger

if TYPE_CHECKING:
    from docling.document_converter import DocumentConverter

logger = get_logger(__name__)

# Global singleton for the converter, initialized lazily
_converter: DocumentConverter | None = None


def get_converter() -> DocumentConverter:
    """
    Lazily initializes and returns the DocumentConverter instance.
    This saves startup memory and prevents initialization failures from crashing the server startup.
    """
    global _converter
    if _converter is None:
        logger.info("initializing_docling_converter")
        try:
            from docling.document_converter import DocumentConverter
            # Initialize with default options.
            # Docling automatically uses GPU if CUDA is available, or CPU otherwise.
            _converter = DocumentConverter()
            logger.info("docling_converter_initialized")
        except Exception as exc:
            logger.exception("docling_initialization_failed", error=str(exc))
            raise RuntimeError(f"Failed to initialize Docling: {exc}") from exc
    return _converter


def run_docling_ocr(file_path: str) -> str:
    """
    Runs IBM Docling on the specified document (PDF or image).
    Returns the parsed document structured in Markdown format.
    """
    path = Path(file_path)
    if not path.exists():
        logger.warning("docling_skipped", reason="file_not_found", file_path=file_path)
        return ""

    logger.info("running_docling_ocr", file_path=file_path)
    try:
        converter = get_converter()
        # Perform conversion
        result = converter.convert(path)
        # Export to Markdown format
        markdown_text = result.document.export_to_markdown()
        logger.info("docling_ocr_completed", file_path=file_path, text_length=len(markdown_text))
        return markdown_text.strip()
    except Exception as exc:
        logger.exception("docling_ocr_failed", file_path=file_path, error=str(exc))
        raise RuntimeError(f"Docling conversion failed for {file_path}: {exc}") from exc

from __future__ import annotations

from pathlib import Path
from time import perf_counter
from typing import Literal

import pdfplumber
import pymupdf
import pytesseract
from pdf2image import convert_from_path
from PIL import Image

# Auto-detect Tesseract executable on Windows
import platform
if platform.system() == "Windows":
    _tesseract_win = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")
    if _tesseract_win.exists():
        pytesseract.pytesseract.tesseract_cmd = str(_tesseract_win)

from ocr_platform.observability.logging import get_logger

logger = get_logger(__name__)

# Максимальная длина текста для MLflow-артефакта (чтобы не перегружать хранилище)
OCR_TEXT_ARTIFACT_MAX_LEN = 100_000

# Источник извлечённого текста (для логирования)
TextSource = Literal["pdfplumber", "pymupdf", "ocr", "text", "glm"]

ContentType = Literal["pdf", "image", "text"]

# Языки для Tesseract (русский + английский для смешанных документов)
TESSERACT_LANG = "rus+eng"


def extract_text_from_pdf(file_path: str) -> str:
    """
    Пытается извлечь текст из PDF без OCR.
    Для MVP: простое конкатенирование текста со всех страниц.
    При ошибке (повреждённый PDF, не-PDF с расширением .pdf) возвращает "".
    """
    path = Path(file_path)
    if not path.exists():
        return ""

    try:
        text_parts: list[str] = []
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text() or ""
                text_parts.append(page_text)
        return "\n".join(text_parts).strip()
    except Exception as exc:
        logger.warning(
            "pdf_text_extraction_failed",
            file_path=file_path,
            error_type=type(exc).__name__,
            error=str(exc),
        )
        return ""


def extract_text_with_pymupdf(file_path: str) -> str:
    """
    Извлечение текста через PyMuPDF (fitz).
    Может находить текст в PDF, где pdfplumber не справляется.
    При ошибке возвращает "".
    """
    path = Path(file_path)
    if not path.exists():
        return ""

    try:
        text_parts: list[str] = []
        with pymupdf.open(path) as doc:
            for page in doc:
                page_text = page.get_text() or ""
                text_parts.append(page_text)
        return "\n".join(text_parts).strip()
    except Exception as exc:
        logger.warning(
            "pymupdf_text_extraction_failed",
            file_path=file_path,
            error_type=type(exc).__name__,
            error=str(exc),
        )
        return ""


def run_ocr(file_path: str, content_type: ContentType) -> str:
    """
    OCR для PDF (страницы → изображения → Tesseract) и изображений.
    Вызывается, когда текстовый слой отсутствует или пуст.
    """
    path = Path(file_path)
    if not path.exists():
        logger.warning("ocr_skipped", reason="file_not_found", file_path=file_path)
        return ""

    if content_type == "pdf":
        try:
            import os
            poppler_path = None
            if platform.system() == "Windows":
                user_profile = os.environ.get("USERPROFILE", r"C:\Users\user2")
                candidates = [
                    os.path.join(user_profile, r"poppler\poppler-24.08.0\Library\bin"),
                    r"C:\poppler\poppler-24.08.0\Library\bin",
                    r"C:\Users\user2\poppler\poppler-24.08.0\Library\bin",
                ]
                for c in candidates:
                    if os.path.exists(c):
                        poppler_path = c
                        break
            images = convert_from_path(path, dpi=200, poppler_path=poppler_path)
        except Exception as exc:
            logger.exception(
                "ocr_failed",
                content_type=content_type,
                file_path=file_path,
                stage="pdf2image",
                error_type=type(exc).__name__,
                error=str(exc),
            )
            return ""
        text_parts: list[str] = []
        for i, img in enumerate(images):
            try:
                page_text = pytesseract.image_to_string(img, lang=TESSERACT_LANG)
                text_parts.append(page_text or "")
            except Exception as exc:
                logger.warning(
                    "ocr_page_failed",
                    content_type=content_type,
                    file_path=file_path,
                    page_index=i,
                    error_type=type(exc).__name__,
                    error=str(exc),
                )
                text_parts.append("")
        return "\n".join(text_parts).strip()

    if content_type == "image":
        try:
            img = Image.open(path)
            return pytesseract.image_to_string(img, lang=TESSERACT_LANG).strip()
        except Exception as exc:
            logger.exception(
                "ocr_failed",
                content_type=content_type,
                file_path=file_path,
                stage="image_tesseract",
                error_type=type(exc).__name__,
                error=str(exc),
            )
            return ""

    return ""


def run_ocr_with_engine(file_path: str, content_type: ContentType) -> tuple[str, TextSource]:
    """
    Выполняет OCR с использованием выбранного движка (glm или tesseract).
    Применяет цепочки откатов (fallbacks) при возникновении сбоев:
    - glm -> tesseract (прямой откат)
    Возвращает (текст, источник_текста).
    """
    from ocr_platform.config.settings import get_settings
    settings = get_settings()
    engine = settings.ocr_engine.lower()

    # Ступень 1: GLM OCR (удаленный GPU сервер)
    if engine == "glm":
        try:
            from ocr_platform.services.glm_ocr_service import run_glm_ocr
            text = run_glm_ocr(file_path)
            if text.strip():
                return text, "glm"
            logger.warning("glm_ocr_returned_empty_text", file_path=file_path)
        except Exception as exc:
            logger.warning(
                "glm_ocr_failed_falling_back_to_tesseract",
                file_path=file_path,
                error=str(exc),
            )
        # Если свалился glm, переходим напрямую к Tesseract
        engine = "tesseract"

    # Ступень 3: Tesseract (легкий локальный OCR)
    return run_ocr(file_path, content_type), "ocr"


def extract_text_at_ingest(
    file_path: str,
    content_type: ContentType,
    *,
    document_id: str | None = None,
    pipeline_run_id: str | None = None,
) -> tuple[str, bool, float | None, TextSource]:
    """
    Единая точка извлечения текста на входе пайплайна.
    Возвращает (текст, ocr_was_used, ocr_latency_ms, text_source).
    PDF: pdfplumber → pymupdf → OCR.
    Image: OCR.
    Text: чтение файла.
    """
    path = Path(file_path)
    if not path.exists():
        return "", False, None, "pdfplumber"

    if content_type == "pdf":
        # 1. pdfplumber (текстовый слой)
        text = extract_text_from_pdf(file_path)
        if text.strip():
            logger.info(
                "text_extraction_source",
                source="pdfplumber",
                document_id=document_id,
                pipeline_run_id=pipeline_run_id,
                text_length=len(text),
            )
            return text, False, None, "pdfplumber"

        # 2. PyMuPDF (может найти текст там, где pdfplumber не справился)
        logger.info(
            "text_extraction_step",
            step="pymupdf",
            reason="pdfplumber_empty",
            document_id=document_id,
            pipeline_run_id=pipeline_run_id,
        )
        text = extract_text_with_pymupdf(file_path)
        if text.strip():
            logger.info(
                "text_extraction_source",
                source="pymupdf",
                document_id=document_id,
                pipeline_run_id=pipeline_run_id,
                text_length=len(text),
            )
            return text, False, None, "pymupdf"

        # 3. OCR (с выбором движка и fallback-ом)
        logger.info(
            "text_extraction_step",
            step="ocr",
            reason="pymupdf_empty",
            document_id=document_id,
            pipeline_run_id=pipeline_run_id,
        )
        t0 = perf_counter()
        text, text_source = run_ocr_with_engine(file_path, content_type)
        ocr_latency_ms = (perf_counter() - t0) * 1000.0
        logger.info(
            "text_extraction_source",
            source=text_source,
            document_id=document_id,
            pipeline_run_id=pipeline_run_id,
            latency_ms=round(ocr_latency_ms, 2),
            text_length=len(text),
        )
        return text, True, ocr_latency_ms, text_source

    if content_type == "image":
        logger.info(
            "text_extraction_step",
            step="ocr",
            reason="image_content",
            document_id=document_id,
            pipeline_run_id=pipeline_run_id,
        )
        t0 = perf_counter()
        text, text_source = run_ocr_with_engine(file_path, content_type)
        ocr_latency_ms = (perf_counter() - t0) * 1000.0
        logger.info(
            "text_extraction_source",
            source=text_source,
            document_id=document_id,
            pipeline_run_id=pipeline_run_id,
            latency_ms=round(ocr_latency_ms, 2),
            text_length=len(text),
        )
        return text, True, ocr_latency_ms, text_source

    if content_type == "text":
        try:
            return path.read_text(encoding="utf-8", errors="replace").strip(), False, None, "text"
        except Exception as exc:
            logger.warning(
                "text_file_read_failed",
                file_path=file_path,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            return "", False, None, "text"

    return "", False, None, "pdfplumber"


def run_ocr_if_needed(file_path: str, content_type: ContentType, existing_text: str | None = None) -> str:
    """
    Возвращает existing_text, если он не пуст. Иначе — OCR.
    Оставлено для обратной совместимости; основной сценарий — extract_text_at_ingest.
    """
    if existing_text and existing_text.strip():
        return existing_text
    return run_ocr(file_path, content_type)


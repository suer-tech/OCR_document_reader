from unittest.mock import MagicMock, patch

import pytest

from ocr_platform.config.settings import Settings
from ocr_platform.services import ocr_service


def test_run_ocr_with_engine_tesseract():
    """
    Проверяет, что при OCR_ENGINE="tesseract" вызывается стандартный Tesseract.
    """
    mock_settings = Settings()
    mock_settings.ocr_engine = "tesseract"

    with patch("ocr_platform.config.settings.get_settings", return_value=mock_settings), \
         patch("ocr_platform.services.ocr_service.run_ocr", return_value="Tesseract Text") as mock_run_ocr:

        text, source = ocr_service.run_ocr_with_engine("dummy_path.pdf", "pdf")

        assert text == "Tesseract Text"
        assert source == "ocr"
        mock_run_ocr.assert_called_once_with("dummy_path.pdf", "pdf")


def test_run_ocr_with_engine_docling_success():
    """
    Проверяет, что при OCR_ENGINE="docling" вызывается Docling и возвращает текст.
    """
    mock_settings = Settings()
    mock_settings.ocr_engine = "docling"

    with patch("ocr_platform.config.settings.get_settings", return_value=mock_settings), \
         patch("ocr_platform.services.docling_service.run_docling_ocr", return_value="Docling Markdown Text") as mock_docling_ocr:

        text, source = ocr_service.run_ocr_with_engine("dummy_path.pdf", "pdf")

        assert text == "Docling Markdown Text"
        assert source == "docling"
        mock_docling_ocr.assert_called_once_with("dummy_path.pdf")


def test_run_ocr_with_engine_docling_fallback_on_error():
    """
    Проверяет, что если Docling падает с ошибкой, происходит автоматический откат на Tesseract.
    """
    mock_settings = Settings()
    mock_settings.ocr_engine = "docling"

    with patch("ocr_platform.config.settings.get_settings", return_value=mock_settings), \
         patch("ocr_platform.services.docling_service.run_docling_ocr", side_effect=RuntimeError("Docling failed")), \
         patch("ocr_platform.services.ocr_service.run_ocr", return_value="Tesseract Fallback Text") as mock_run_ocr:

        text, source = ocr_service.run_ocr_with_engine("dummy_path.pdf", "pdf")

        assert text == "Tesseract Fallback Text"
        assert source == "ocr"
        mock_run_ocr.assert_called_once_with("dummy_path.pdf", "pdf")


def test_run_ocr_with_engine_docling_fallback_on_empty():
    """
    Проверяет, что если Docling возвращает пустой текст, происходит откат на Tesseract.
    """
    mock_settings = Settings()
    mock_settings.ocr_engine = "docling"

    with patch("ocr_platform.config.settings.get_settings", return_value=mock_settings), \
         patch("ocr_platform.services.docling_service.run_docling_ocr", return_value=""), \
         patch("ocr_platform.services.ocr_service.run_ocr", return_value="Tesseract Fallback Text") as mock_run_ocr:

        text, source = ocr_service.run_ocr_with_engine("dummy_path.pdf", "pdf")

        assert text == "Tesseract Fallback Text"
        assert source == "ocr"
        mock_run_ocr.assert_called_once_with("dummy_path.pdf", "pdf")

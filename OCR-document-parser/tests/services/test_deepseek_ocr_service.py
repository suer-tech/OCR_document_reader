from unittest.mock import MagicMock, patch

import pytest

from ocr_platform.config.settings import Settings
from ocr_platform.services import ocr_service


def test_run_ocr_with_engine_deepseek_success():
    """
    Проверяет, что при OCR_ENGINE="deepseek" вызывается удаленный DeepSeek OCR.
    """
    mock_settings = Settings()
    mock_settings.ocr_engine = "deepseek"
    mock_settings.ollama_ocr_url = "http://fake-ollama:11434"

    with patch("ocr_platform.config.settings.get_settings", return_value=mock_settings), \
         patch("ocr_platform.services.deepseek_ocr_service.run_deepseek_ocr", return_value="DeepSeek OCR Text") as mock_deepseek_ocr:

        text, source = ocr_service.run_ocr_with_engine("dummy_path.png", "image")

        assert text == "DeepSeek OCR Text"
        assert source == "deepseek"
        mock_deepseek_ocr.assert_called_once_with("dummy_path.png")


def test_run_ocr_with_engine_deepseek_fallback_to_tesseract():
    """
    Проверяет, что если DeepSeek падает с ошибкой, происходит автоматический откат сразу на Tesseract.
    """
    mock_settings = Settings()
    mock_settings.ocr_engine = "deepseek"

    with patch("ocr_platform.config.settings.get_settings", return_value=mock_settings), \
         patch("ocr_platform.services.deepseek_ocr_service.run_deepseek_ocr", side_effect=RuntimeError("DeepSeek server down")), \
         patch("ocr_platform.services.ocr_service.run_ocr", return_value="Tesseract Fallback Text") as mock_run_ocr:

        text, source = ocr_service.run_ocr_with_engine("dummy_path.png", "image")

        assert text == "Tesseract Fallback Text"
        assert source == "ocr"
        mock_run_ocr.assert_called_once_with("dummy_path.png", "image")

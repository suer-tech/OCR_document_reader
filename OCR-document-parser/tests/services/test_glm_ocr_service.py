from unittest.mock import MagicMock, patch

import pytest

from ocr_platform.config.settings import Settings
from ocr_platform.services import ocr_service


def test_run_ocr_with_engine_glm_success():
    """
    Проверяет, что при OCR_ENGINE="glm" вызывается удаленный GLM OCR.
    """
    mock_settings = Settings()
    mock_settings.ocr_engine = "glm"
    mock_settings.ollama_ocr_url = "http://fake-ollama:11434"

    with patch("ocr_platform.config.settings.get_settings", return_value=mock_settings), \
         patch("ocr_platform.services.glm_ocr_service.run_glm_ocr", return_value="GLM OCR Text") as mock_glm_ocr:

        text, source = ocr_service.run_ocr_with_engine("dummy_path.png", "image")

        assert text == "GLM OCR Text"
        assert source == "glm"
        mock_glm_ocr.assert_called_once_with("dummy_path.png")


def test_run_ocr_with_engine_glm_fallback_to_tesseract():
    """
    Проверяет, что если GLM падает с ошибкой, происходит автоматический откат сразу на Tesseract.
    """
    mock_settings = Settings()
    mock_settings.ocr_engine = "glm"

    with patch("ocr_platform.config.settings.get_settings", return_value=mock_settings), \
         patch("ocr_platform.services.glm_ocr_service.run_glm_ocr", side_effect=RuntimeError("GLM server down")), \
         patch("ocr_platform.services.ocr_service.run_ocr", return_value="Tesseract Fallback Text") as mock_run_ocr:

        text, source = ocr_service.run_ocr_with_engine("dummy_path.png", "image")

        assert text == "Tesseract Fallback Text"
        assert source == "ocr"
        mock_run_ocr.assert_called_once_with("dummy_path.png", "image")

from __future__ import annotations

import base64
import io
from pathlib import Path
import platform
import requests
from PIL import Image
from pdf2image import convert_from_path

from ocr_platform.config.settings import get_settings
from ocr_platform.observability.logging import get_logger

logger = get_logger(__name__)


def run_deepseek_ocr(file_path: str) -> str:
    """
    Выполняет OCR с использованием удаленной VLM-модели DeepSeek OCR через Ollama API.
    Поддерживает как изображения, так и PDF (автоматически конвертирует страницы PDF в картинки).
    """
    path = Path(file_path)
    if not path.exists():
        logger.warning("deepseek_ocr_skipped", reason="file_not_found", file_path=file_path)
        return ""

    settings = get_settings()
    base_url = settings.ollama_ocr_url.rstrip('/')
    if base_url.endswith("/api/chat"):
        url = base_url
    else:
        url = f"{base_url}/api/chat"
    model = settings.ollama_ocr_model

    logger.info("running_remote_deepseek_ocr", file_path=file_path, url=url, model=model)

    # 1. Определяем тип документа
    is_pdf = path.suffix.lower() == ".pdf"
    
    images_to_process: list[Image.Image] = []
    if is_pdf:
        try:
            # Для Windows локального запуска может понадобиться poppler_path, 
            # но в Docker-контейнере poppler-utils прописан в системном PATH.
            poppler_path = r"C:\poppler\poppler-24.08.0\Library\bin" if platform.system() == "Windows" else None
            images_to_process = convert_from_path(path, dpi=200, poppler_path=poppler_path)
            logger.info("deepseek_ocr_pdf_converted", pages=len(images_to_process))
        except Exception as exc:
            logger.exception("deepseek_ocr_pdf_conversion_failed", file_path=file_path, error=str(exc))
            raise RuntimeError(f"Failed to convert PDF to images for DeepSeek: {exc}") from exc
    else:
        try:
            images_to_process = [Image.open(path)]
        except Exception as exc:
            logger.exception("deepseek_ocr_image_load_failed", file_path=file_path, error=str(exc))
            raise RuntimeError(f"Failed to load image for DeepSeek: {exc}") from exc

    extracted_pages: list[str] = []
    
    headers = {}
    if settings.ollama_ocr_token:
        headers["Authorization"] = f"Bearer {settings.ollama_ocr_token}"

    for i, img in enumerate(images_to_process):
        try:
            # Сохраняем PIL Image в байты JPEG
            buf = io.BytesIO()
            img.save(buf, format="JPEG")
            img_bytes = buf.getvalue()
            
            b64_image = base64.b64encode(img_bytes).decode("utf-8")

            payload = {
                "model": model,
                "messages": [
                    {
                        "role": "user",
                        "content": "Extract the text in the image.",
                        "images": [b64_image]
                    }
                ],
                "stream": False
            }

            # Таймаут 60 секунд на одну страницу
            response = requests.post(url, json=payload, headers=headers, timeout=60.0)
            
            if response.status_code != 200:
                logger.error(
                    "deepseek_ocr_page_http_error",
                    page=i,
                    status_code=response.status_code,
                    response_text=response.text,
                )
                raise RuntimeError(f"Ollama server returned status code {response.status_code} for page {i}")

            resp_data = response.json()
            page_text = resp_data.get("message", {}).get("content", "").strip()
            extracted_pages.append(page_text)
            logger.info("deepseek_ocr_page_completed", page=i, text_length=len(page_text))
            
        except Exception as exc:
            logger.exception("deepseek_ocr_page_failed", page=i, error=str(exc))
            raise RuntimeError(f"DeepSeek OCR failed on page {i}: {exc}") from exc

    extracted_text = "\n\n".join(extracted_pages).strip()
    
    logger.info(
        "deepseek_ocr_completed",
        file_path=file_path,
        pages_total=len(images_to_process),
        text_length=len(extracted_text),
    )
    return extracted_text

from __future__ import annotations

import base64
import io
import time as _time
from pathlib import Path
import platform
import requests
from PIL import Image
from pdf2image import convert_from_path

from ocr_platform.config.settings import get_settings
from ocr_platform.observability.logging import get_logger

import concurrent.futures

logger = get_logger(__name__)

def _process_page(i: int, img: Image.Image, model: str, url: str, headers: dict, timeout: float, max_page_retries: int) -> str:
    last_exception = None
    for attempt in range(max_page_retries):
        try:
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
                "stream": False,
                "options": {
                    "num_ctx": 8192,
                    "num_predict": 4096,
                    "temperature": 0.1,
                    "repeat_penalty": 1.2,
                    "repeat_last_n": 128
                }
            }

            response = requests.post(url, json=payload, headers=headers, timeout=timeout)
            
            if response.status_code != 200:
                raise RuntimeError(
                    f"Ollama server returned status code {response.status_code} for page {i}"
                )

            resp_data = response.json()
            page_text = resp_data.get("message", {}).get("content", "").strip()
            logger.info(
                "glm_ocr_page_completed",
                page=i,
                attempt=attempt + 1,
                text_length=len(page_text),
            )
            return page_text

        except Exception as exc:
            last_exception = exc
            if attempt < max_page_retries - 1:
                backoff = 2.0 ** attempt
                logger.warning(
                    "glm_ocr_page_retry",
                    page=i,
                    attempt=attempt + 1,
                    max_retries=max_page_retries,
                    backoff_seconds=backoff,
                    error=str(exc),
                )
                _time.sleep(backoff)
            else:
                logger.error(
                    "glm_ocr_page_failed_all_retries",
                    page=i,
                    attempts=max_page_retries,
                    error=str(exc),
                )

    raise RuntimeError(
        f"GLM OCR failed on page {i} after {max_page_retries} retries: {last_exception}"
    ) from last_exception



def run_glm_ocr(file_path: str) -> str:
    """
    Выполняет OCR с использованием удаленной VLM-модели GLM OCR через Ollama API.
    Поддерживает как изображения, так и PDF (автоматически конвертирует страницы PDF в картинки).
    При ошибке на странице выполняет retry (по настройке glm_page_retries).
    Только после исчерпания всех retry — переходит к fallback-движку (Tesseract).
    """
    path = Path(file_path)
    if not path.exists():
        logger.warning("glm_ocr_skipped", reason="file_not_found", file_path=file_path)
        return ""

    settings = get_settings()
    base_url = settings.ollama_ocr_url.rstrip('/')
    if base_url.endswith("/api/chat"):
        url = base_url
    else:
        url = f"{base_url}/api/chat"
    model = settings.ollama_ocr_model
    timeout = settings.glm_timeout_seconds
    max_page_retries = settings.glm_page_retries

    logger.info("running_remote_glm_ocr", file_path=file_path, url=url, model=model, timeout=timeout)

    # 1. Определяем тип документа
    is_pdf = path.suffix.lower() == ".pdf"
    
    images_to_process: list[Image.Image] = []
    if is_pdf:
        try:
            # Для Windows локального запуска может понадобиться poppler_path, 
            # но в Docker-контейнере poppler-utils прописан в системном PATH.
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
            images_to_process = convert_from_path(path, dpi=200, poppler_path=poppler_path)
            logger.info("glm_ocr_pdf_converted", pages=len(images_to_process))
        except Exception as exc:
            logger.exception("glm_ocr_pdf_conversion_failed", file_path=file_path, error=str(exc))
            raise RuntimeError(f"Failed to convert PDF to images for GLM: {exc}") from exc
    else:
        try:
            images_to_process = [Image.open(path)]
        except Exception as exc:
            logger.exception("glm_ocr_image_load_failed", file_path=file_path, error=str(exc))
            raise RuntimeError(f"Failed to load image for GLM: {exc}") from exc

    extracted_pages: list[str] = []
    
    headers = {}
    if settings.ollama_ocr_token:
        headers["Authorization"] = f"Bearer {settings.ollama_ocr_token}"

    if images_to_process:
        max_workers = min(len(images_to_process), 10)
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [
                executor.submit(_process_page, i, img, model, url, headers, timeout, max_page_retries)
                for i, img in enumerate(images_to_process)
            ]
            for future in futures:
                extracted_pages.append(future.result())

    extracted_text = "\n\n".join(extracted_pages).strip()
    
    logger.info(
        "glm_ocr_completed",
        file_path=file_path,
        pages_total=len(images_to_process),
        text_length=len(extracted_text),
    )
    return extracted_text

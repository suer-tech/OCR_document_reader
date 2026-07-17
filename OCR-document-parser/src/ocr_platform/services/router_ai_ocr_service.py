from __future__ import annotations

import base64
import os
import re
import httpx
from pathlib import Path
from langfuse.openai import OpenAI

from ocr_platform.config.settings import get_settings
from ocr_platform.observability.logging import get_logger

logger = get_logger(__name__)


def _clean_ocr_output(text: str) -> str:
    text = text.strip()
    text = re.sub(r'^```(?:json)?\s*', '', text)
    text = re.sub(r'\s*```$', '', text)
    text = re.sub(r'\{"box_2d":\s*\[[^\]]*\],\s*"text_content":\s*"([^"]*)"\}', r'\1', text)
    text = re.sub(r'^\s*\[[\s\S]*?\]\s*$', '', text)
    lines = []
    for line in text.split('\n'):
        line = line.strip()
        if not line or line in ('[', ']', '{', '}', ','):
            continue
        lines.append(line)
    return '\n'.join(lines).strip()

def run_router_ai_ocr(file_path: str, ocr_config: dict | None = None) -> str:
    """
    Выполняет OCR с использованием удаленной модели через RouterAI API (напр. Gemini 2.5).
    Поддерживает как изображения, так и PDF (отправляются целиком).
    """
    path = Path(file_path)
    if not path.exists():
        logger.warning("router_ai_ocr_skipped", reason="file_not_found", file_path=file_path)
        return ""

    settings = get_settings()
    base_url = (
        os.environ.get("OCR_ROUTER_AI_BASE_URL")
        or settings.router_ai_base_url
        or "https://routerai.ru/api/v1"
    )
    api_key = os.environ.get("OCR_ROUTER_AI_API_KEY") or settings.router_ai_api_key
    if not api_key:
        logger.error("router_ai_ocr_no_api_key")
        raise ValueError("OCR_ROUTER_AI_API_KEY is not set.")

    if ocr_config:
        model = ocr_config.get("model", settings.router_ai_ocr_model)
        timeout = float(ocr_config.get("timeout_seconds", settings.router_ai_ocr_timeout_seconds))
    else:
        model = settings.router_ai_ocr_model
        timeout = settings.router_ai_ocr_timeout_seconds

    logger.info("running_router_ai_ocr", file_path=file_path, url=base_url, model=model, timeout=timeout)

    try:
        with open(path, "rb") as f:
            file_b64 = base64.b64encode(f.read()).decode("utf-8")
    except Exception as e:
        logger.exception("router_ai_ocr_read_failed", file_path=file_path, error=str(e))
        raise RuntimeError(f"Failed to read file for RouterAI OCR: {e}") from e

    is_pdf = path.suffix.lower() == ".pdf"
    mime_type = "application/pdf" if is_pdf else "image/jpeg"

    http_client = httpx.Client(timeout=timeout)
    client = OpenAI(
        base_url=base_url, api_key=api_key, http_client=http_client
    )

    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{mime_type};base64,{file_b64}"
                            },
                        },
                        {
                            "type": "text",
                            "text": "Extract all text from this document accurately. Return ONLY the plain text content without any bounding boxes, coordinates, JSON formatting, or markdown. Just raw text preserving line breaks and paragraph structure."
                        },
                    ],
                }
            ],
            max_tokens=32768,
            temperature=0.0,
        )
        extracted = resp.choices[0].message.content
        if extracted and extracted.strip():
            cleaned = _clean_ocr_output(extracted)
            logger.info("router_ai_ocr_succeeded", file_path=file_path, text_length=len(cleaned))
            return cleaned
        logger.warning("router_ai_ocr_empty_response", file_path=file_path)
        return ""
    except Exception as e:
        logger.exception("router_ai_ocr_api_failed", file_path=file_path, error=str(e))
        raise RuntimeError(f"RouterAI OCR failed: {e}") from e
    finally:
        http_client.close()

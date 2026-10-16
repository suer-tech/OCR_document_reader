"""One multimodal request per passport: original pages -> structured fields."""
from __future__ import annotations

import asyncio
import base64
import io
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import pymupdf
from PIL import Image, ImageOps, ImageSequence

from ocr_platform.config.settings import get_settings
from ocr_platform.services.agent_tools import search_postal_index_by_address


PASSPORT_PROFILES = {"passport_main", "passport_registration"}


def _normalize_passport_value(name: str, value: str) -> str | None:
    """Normalize presentation only; never complete or modernize a printed value."""
    if name in {"last_name", "first_name", "patronymic"}:
        value = re.sub(r"\s+", " ", value).strip()
        # Case only: preserve letters, apostrophes, hyphens and compound names.
        value = re.sub(r"[^\W\d_]+", lambda m: m[0].capitalize(), value)
        if name == "patronymic":
            value = re.sub(r"(?<= )(?:оглы|кызы|уулу)$", lambda m: m[0].lower(),
                           value, flags=re.IGNORECASE)
        return value or None
    if name in {"birth_date", "issue_date"}:
        value = value.strip()
        if not re.fullmatch(r"[0-9]{2}\.[0-9]{2}\.[0-9]{4}", value):
            return None
        try:
            datetime.strptime(value, "%d.%m.%Y")
        except ValueError:
            return None
        return value
    if name == "gender":
        return {
            "мужской": "Мужской", "муж": "Мужской", "муж.": "Мужской", "м": "Мужской",
            "женский": "Женский", "жен": "Женский", "жен.": "Женский", "ж": "Женский",
        }.get(value.strip().casefold())
    if name == "street":
        value = re.sub(r"\s+", " ", value).strip()
        # Do not guess where the street ends by cutting at the first number:
        # 'ул. 8 Марта', 'улица 1905 года' and '4-й проезд' are valid names.
        if re.search(
            r"(?<!\w)(?:д|дом|к|корп|корпус|стр|строение|"
            r"кв|квартира|ком|комната|помещ|помещение)\b\.?\s*[:№]?\s*[0-9]"
            r"|(?<![0-9])[0-9]{6}(?![0-9])",
            value, flags=re.IGNORECASE,
        ):
            return None
        return value or None
    if name not in {"issued_by", "birth_place", "registration_address"}:
        return value.strip() or None
    if name == "registration_address":
        # A wrapped street name is not a new component. Separate only lines
        # starting with explicit address labels; otherwise join with a space.
        value = re.sub(
            r"\s*[\r\n]+\s*(?=(?:ул\.|улица\b|г\.|город\b|д\.|дом\b|к\.|корп\.|"
            r"корпус\b|стр\.|строение\b|кв\.|квартира\b|ком\.|комната\b)\s)",
            ", ", value, flags=re.IGNORECASE,
        )
    value = re.sub(r"\s+", " ", value).strip()
    value = re.sub(r"\s*,\s*", ", ", value)
    value = re.sub(r"(?:,\s*){2,}", ", ", value).strip(" ,")
    if name in {"issued_by", "birth_place"}:
        value = value.upper()
    if name == "issued_by":
        value = re.sub(r"№(?:\s*№)+", "№", value)
    if name == "registration_address":
        # Only separate explicitly labelled numbered parts; never infer numbers,
        # or interpret a settlement such as 'д. Ивановка' as a house.
        value = re.sub(
            r"(?<=[\w\)])\s+(?=(?:д\.|дом\b|к\.|корп\.|корпус\b|стр\.|строение\b|"
            r"кв\.|квартира\b|ком\.|комната\b|помещ\.|помещение\b)\s*№?\s*[0-9])",
            ", ", value, flags=re.IGNORECASE,
        )
    return value or None


def is_direct_vision_profile(profile: dict[str, Any]) -> bool:
    return (
        profile.get("profile_id") in PASSPORT_PROFILES
        and profile.get("extraction_mode") == "direct_vision"
    )


def _image_part(image: Image.Image) -> dict:
    image = ImageOps.exif_transpose(image).convert("RGB")
    image.thumbnail((2200, 2200), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return {
        "type": "image_url",
        "image_url": {
            "url": "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii"),
            "detail": "high",
        },
    }


def _document_images(storage_path: str) -> list[dict]:
    """Render every PDF/TIFF page locally; never select only the first page."""
    path = Path(storage_path)
    if not path.is_file():
        raise ValueError("Passport source file is missing")
    with path.open("rb") as source:
        is_pdf = source.read(5) == b"%PDF-"
    parts = []
    if is_pdf:
        with pymupdf.open(path) as document:
            if document.needs_pass:
                raise ValueError("Password-protected passport PDF is not supported")
            for page in document:
                scale = 2200 / max(page.rect.width, page.rect.height)
                pixmap = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
                with Image.open(io.BytesIO(pixmap.tobytes("png"))) as image:
                    parts.append(_image_part(image))
    else:
        with Image.open(path) as image:
            for frame in ImageSequence.Iterator(image):
                parts.append(_image_part(frame.copy()))
    if not parts:
        raise ValueError("Passport document contains no pages")
    return parts


def _prompt(profile: dict[str, Any]) -> str:
    fields = profile["fields"]
    page_kind = (
        "MAIN SPREAD: personal data and issuing authority"
        if profile["profile_id"] == "passport_main"
        else "REGISTRATION: current registration address. Ignore superseded registration stamps. "
             "Derive region, city and street only from the selected current registration_address; "
             "street must contain only the street type and name, not the house or apartment"
    )
    instructions = []
    for name, config in fields.items():
        instruction = config.get("prompt_instruction", "")
        if name == "post_index":
            instruction = "Return null. Postal index will be verified by application code, never guess it."
        instructions.append(f"{name}: {instruction}")
    shape = {name: None for name in fields}
    shape["raw_text"] = ""
    return (
        "Read the Russian internal passport directly from the attached original page images. "
        f"Extract fields for the {page_kind}. Pages may be rotated, contain a cover or blank margins. "
        "Locate the relevant passport pages yourself; read every line of multi-line values. "
        "Do not combine different people's passports into a single identity. "
        "Transcribe visible Cyrillic faithfully, including unusual names; do not replace letters "
        "or spellings with common names. Exclude labels, hologram words and decorative backgrounds. "
        "Repeated numbers and MRZ may corroborate reading, but printed Cyrillic is the source for names. "
        "Preserve complete birthplace and issuing authority, historical names, abbreviations and grammar. "
        "For issued_by and birth_place use uppercase, joining wrapped lines with spaces. "
        "For birth_place preserve printed punctuation; do not invent commas or geographic details. "
        "For registration_address return one line with commas between address components. "
        "Preserve settlement types, districts, microdistricts, SNT, house, corpus, building, letter, "
        "apartment and room when visible. Do not include empty stamp labels or the authority stamping "
        "the registration. Keep the printed spelling and do not add a guessed postal index to the address. "
        "Use null when a field cannot be read reliably. Never invent a value. "
        "Return only one JSON object, exactly the keys shown below. Field values must be strings or null; "
        "identifiers are strings with leading zeroes preserved. Series: four digits without spaces; "
        "number: six digits; dates: DD.MM.YYYY; department code: DDD-DDD. "
        "In the SAME response, raw_text must contain a faithful transcription of the relevant passport "
        "pages for review. It is not a separate OCR task or request. Use an empty raw_text only if no text is readable.\n\n"
        + "\n".join(instructions)
        + "\n\nJSON shape:\n" + json.dumps(shape, ensure_ascii=False)
    )


def _parse_fields(content: str, profile: dict[str, Any]) -> dict:
    try:
        data = json.loads(content)
    except (ValueError, TypeError) as exc:
        raise ValueError("Passport vision returned invalid JSON") from exc
    expected = set(profile["fields"]) | {"raw_text"}
    if not isinstance(data, dict) or set(data) != expected:
        raise ValueError("Passport vision returned unexpected or missing fields")
    raw_text = data.pop("raw_text")
    if not isinstance(raw_text, str):
        raise ValueError("Passport vision raw_text must be a string")
    results = {}
    for name, value in data.items():
        if value is not None and not isinstance(value, str):
            raise ValueError(f"Passport vision field {name} must be a string or null")
        original_value = value
        if value is not None:
            value = _normalize_passport_value(name, value)
        if name in {"passport_series", "passport_number", "department_code", "post_index"} and value:
            value = re.sub(r"\s+", "", value)
            patterns = {"passport_series": r"[0-9]{4}", "passport_number": r"[0-9]{6}",
                        "department_code": r"[0-9]{3}-?[0-9]{3}", "post_index": r"[0-9]{6}"}
            if not re.fullmatch(patterns[name], value):
                # Keep invalid readings out of the automatic result, without guessing digits.
                value = None
            elif name == "department_code" and "-" not in value:
                value = value[:3] + "-" + value[3:]
        results[name] = {
            "value": value,
            "confidence": None,
            "reasoning": (
                "Значение отклонено проверкой формата; требуется ручная проверка исходного текста."
                if isinstance(original_value, str) and original_value.strip() and value is None
                else "Прямое извлечение из изображения паспорта за один запрос."
            ),
            "source": "passport_vision",
        }
    if any(f["value"] for f in results.values()) and not raw_text.strip():
        raise ValueError("Passport vision returned fields without reviewable raw_text")
    results["_raw_text"] = raw_text
    return results


def _verify_postal_index(results: dict) -> None:
    """Preserve the existing reference lookup, without another model call."""
    if "post_index" not in results:
        return
    address = results.get("registration_address", {}).get("value")
    value = None
    source = "registration_address"
    if address:
        match = re.search(r"(?<!\d)(\d{6})(?!\d)", address)
        if not match:
            source = "tool_fallback"
            try:
                lookup = search_postal_index_by_address(None, address)
            except Exception:
                lookup = ""
            match = re.search(r"(?<!\d)(\d{6})(?!\d)", lookup or "")
        value = match.group(1) if match else None
    results["post_index"] = {
        "value": value,
        "confidence": (1.0 if source == "registration_address" else 0.9) if value else 0.0,
        "reasoning": "Почтовый индекс проверен по адресу регистрации и справочному поиску.",
        "source": source,
    }


async def extract_passport_fields(storage_path: str, profile: dict[str, Any]) -> dict:
    if not is_direct_vision_profile(profile):
        raise ValueError("Direct vision is only enabled for configured passport profiles")
    config = profile.get("models", {}).get("llm_extraction", {})
    if config.get("provider") != "router_ai" or not config.get("model"):
        raise ValueError("Passport direct vision requires a configured RouterAI model")
    settings = get_settings()
    if not settings.router_ai_api_key:
        raise ValueError("OCR_ROUTER_AI_API_KEY is not configured")
    images = await asyncio.to_thread(_document_images, storage_path)
    payload = {
        "model": config["model"],
        "messages": [{"role": "user", "content": [{"type": "text", "text": _prompt(profile)}, *images]}],
        "response_format": {"type": "json_object"},
        "max_tokens": int(config.get("max_tokens", 8192)),
        "reasoning_effort": config.get("reasoning_effort", "low"),
    }
    if "temperature" in config:
        payload["temperature"] = float(config["temperature"])
    # Plain HTTP has no SDK/tool/schema retries: a failed response fails the run.
    async with httpx.AsyncClient(timeout=float(config.get("timeout_seconds", 180))) as client:
        response = await client.post(
            settings.router_ai_base_url.rstrip("/") + "/chat/completions",
            headers={"Authorization": "Bearer " + settings.router_ai_api_key},
            json=payload,
        )
        response.raise_for_status()
        body = response.json()
    try:
        choice = body["choices"][0]
        if choice.get("finish_reason") != "stop":
            raise ValueError("Passport vision response did not finish successfully")
        content = choice["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError("Passport vision returned an invalid response") from exc
    results = _parse_fields(content, profile)
    if profile["profile_id"] == "passport_registration":
        await asyncio.to_thread(_verify_postal_index, results)
    return results

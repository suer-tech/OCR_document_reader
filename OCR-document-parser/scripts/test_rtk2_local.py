"""Run the rtk2 extractor on local PDFs without the API, database, or queue."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ocr_platform.orchestration.router import load_profile
from ocr_platform.services.document_content_validation import validate_document_content
from ocr_platform.services.extraction_agent import run_agent_extraction
from ocr_platform.services.ocr_service import (
    extract_text_from_pdf,
    extract_text_with_pymupdf,
)


async def _run(files: list[Path], provider: str | None) -> None:
    profile = load_profile("rtk2")
    if provider:
        profile["models"]["llm_extraction"]["provider"] = provider

    results = []
    for path in files:
        validate_document_content(path.read_bytes(), "pdf")
        text = extract_text_from_pdf(str(path)) or extract_text_with_pymupdf(str(path))
        if not text.strip():
            raise ValueError(f"{path}: no readable text layer; this local test requires OCR")
        fields = await run_agent_extraction(
            text, profile["fields"], profile_id="rtk2", profile_config=profile
        )
        results.append({"file": str(path), "fields": fields})
    print(json.dumps(results, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="+", type=Path, help="Local PDF files to test")
    parser.add_argument(
        "--provider",
        choices=("router_ai", "openrouter"),
        help="Override the rtk2 LLM provider for this test only; document text is sent to it",
    )
    args = parser.parse_args()
    asyncio.run(_run(args.files, args.provider))


if __name__ == "__main__":
    main()

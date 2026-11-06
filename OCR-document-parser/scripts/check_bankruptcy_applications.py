"""Offline LOCAL-PARSER experiment (not the active Argon handler).

python scripts/check_bankruptcy_applications.py INPUT_DIR OUTPUT_DIR.

This deliberately tests the archived local parser regardless of the active
profile mode; use /documents/upload for the current Argon extraction pipeline.
No database or API server. By default, only native PDF/DOCX/text is read; a file
requiring OCR fails without a remote request. --allow-remote-ocr explicitly opts
into the configured Gemini OCR. Outputs contain personal data and should remain
in a private local directory, outside version control.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from ocr_platform.orchestration.router import load_profile
from ocr_platform.services.bankruptcy_application import PROFILE_ID, extract_fields
from ocr_platform.services.ocr_service import extract_text_at_ingest
from ocr_platform.services.quality_service import compute_quality_scores
from ocr_platform.services.validation_service import review_requirement, validate_fields


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_dir", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--allow-remote-ocr", action="store_true")
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    profile = load_profile(PROFILE_ID)
    rows = []
    types = {".pdf": "pdf", ".docx": "docx", ".txt": "text",
             ".png": "image", ".jpg": "image", ".jpeg": "image", ".tiff": "image"}
    for path in sorted(args.input_dir.iterdir()):
        if not path.is_file() or path.suffix.lower() not in types:
            continue
        row = {"file": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        try:
            if not args.allow_remote_ocr:
                from ocr_platform.services.ocr_service import extract_text_from_pdf, extract_text_with_pymupdf
                if types[path.suffix.lower()] == "image":
                    raise ValueError("Image requires --allow-remote-ocr")
                if path.suffix.lower() == ".pdf" and not (
                    extract_text_from_pdf(str(path)) or extract_text_with_pymupdf(str(path))
                ):
                    raise ValueError("PDF has no text; --allow-remote-ocr is required")
            text, used_ocr, latency, source = extract_text_at_ingest(
                str(path), types[path.suffix.lower()], ocr_config=profile["models"]["ocr"],
            )
            fields = extract_fields(text, profile)
            if used_ocr:
                fields["passport_number"]["validation_issue"] = "Шапка прочитана OCR; требуется сверка с изображением."
            status, issues = validate_fields(fields, PROFILE_ID, profile)
            _, _, overall = compute_quality_scores(text, fields)
            review, reason = review_requirement(overall, issues)
            row.update(text_source=source, ocr_used=used_ocr, ocr_latency_ms=latency, fields=fields,
                       validation_status=status, validation_issues=[i.model_dump() for i in issues],
                       human_review_required=review, human_review_reason=reason, raw_text=text)
        except Exception as exc:
            row.update(error=f"{type(exc).__name__}: {exc}")
        rows.append(row)
    (args.output_dir / "results.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    errors = sum("error" in row for row in rows)
    print(f"Processed {len(rows)} files; {errors} failures. Results: {args.output_dir / 'results.json'}")
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

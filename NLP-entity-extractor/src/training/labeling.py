from __future__ import annotations

import argparse
import json
from pathlib import Path

from api.services.pdf import extract_text_from_pdf_bytes
from inference.loader import DEFAULT_MODEL_DIR, load_extractor

from .dataset import write_jsonl


def build_record(pdf_path: Path, extractor, split: str = "train") -> dict:
    pdf_bytes = pdf_path.read_bytes()
    text = extract_text_from_pdf_bytes(pdf_bytes)
    prediction = extractor.predict(text)
    fields = prediction.fields
    return {
        "document_id": pdf_path.stem,
        "source_pdf": str(pdf_path),
        "text": text,
        "fio_normalized": fields.applicant_fio.normalized,
        "last_name": fields.applicant_fio.last_name,
        "first_name": fields.applicant_fio.first_name,
        "patronymic": fields.applicant_fio.patronymic,
        "judge_fio_normalized": fields.judge_fio.normalized,
        "court_name": fields.court_name,
        "case_number": fields.case_number,
        "inn": fields.inn,
        "decision_date": fields.decision_date,
        "procedure_end_date": fields.procedure_end_date,
        "procedure_type": fields.procedure_type,
        "span_start": None,
        "span_end": None,
        "split": split,
        "review_status": "needs_review",
        "suggested_confidence": prediction.confidence,
        "suggested_span": prediction.source_text_span,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Bootstrap annotation records from text PDFs using the local NLP model")
    parser.add_argument("pdf_dir", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--split", default="train")
    parser.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL_DIR)
    args = parser.parse_args()

    extractor = load_extractor(args.model_dir)
    pdf_paths = sorted(args.pdf_dir.glob("*.pdf"))
    rows = [build_record(path, extractor, split=args.split) for path in pdf_paths]
    write_jsonl(args.output, rows)
    print(json.dumps({"records": len(rows), "output": str(args.output), "model_dir": str(args.model_dir)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
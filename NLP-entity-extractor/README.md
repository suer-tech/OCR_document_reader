# NLP Entity Extractor

Local-first Python backend for extracting structured court-decision fields from Base64-encoded text PDF documents through a local NLP model.

## What Is Implemented

- FastAPI app with `/health` and `/extract`
- Base64 PDF decoding and text extraction via `pypdf`
- Local transformer-based NER inference using `Gherman/bert-base-NER-Russian`
- Morphological FIO normalization to nominative case via `pymorphy3`
- Dataset bootstrap CLI that pre-fills applicant FIO suggestions with the local model
- Fine-tuning pipeline for token classification on your corrected JSONL labels

## Environment

Project virtual environment:

- Windows PowerShell activation: `.\.venv\Scripts\Activate.ps1`
- Python: `.\.venv\Scripts\python.exe`

## Recommended Data Structure

- training PDFs: `data/raw/train_pdfs/`
- manual inference samples: `data/raw/inference_samples/`
- bootstrap labels: `data/processed/labels/bootstrap.jsonl`
- final splits: `data/processed/splits/`

## Training Workflow

1. Put many similar training PDFs into `data/raw/train_pdfs/`.
2. Build bootstrap labels:
   `python -m training.labeling data/raw/train_pdfs data/processed/labels/bootstrap.jsonl`
3. Review and correct `data/processed/labels/bootstrap.jsonl`.
4. Split approved rows automatically:
   `python -m training.split_dataset data/processed/labels/bootstrap.jsonl data/processed/splits`
5. Fine-tune the model:
   `python -m training.train data/processed/splits/train.jsonl --valid-data data/processed/splits/valid.jsonl`
6. Run the API on the resulting model for new documents.

## Important Clarification

- You do not need to run the API while preparing labels or training.
- You do not train one PDF at a time.
- You run labeling on a whole folder of PDFs, build one reviewed dataset, and train one reusable model.

## API Contract

`POST /extract`

```json
{
  "pdf_base64": "<base64-pdf>",
  "document_id": "optional-id"
}
```

Response:

```json
{
  "applicant_fio": {
    "last_name": "Олейников",
    "first_name": "Юрий",
    "patronymic": "Владимирович",
    "normalized": "Олейников Юрий Владимирович"
  },
  "judge_fio": {
    "last_name": "Глухова",
    "first_name": "Виктория",
    "patronymic": "Викторовна",
    "normalized": "Глухова Виктория Викторовна"
  },
  "court_name": "АРБИТРАЖНЫЙ СУД РОСТОВСКОЙ ОБЛАСТИ",
  "case_number": "А53-38537/2023",
  "inn": "611600763369",
  "decision_date": "2023-12-19",
  "procedure_end_date": "2024-06-17",
  "procedure_type": "процедуры реализации имущества гражданина",
  "confidence": 0.99,
  "source_text_span": "Олейникова Юрия Владимировича",
  "source_text_preview": "...",
  "model_version": "bootstrap-russian-ner",
  "document_id": "optional-id"
}
```

## How Labeling Works

See `data/annotation.md`.
Short version:

- keep source PDFs in `data/raw/train_pdfs/`
- keep `text` unchanged
- correct only `fio_normalized`, `last_name`, `first_name`, `patronymic`
- mark good rows as `approved`
- skip bad rows with `review_status = skip`

## Algorithm Reference

Detailed extraction and maintenance notes are documented in `docs/project/algorithm.md`.

## Notes

- Inference goes through the local NLP model, not heuristics.
- The bootstrap model is generic NER; quality should improve after fine-tuning on your own PDFs.
- Applicant FIO is normalized to nominative case in postprocessing.
- `procedure_end_date` is extracted from an explicit date when present, or derived from the decision date plus the stated procedure term such as `на срок 6 месяцев`.

## Extracted Fields

- applicant FIO
- judge FIO
- court name
- case number
- INN
- decision date
- procedure end date
- procedure type

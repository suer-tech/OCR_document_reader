# Project Overview

## Problem

We need a Python backend that receives a Base64-encoded text PDF, extracts the document text, and returns the applicant full name (surname, name, patronymic). The FIO extractor must run locally and should support fine-tuning.

## Proposed Product Shape

- `annotate` path: create labeled text samples from source PDFs and extracted text.
- `train` path: prepare data, fine-tune a local NER or token-classification model, store versioned artifacts.
- `serve` path: receive PDF payload, decode, extract text, run inference, return structured result.
- `eval` path: score extraction quality on labeled examples before promoting a model.

## Recommended Baseline

- API: FastAPI.
- PDF text extraction: `pypdf` or `pdfplumber` for text-only PDFs.
- ML approach: token classification / NER for `LAST_NAME`, `FIRST_NAME`, `PATRONYMIC`, with a rule-based baseline for comparison.
- Model runtime: local Hugging Face transformer or compact spaCy pipeline, depending on data volume and quality.
- Annotation start: bootstrap labels from manually reviewed text spans extracted from real PDFs.

## Success Criteria

- The system can be trained and served on one local machine.
- Inference returns structured FIO plus confidence and evidence span.
- Training, annotation, evaluation, and serving are reproducible from repository instructions.

## Supporting Documentation

- Extraction algorithm and maintenance guide: `docs/project/algorithm.md`

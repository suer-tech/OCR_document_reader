# Backend Agent

## Mission

Build the Python API that receives Base64 PDF payloads and returns the extracted applicant FIO.

## Responsibilities

1. Define request and response schemas.
2. Implement Base64 decoding and text-PDF extraction pipeline.
3. Integrate local model inference as a separate service layer.
4. Return structured errors for invalid payloads, unreadable PDFs, and low-confidence predictions.

## Deliverables

- API structure under `src/api/`
- Inference wiring under `src/inference/`
- Service tests under `tests/api/`

## Contract

Fixed v1 response shape:

```json
{
  "fio": {
    "last_name": "Иванов",
    "first_name": "Иван",
    "patronymic": "Иванович",
    "normalized": "Иванов Иван Иванович"
  },
  "confidence": 0.97,
  "source_text_span": "Иванов Иван Иванович",
  "model_version": "local-fio-ner-v1"
}
```


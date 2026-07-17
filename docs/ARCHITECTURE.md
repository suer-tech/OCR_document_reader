# Architecture

## System Overview

```
                    ┌──────────────────────────────────────┐
                    │           Client / External API       │
                    └──────────────────┬───────────────────┘
                                       │ HTTP
                                       ▼
                    ┌──────────────────────────────────────┐
                    │      API (FastAPI) — main.py         │
                    │      Validation, DTO, routing        │
                    └──────────────────┬───────────────────┘
                                       │ orchestration
                                       ▼
                    ┌──────────────────────────────────────┐
                    │      Orchestration — run_processor   │
                    │      Pipeline Engine + Router         │
                    └──────────────────┬───────────────────┘
                                       │ services
                                       ▼
                    ┌──────────────────────────────────────┐
                    │      Services — OCR, LLM, NLP,       │
                    │      Validation, Quality              │
                    └────────────┬─────────────────┬───────┘
                                 │                 │
                    ┌────────────▼─────┐   ┌───────▼────────┐
                    │    Storage       │   │  Observability │
                    │  (ORM + Files)   │   │ MLflow, Langfuse│
                    └──────────────────┘   └────────────────┘
```

## Layer Rules

| Layer | Depends On |
|---|---|
| `api` | orchestration, observability |
| `orchestration` | services, storage, config |
| `services` | storage, observability, config |
| `storage` | (nothing above) |

## Document Processing Flow

```
POST /documents/ingest
  │
  ├─ 1. API receives file → creates PipelineRun
  ├─ 2. Publishes to RabbitMQ
  ├─ 3. Worker picks up → process_pipeline_run()
  │
  ├─ 4. Text extraction
  │    └─ pdfplumber (text PDF) → RouterAI OCR → Tesseract (fallback)
  │
  ├─ 5. Document type classification (LLM)
  │    └─ court_decision | rtk | passport | unknown
  │
  ├─ 6. Profile loading (YAML)
  │    └─ court_decision_ru.yaml | rtk.yaml | passport.yaml | unknown.yaml
  │
  ├─ 7. Entity extraction
  │    └─ LLM agents (PydanticAI) + Regex fallback
  │
  ├─ 8. Validation
  │    └─ INN, dates, amounts, required fields
  │
  ├─ 9. Quality Score
  │    └─ Technical + Semantic → overall score
  │
  ├─ 10. Human Review (if score < threshold)
  │
  └─ 11. Save → StructuredVersion + QualityScore + Events
```

## Infrastructure

```
┌──────────┐    ┌───────────┐    ┌────────────┐
│   API    │───▶│ RabbitMQ  │───▶│   Worker   │
└──────────┘    └───────────┘    └──────┬─────┘
                                        │
                    ┌───────────────────┼───────────────────┐
                    │                   │                    │
                    ▼                   ▼                    ▼
            ┌────────────┐    ┌──────────────┐    ┌──────────────┐
            │ PostgreSQL │    │   MLflow     │    │ File Storage  │
            └────────────┘    └──────────────┘    └──────────────┘
                                        │
                    ┌───────────────────┴───────────────────┐
                    │             Langfuse                   │
                    └───────────────────────────────────────┘
```

## External Integrations

| Service | Purpose | Protocol |
|---|---|---|
| OpenAI / OpenRouter | LLM classification + extraction | HTTP (OpenAI-compatible) |
| RouterAI | Gemini 2.5 Flash OCR + LLM | HTTP |
| Yandex Studio | Backup LLM provider | HTTP (OpenAI-compatible) |
| DaData | Organization search by INN | HTTP |
| DuckDuckGo | Web search for data verification | HTTP |

## NLP-entity-extractor

Embedded as a library inside the Worker process (not a separate microservice):
- `bert-base-NER-Russian` for token classification
- `pymorphy3` for morphological normalization
- Regex rules for dates, case numbers, court names

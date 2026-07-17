# OCR Document Reader

Платформа для автоматического OCR-распознавания и NER-извлечения структурированных данных из судебных решений и заявлений о включении в реестр требований кредиторов (РТК).

## Architecture

Проект состоит из двух компонентов:

```
OCR-document-parser/     # Основная платформа-оркестратор (FastAPI + PostgreSQL)
NLP-entity-extractor/    # NER-микросервис на BERT (вызывается как библиотека)
```

## Quick Start

```bash
# 1. OCR-document-parser
cd OCR-document-parser
cp .env.example .env   # настроить ключи API
docker compose up -d --build

# 2. NLP-entity-extractor (опционально, standalone)
cd NLP-entity-extractor
pip install -e .
uvicorn src.api.app:app --port 8001
```

См. индивидуальные README каждого модуля для деталей.

## Documentation

| File | Description |
|---|---|
| `AGENT.md` | Входная точка для AI-агентов |
| `docs/ARCHITECTURE.md` | Архитектура и потоки данных |
| `docs/PROJECT_MAP.md` | Карта файлов проекта |
| `docs/DEVELOPMENT_RULES.md` | Правила разработки |
| `docs/MODULE_INDEX.md` | Индекс модулей |
| `docs/API.md` | API reference |
| `docs/ROADMAP.md` | Планы развития |
| `docs/adr/` | Architecture Decision Records |
| `docs/wiki/` | LLM Wiki — детальная документация |

## Tech Stack

- **API:** Python, FastAPI, Uvicorn, Pydantic
- **DB:** PostgreSQL (SQLAlchemy ORM)
- **Queue:** RabbitMQ (pika)
- **OCR:** RouterAI Gemini 2.5 Flash, Tesseract, pdfplumber
- **NLP:** HuggingFace Transformers (BERT), pymorphy3
- **Observability:** MLflow, Langfuse, Loki, Grafana, Prometheus
- **LLM Providers:** OpenAI, OpenRouter, RouterAI, Yandex Studio

# Architecture

```
Запрос (HTTP)
     │
     ▼
┌─────────────────────┐
│   API (FastAPI)     │  api/main.py, api/schemas.py
│   Валидация DTO     │
└─────────┬───────────┘
          │ вызов orchestration
          ▼
┌─────────────────────┐
│   Orchestration     │  orchestration/*.py
│   Пайплайн-движок   │  Читает YAML → выполняет шаги
│   Роутинг           │
└─────────┬───────────┘
          │ вызов services
          ▼
┌─────────────────────┐
│   Services          │  services/*.py
│   OCR, LLM, NLP,    │  Бизнес-логика извлечения,
│   Валидация,        │  валидации, расчёта качества
│   Quality Score     │
└─────────┬───────────┘
          │ вызов storage
          ▼
┌─────────────────────┐
│   Storage           │  storage/*.py
│   SQLAlchemy ORM    │  Только работа с БД
│   File I/O          │
└─────────────────────┘
```

## Правила зависимостей слоёв

- **api** → orchestration, observability
- **orchestration** → services, storage, config
- **services** → storage, observability, config
- **storage** → (не вызывает ничего выше)

Любая бизнес-логика должна находиться в **services** или **orchestration**.

**Storage** работает только с БД.

**API** не знает о SQL и БД напрямую.

## Поток обработки документа

1. **API** принимает документ → создаёт PipelineRun → публикует задачу в RabbitMQ
2. **Worker** забирает задачу → вызывает `process_pipeline_run()`
3. **Извлечение текста** (ocr_service): pdfplumber → pymupdf → RouterAI/Tesseract OCR
4. **Определение типа** (document_type_service): LLM классифицирует документ
5. **Загрузка профиля** (router): читает YAML профиль по document_type
6. **Извлечение сущностей** (extraction_agent): LLM/NLP агенты согласно профилю
7. **Валидация** (validation_service): проверка обязательных полей
8. **Quality Score** (quality_service): расчёт оценки качества
9. **Human Review** (опционально): если качество ниже порога
10. **Сохранение**: StructuredVersion, QualityScore, события в БД
11. **Webhook** (опционально): уведомление внешней системы

## Инфраструктура

```
┌──────────┐    ┌───────────┐    ┌────────────┐
│   API    │───▶│ RabbitMQ  │───▶│   Worker   │
└──────────┘    └───────────┘    └──────┬─────┘
                                        │
                              ┌─────────▼─────────┐
                              │    PostgreSQL      │
                              ├───────────────────┤
                              │    MLflow          │
                              ├───────────────────┤
                              │    Langfuse        │
                              ├───────────────────┤
                              │    File Storage    │
                              └───────────────────┘
```

## Внешние интеграции

| Сервис | Назначение | Библиотека |
|---|---|---|
| OpenAI / OpenRouter | LLM (классификация, экстракция) | httpx, AsyncOpenAI |
| RouterAI | OCR через Gemini 2.5 Vision, LLM | httpx, OpenAI |
| Yandex Studio | LLM (резервный провайдер) | AsyncOpenAI |
| DaData | Поиск организаций по ИНН/названию | requests |
| DuckDuckGo | Веб-поиск для верификации данных | requests |
| MLflow | Логирование метрик, моделей, артефактов | mlflow |
| Langfuse | Трассировка LLM вызовов, управление промптами | langfuse |

## NLP-entity-extractor

Вызывается как библиотека внутри Worker (не отдельный процесс). Использует:
- `bert-base-NER-Russian` для NER
- `pymorphy3` для нормализации ФИО
- Regex правила для дат, номеров дел, названий судов

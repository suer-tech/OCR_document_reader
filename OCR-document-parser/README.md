# OCR Document Parser

Платформа для OCR‑обработки судебных и других документов с декларативными пайплайнами, профилями задач и встроенной системой оценок качества.

- Стек: **Python + FastAPI + Postgres**.
- Архитектура и правила для агентов описаны в `AGENTS.md` и `ARCHITECTURE.md`.
- Продуктовые требования и дизайн — в каталоге `docs/`.
- **OCR для сканов**: PDF без текстового слоя и изображения обрабатываются через Tesseract (rus+eng); текст извлекается один раз на входе пайплайна.

## Быстрый старт (для людей)

1. Установите зависимости:

   ```bash
   pip install -e ".[dev]"
   ```

2. Запустите приложение (после реализации API):

   ```bash
   uvicorn ocr_platform.api.main:app --reload
   ```

Перед запуском можно создать `.env` на основе `.env.example` и задать:

- `OCR_OPENAI_API_KEY` и `OCR_OPENAI_DOC_TYPE_MODELS` (до 3 моделей для fallback-классификации типа документа),
- `OCR_MLFLOW_TRACKING_URI` для логирования экспериментов.

Примечание по классификации типа документа:

- если `document_type` не передан во входном запросе, система определяет тип только через LLM;
- при неуспешной классификации используется `unknown` (без эвристического определения).

3. Откройте документацию API:

   - Swagger UI: `http://localhost:8000/docs`
   - ReDoc: `http://localhost:8000/redoc`

## Observability (Loki + Grafana)

Для локального/стендового мониторинга логов и алертов используйте готовый стек в `observability/`.

Коротко:

1. Убедитесь, что включена запись JSON-логов в файл:
   - `OCR_LOG_LEVEL=INFO`
   - `OCR_LOG_FILE_PATH=logs/app.json.log`
2. Запустите `uvicorn`.
3. В отдельном терминале:

   ```bash
   cd observability
   docker compose up -d
   ```

Детальная инструкция: `observability/README.md`.

## Запуск всех служб (локально)

Ниже рекомендованный порядок запуска для разработки.

1. Подготовьте окружение в корне `OCR-document-parser`:

   ```bash
   pip install -e ".[dev]"
   ```

2. Создайте `.env` из `.env.example` (в той же директории) и задайте минимум:
   - `OCR_LOG_LEVEL=INFO`
   - `OCR_LOG_FILE_PATH=logs/app.json.log`
   - `OCR_OPENROUTER_API_KEY` или `OCR_OPENAI_API_KEY` (для LLM шагов)

3. Терминал №1 — API:

   ```bash
   uvicorn ocr_platform.api.main:app --reload
   ```

4. Терминал №2 — Pipeline worker (асинхронная обработка):

   ```bash
   ocr-pipeline-worker
   ```

5. Терминал №3 — Observability stack (Loki/Promtail/Grafana):

   ```bash
   cd observability
   docker compose up -d
   ```

6. (Опционально) Терминал №4 — MLflow UI, если нужен просмотр экспериментов:

   ```bash
   mlflow ui --host 127.0.0.1 --port 5000
   ```

### Проверка доступности

- API health: `http://127.0.0.1:8000/health`
- API docs: `http://127.0.0.1:8000/docs`
- Grafana: `http://127.0.0.1:3000` (`admin` / `admin`)
- Loki health: `http://127.0.0.1:3100/ready`
- MLflow UI (если запущен): `http://127.0.0.1:5000`

Для асинхронного ingest также требуется RabbitMQ (по умолчанию `amqp://guest:guest@localhost:5672/%2F`).

## Запуск через Docker Compose (все сервисы)

Из корня `OCR-document-parser`:

```bash
docker compose up -d --build
```

Поднимутся:

- `api` (FastAPI) — `http://127.0.0.1:8000`
- `worker` (асинхронная обработка пайплайнов)
- `rabbitmq` — AMQP `5672`, UI `http://127.0.0.1:15672` (`guest/guest`)
- `mlflow` — `http://127.0.0.1:5000`
- `loki`, `promtail`, `grafana` — Grafana `http://127.0.0.1:3000`

Остановка:

```bash
docker compose down
```

## API: ключевые эндпоинты

- `POST /documents/ingest` — асинхронный ingest:
  - сохраняет документ,
  - создает `pipeline_run` со статусом `queued`,
  - публикует задачу в RabbitMQ,
  - возвращает `202 Accepted` и `pipeline_run_id`.
- `GET /pipeline-runs/{pipeline_run_id}` — текущий статус run:
  - `queued | processing | retrying | done | failed`.
- `GET /documents/{document_id}/result` — агрегированный результат обработки.
- `POST /mlflow/backfill` — ручной backfill завершенных run в MLflow.

Пример backfill:

```bash
curl -X POST "http://127.0.0.1:8000/mlflow/backfill" \
  -H "Content-Type: application/json" \
  -d '{"limit": 200, "force": false}'
```

## MLflow: где смотреть prompt/response LLM

Для LLM вызовов создаются отдельные run (например `document_type_detection_llm`, `field_extraction_llm`).
Именно в них лежат артефакты:

- `prompts/system_prompt.txt`
- `prompts/user_prompt.txt`
- `prompts/response_schema.json`
- `responses/llm_responses.json`
- `attempts/attempts.json`

Сводный run `pipeline_request_summary` хранит метрики/теги пайплайна и обычно не содержит этих артефактов.

### Остановка служб

- Для API и MLflow: `Ctrl+C` в их терминалах.
- Для observability:

  ```bash
  cd observability
  docker compose down
  ```

## Архитектура

- Слои и правила зависимостей: см. `ARCHITECTURE.md`.
- Основная логика живёт в пакете `src/ocr_platform`.
- Пайплайны и профили описываются в YAML в `src/ocr_platform/config/pipelines`.
- Руководство по профилям и роутингу: `docs/user-guide/profiles-routing.md`.


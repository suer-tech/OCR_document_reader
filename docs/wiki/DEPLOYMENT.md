# 🚀 Деплой и Эксплуатация (Deployment Guide)

Данный документ содержит краткую выжимку по развертыванию OCR платформы на Production серверах (Ubuntu/Linux). Полная версия с деталями настройки Nginx и сертификатов находится в [deployment_guide.md](/deployment_guide.md).

## 1. Архитектура развертывания

В Production-среде система поднимается через **Docker Compose** и состоит из следующих контейнеров:
1. **API (FastAPI)** — точка входа для загрузки документов (`ocr-api`).
2. **Worker** — фоновый обработчик очередей и пайплайнов (`ocr-worker`).
3. **PostgreSQL** — основная база данных (`ocr-postgres`).
4. **RabbitMQ** — брокер сообщений для Ingest-очередей (`ocr-rabbitmq`).
5. **MLflow** — мониторинг моделей (`ocr-mlflow`).
6. **Observability Stack** (Loki + Promtail + Grafana) — агрегация логов и метрик.

> [!NOTE]
> Локальный микросервис `NLP-entity-extractor` **НЕ деплоится отдельно**. Он вызывается как библиотека внутри воркера (`ocr-worker`). 

## 2. Подготовка весов NLP модели

Перед запуском Docker Compose необходимо убедиться, что веса обученной NLP модели загружены на сервер по пути:
`/opt/ocr-platform/OCR-document-parser/models/exports/bootstrap-russian-ner/`

Там должны находиться файлы:
- `model.safetensors`
- `config.json`
- `metadata.json`
- `tokenizer.json` (или `vocab.txt`)

## 3. Настройка окружения

Файл `.env` должен содержать производственные ключи и настройки (без использования SQLite):

```ini
OCR_DATABASE_URL=postgresql+psycopg://ocr_user:STRONG_PASS@postgres:5432/ocr_db
OCR_RABBITMQ_URL=amqp://ocr_user:RABBIT_PASS@rabbitmq:5672/%2F
OCR_OPENROUTER_API_KEY=sk-or-v1-***
```

Для оптимального использования PyTorch на CPU, в `.env` задаются параметры:
```ini
OMP_NUM_THREADS=8
MKL_NUM_THREADS=8
TOKENIZERS_PARALLELISM=true
```

### 3.1. GLM-OCR (настройки таймаутов и retry)

```ini
# Движок OCR для сканированных страниц и картинок: tesseract или glm
OCR_ENGINE=glm
# Удаленный сервер Ollama для OCR
OCR_OLLAMA_OCR_URL=https://mainwgpu.devbpm.ru/ollama/api/chat
OCR_OLLAMA_OCR_MODEL=deepseek-ocr:latest
OCR_OLLAMA_OCR_TOKEN=***
# Таймаут (сек) на один запрос к GLM OCR
OCR_GLM_TIMEOUT_SECONDS=500.0
# Количество retries на одну страницу при ошибке/timeout GLM OCR
OCR_GLM_PAGE_RETRIES=3
```

> [!NOTE]
> **Heartbeat RabbitMQ отключён** (`heartbeat=0`) на уровне подключения pika, чтобы предотвратить разрыв соединения при долгой обработке документов (пайплайн может длиться несколько минут). При потере соединения воркер автоматически переподключается в цикле reconnection.

## 4. Запуск и Обслуживание

1. **Запуск**:
   ```bash
   docker compose up -d --build
   ```
2. **Логи**:
   ```bash
   docker compose logs -f api
   docker compose logs -f worker
   ```
3. **Бэкапы**:
   Регулярно делайте дампы БД Postgres и бэкапы директории `OCR_STORAGE_DIR` (куда сохраняются оригиналы PDF файлов).

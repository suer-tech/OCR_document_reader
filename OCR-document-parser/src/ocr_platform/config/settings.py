from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="OCR_", env_file=".env", extra="ignore")

    app_name: str = "OCR Document Parser"

    # БД: по умолчанию используем локальный SQLite для простого запуска.
    database_url: str = "sqlite:///./ocr.db"

    # Каталог для файлов документов.
    storage_dir: str = "data/documents"

    # MLflow (опционально).
    mlflow_tracking_uri: str | None = None
    mlflow_experiment_name: str = "ocr-pipeline"
    mlflow_experiment_artifact_location: str = "mlflow-artifacts:/ocr-pipeline"
    mlflow_async_logging: bool = True
    mlflow_async_queue_size: int = 1000
    mlflow_async_retries: int = 3
    mlflow_async_retry_backoff_seconds: float = 0.5
    mlflow_async_enqueue_timeout_seconds: float = 1.0

    # OpenAI-compatible provider settings.
    openai_api_key: str | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    openrouter_api_key: str | None = None
    openrouter_base_url: str = "https://openrouter.ai/api/v1"

    # Используется как общий fallback, если список fallback-моделей
    # не задан в YAML-конфиге конкретного шага.
    llm_default_fallback_models: str = "gpt-4.1-mini,gpt-4o-mini,gpt-4.1"

    # Legacy-настройка оставлена для обратной совместимости.
    openai_doc_type_models: str = "gpt-4.1-mini,gpt-4o-mini,gpt-4.1"
    openai_timeout_seconds: float = 180.0

    # RabbitMQ queue for async pipeline processing.
    rabbitmq_url: str = "amqp://guest:guest@localhost:5672/%2F"
    rabbitmq_ingest_queue: str = "ocr.ingest"
    worker_max_retries: int = 3

    # Движок OCR для сканированных страниц и картинок: tesseract или deepseek.
    ocr_engine: str = Field(default="tesseract", validation_alias="OCR_ENGINE")

    # Удаленный сервер Ollama для OCR
    ollama_ocr_url: str = Field(default="http://localhost:11434", validation_alias="OCR_OLLAMA_OCR_URL")
    ollama_ocr_model: str = Field(default="deepseek-ocr:latest", validation_alias="OCR_OLLAMA_OCR_MODEL")
    ollama_ocr_token: str | None = Field(default=None, validation_alias="OCR_OLLAMA_OCR_TOKEN")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


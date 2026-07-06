from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="OCR_", env_file=".env", extra="ignore"
    )

    app_name: str = "OCR Document Parser"

    # БД: по умолчанию используем локальный SQLite для простого запуска.
    database_url: str = "sqlite:///./ocr.db"

    # Каталог для файлов документов.
    storage_dir: str = "data/documents"

    # Observability (Langfuse & MLflow)
    langfuse_public_key: str | None = Field(
        default=None, validation_alias="LANGFUSE_PUBLIC_KEY"
    )
    langfuse_secret_key: str | None = Field(
        default=None, validation_alias="LANGFUSE_SECRET_KEY"
    )
    langfuse_host: str = Field(
        default="https://cloud.langfuse.com", validation_alias="LANGFUSE_HOST"
    )

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
    router_ai_api_key: str | None = None
    router_ai_base_url: str = "https://routerai.ru/api/v1"
    yandex_studio_api_key: str | None = None
    yandex_studio_base_url: str = "https://ai.api.cloud.yandex.net/v1"

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

    # DaData API for INN search
    dadata_api_key: str | None = None
    dadata_secret_key: str | None = None

    # Движок OCR для сканированных страниц и картинок: tesseract или router_ai.
    ocr_engine: str = Field(default="tesseract", validation_alias="OCR_ENGINE")

    # Настройки для OCR через RouterAI (Gemini)
    router_ai_ocr_model: str = Field(
        default="google/gemini-2.5-flash", validation_alias="OCR_ROUTER_AI_OCR_MODEL"
    )
    router_ai_ocr_timeout_seconds: float = Field(
        default=300.0, validation_alias="OCR_ROUTER_AI_OCR_TIMEOUT_SECONDS"
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

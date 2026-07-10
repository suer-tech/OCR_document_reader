# Orchestration

Папка `orchestration/` управляет жизненным циклом обработки документов.

**Не содержит** бизнес-логику извлечения (она в `services/`).

**Главная точка входа**: `run_processor.py` → `process_pipeline_run()`.

**Компоненты**:
- `pipeline_engine.py` — выполняет шаги из YAML профиля
- `router.py` — загружает YAML конфиги, определяет профиль документа
- `mlflow_backfill.py` — синхронизирует завершённые обработки с MLflow

# AGENT: При добавлении нового типа документа — добавь YAML профиль и обнови router.yaml. Не хардкодь бизнес-логику в orchestration.

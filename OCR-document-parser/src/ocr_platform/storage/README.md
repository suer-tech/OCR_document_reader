# Storage

Папка `storage/` отвечает только за хранение и доступ к данным.

**Не содержит** бизнес-логику.

**Компоненты**:
- `models.py` — SQLAlchemy ORM модели (10 моделей: Document, PipelineRun, TextVersion, StructuredVersion, QualityScore, HumanReviewTask и др.)
- `repository.py` — управление сессиями БД, инициализация, `generate_id()`
- `file_storage.py` — сохранение/загрузка файлов на диск

# AGENT: Все SQL-запросы — только через ORM модели из этого пакета. Не писать сырой SQL. Не вызывать сервисы или API из storage.

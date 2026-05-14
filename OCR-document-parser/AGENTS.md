## Кратко для агентов

Этот репозиторий — **платформа OCR‑пайплайнов** для судебных и других документов на **Python + FastAPI + Postgres**.  
Код пишут агенты. Люди задают архитектуру, профили задач и правила качества.

Твоя задача как агента — **строго следовать архитектурным слоям и конфигурации пайплайнов**, а не городить произвольные связи.

Если не уверен, начни с чтения:

- `ARCHITECTURE.md` — общая структура системы и слои.
- `QUALITY_SCORE.md` — как считаем качество обработки и когда зовём человека.
- `PLANS.md` / `docs/exec-plans/` — какие работы сейчас в фокусе.
- `docs/design-docs/ocr-platform.md` — детальный дизайн OCR‑платформы.
- `docs/product-specs/ocr-court-decisions.md` — требования к профилю “решение суда”.

---

## Архитектура и слои

Код организован в пакет `src/ocr_platform` со строгими слоями:

- `api` — FastAPI endpoints, только HTTP‑контракты и преобразование DTO.
- `orchestration` — выбор профиля и исполнение шагов пайплайна.
- `services` — бизнес‑логика: OCR, извлечение сущностей, валидация, оценка качества.
- `storage` — доступ к БД и файловому хранилищу.
- `observability` — логирование, метрики, MLflow.
- `config/pipelines` — декларативные YAML‑профили пайплайнов.

**Разрешённые зависимости (направление сверху вниз):**

- `api` → `orchestration`, `observability` (только для трассировки).
- `orchestration` → `services`, `storage` (для записи событий/состояния).
- `services` → `storage`, `observability`.
- `storage` → стандартная библиотека, драйверы БД/хранилищ.
- `observability` → стандартная библиотека, клиент MLflow, библиотеки метрик/логов.
- `config/pipelines` не импортирует код, только читается.

Нельзя:

- вызывать `storage` напрямую из `api`;
- вызывать `api`/`orchestration` из `services`, `storage`, `observability`;
- шить бизнес‑правила в код без отражения в YAML‑профилях и документах.

Подробнее см. `ARCHITECTURE.md`.

---

## Контракты API

Все входы/выходы описываются в `src/ocr_platform/api/schemas.py` через Pydantic‑модели.

Базовые эндпоинты:

- `POST /documents/ingest` — принять документ (PDF/изображение/текст) и метаданные, вернуть `document_id` и `pipeline_run_id`.
- `GET /documents/{id}/result` — получить текст, структурированные поля и оценки качества.
- `GET /human-review/tasks` / `POST /human-review/tasks/{id}/submit` — API для ручной верификации.

Важно для `POST /documents/ingest`:

- `document_type` — основной входной параметр типа документа;
- `document_type_hint` — legacy-поле для обратной совместимости;
- если переданы оба поля, приоритет у `document_type`.

При изменении контрактов:

- сначала обнови спецификацию в `docs/design-docs/ocr-platform.md`;
- затем схемы в `schemas.py`;
- затем тесты в `tests/api/test_*.py`.

---

## Пайплайны и профили

Бизнес‑процессы и типы документов описываются **декларативно** в `src/ocr_platform/config/pipelines/*.yaml`.

Структура конфигов:

- `src/ocr_platform/config/pipelines/system/router.yaml` — глобальный выбор профиля по типу документа.
- `src/ocr_platform/config/pipelines/profiles/*.yaml` — профили обработки (один профиль на один тип документа + `unknown`).

Правила определения типа документа:

- если в запросе передан `document_type`, использовать его;
- если `document_type` не передан, определять тип **только через LLM**;
- эвристики для определения типа документа не использовать;
- если LLM недоступен/не определил тип/уверенность ниже порога, использовать тип `unknown`.

Алгоритм выбора профиля (`router.resolve_profile`):

1. **Извлечь текст** через `ocr_service.extract_text_at_ingest` (PDF: текстовый слой → при пустоте OCR; Image: OCR; Text: чтение).
2. Взять `document_type` из запроса (или `document_type_hint` как legacy fallback).
3. Если тип не передан:
   - запустить LLM-классификацию типа документа по извлечённому тексту;
   - использовать `provider/model/fallback_models` из `system/router.yaml` (`detection.llm`);
   - ожидать строгий JSON-ответ по schema (`document_type`, `confidence`).
4. Если `confidence` ниже порога из `system/router.yaml` (`detection.min_confidence`) — принудительно выбрать `unknown`.
5. Сопоставить тип в профиль через `document_type_to_profile` из `system/router.yaml`.
6. Если сопоставление не найдено или `source_type` не входит в `applicable_sources` профиля — использовать `default_profile` (`unknown`).

Служебные значения `detection_source`:

- `request` — тип взят из входного запроса;
- `llm` — тип определён LLM;
- `low_confidence_fallback` — LLM дал тип, но confidence ниже порога;
- `source_mismatch_fallback` — профиль не подходит для данного `source_type`;
- `llm_unavailable` / `llm_unresolved` / `llm_no_text` — тип не определился и выбран `unknown`.

Пример профиля: `court_decision_ru.yaml`:

- `profile_id` — уникальный идентификатор профиля.
- `applicable_sources` — допустимые источники (`crm`, `email`, `portal`, `external`, ...).
- `pipeline` — список шагов (store_original → detect_type → extract_entities → validate → compute_quality → route_to_human_review). OCR и извлечение текста выполняются на входе, до пайплайна.
- `models` — какие OCR/LLM использовать (`provider`, `model`, `fallback_models`, `temperature`, `timeout_seconds`).
- `thresholds` — пороги качества и уверенности для решений.
- `fields` — какие поля извлекать и каковы требования к ним.

Правило: **добавление нового процесса/типа документа = новый профиль + тесты**, а не переписывание сервисов.

Разделение ответственности конфигов:

- YAML определяет провайдера и параметры модели для каждого шага;
- `.env` хранит только секреты и endpoint для провайдеров (`OCR_OPENAI_*`, `OCR_OPENROUTER_*`).

---

## Качество, метрики, MLflow

Каждый шаг пайплайна обязан:

- писать **структурные логи** (JSON) с `document_id`, `pipeline_run_id`, `profile_id`, `step_name`, `model_id`, `latency_ms`, `status`, `error_code`;
- обновлять **метрики** через функции из `src/ocr_platform/observability/metrics.py`;
- при использовании ML/LLM логировать в **MLflow** параметры, метрики и теги через `mlflow_client`.

Для шага LLM-классификации типа документа дополнительно логировать:

- список моделей fallback-цепочки;
- `winner_model` (какая модель дала финальный ответ);
- `final_confidence`;
- артефакт `document_type_attempts.json` с попытками по каждой модели.

Система оценок описана в `QUALITY_SCORE.md` и в YAML‑профилях:

- `technical_quality_score` — качество картинки/скана;
- `semantic_confidence_score` — уверенность в извлечённых полях;
- `overall_quality_score` — итоговая оценка для маршрутизации (авто/ручная проверка).

---

## Human Review

Для ручной верификации используется единый контракт сущностей:

- `field_id`, `field_name`;
- `value_system`, `value_human`;
- `source` (LLM, правило, справочник);
- `error_type`, `correction_reason`.

Исправления создают новую версию структурированных данных, которая сохраняется в БД.
Подробности — в `docs/design-docs/ocr-platform.md` и `docs/product-specs/ocr-court-decisions.md`.

---

## Как выполнять задачи

1. Прочитай актуальные планы в `PLANS.md` и `docs/exec-plans/`.
2. Для каждой задачи:
   - уточни профиль/тип документа и шаги пайплайна в YAML;
   - обнови код только в допустимых слоях;
   - добавь или обнови тесты.
3. Убедись, что:
   - тесты и линтеры проходят;
   - новые шаги логируют события, метрики и MLflow.

Если документации не хватает:

- сначала предложи обновление в `docs/` и/или `ARCHITECTURE.md`;
- потом обновляй код по новым правилам.


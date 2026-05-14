# Профили и маршрутизация документов

Этот документ объясняет, как система выбирает профиль обработки и какой YAML за это отвечает.

## Где лежат конфиги

- Глобальный роутинг:
  - `src/ocr_platform/config/pipelines/system/router.yaml`
- Профили конкретных типов документов:
  - `src/ocr_platform/config/pipelines/profiles/*.yaml`

## Принцип работы

1. **Извлечение текста на входе** — до определения типа система извлекает текст:
   - PDF с текстовым слоем — pdfplumber;
   - PDF без текста (скан) или изображение — OCR (Tesseract);
   - текстовый файл — чтение.
2. Если во входном запросе передан `document_type` (или `document_type_hint`), система использует его.
3. Если `document_type` не передан:
   - система определяет тип через LLM по извлечённому тексту;
   - при низкой уверенности выбирается `unknown`.
4. Тип документа мапится в профиль через `document_type_to_profile` в `router.yaml`.
5. Если сопоставление отсутствует, применяется `default_profile`.

### Детектор типа документа (LLM)

- Использует до 3 моделей по цепочке fallback из переменной:
  - `OCR_OPENAI_DOC_TYPE_MODELS`
- Ответ модели запрашивается в формате строгой JSON-схемы:
  - `document_type` (одно из разрешённых значений),
  - `confidence` (от 0.0 до 1.0).
- Если модель вернула ошибку/невалидный ответ, система переходит к следующей модели.
- Если все модели недоступны или вернули ошибки, система возвращает `unknown` (без эвристик).

Семантика `detection_source` при `unknown`:

- `llm_unavailable` — ключ/доступ к LLM не настроен;
- `llm_unresolved` — все модели в fallback-цепочке не дали валидный результат;
- `llm_no_text` — для классификации не было текста;
- `low_confidence_fallback` — LLM вернул тип, но confidence ниже порога из `router.yaml`.

Результаты детекции логируются в MLflow:

- какая модель дала финальный ответ (`winner_model`);
- все попытки по моделям и их статусы;
- итоговая уверенность (`final_confidence`).

## Добавление нового типа документа

1. Создайте YAML-профиль в `config/pipelines/profiles/`:
   - например: `invoice_ru.yaml`
   - задайте `profile_id`, `document_type`, `pipeline`, `fields_llm`/`fields_nlp` (для court_decision_ru), `thresholds`.
2. Добавьте маршрут в `system/router.yaml`:
   - `invoice: invoice_ru`
3. Добавьте обработчик в `ocr_platform/profiles/`:
   - например: `profiles/invoice_ru/extractor.py` с классом `InvoiceRuExtractor`;
   - зарегистрируйте в `profiles/__init__.py` в реестре.
4. Добавьте тесты роутинга и проверки результата.

## Профили: YAML и код

- **YAML** (`config/pipelines/profiles/*.yaml`) — конфигурация пайплайна, полей, порогов.
- **Код** (`ocr_platform/profiles/<profile_id>/`) — логика экстракции полей для конкретного профиля.

Сервис `document_intel_service` диспетчеризует по `profile_id` и (для court_decision_ru) по `extractor` из YAML — вызывает LLM- или NLP-обработчик.

## Профиль unknown

Профиль `unknown` обязателен как fallback:

- применяется при неопределённом типе документа;
- обеспечивает безопасный базовый пайплайн;
- обычно ведёт к ручной проверке.


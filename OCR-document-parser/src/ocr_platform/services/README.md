# Services

Папка `services/` содержит всю бизнес-логику платформы.

**Не содержит** HTTP эндпоинты и SQL запросы напрямую.

**Основные сервисы**:
- `ocr_service.py` — извлечение текста из PDF/изображений
- `llm_gateway.py` — LLM шлюз с fallback-цепочкой
- `extraction_agent.py` — PydanticAI агенты для извлечения полей
- `document_intel_service.py` — диспетчеризация экстракции
- `document_type_service.py` — классификация типа документа
- `agent_tools.py` — инструменты веб-поиска (DaData, DuckDuckGo)
- `validation_service.py` — валидация полей
- `quality_service.py` — расчёт Quality Score
- `court_decision_legacy_rules.py` — регулярные выражения для дат/номеров дел

# AGENT: Все новые сервисы добавлять в эту папку. Не вызывать API или Storage напрямую без необходимости.

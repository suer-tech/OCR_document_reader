# Observability Stack (Loki + Grafana)

Этот каталог поднимает локальный/стендовый стек наблюдаемости:

- `Loki` для хранения логов,
- `Promtail` для сбора JSON-логов приложения,
- `Grafana` для дашбордов и алертов.

## Что уже реализовано

1. Сбор логов из `logs/app.json.log` через Promtail.
2. Дашборд v1 с 4 панелями:
   - RPS (`ingest_started`)
   - Error rate (`pipeline_failed / ingest_started`)
   - p95 latency (`pipeline_completed.elapsed_seconds`)
   - LLM fail rate
3. Grafana alerting provisioning:
   - правила алертов (`rules.yml`)
   - contact points для Slack/Telegram (через webhook)
4. Retention в Loki: `14 days` (`336h`) в `loki/config.yml`.

## Быстрый запуск

1. Убедитесь, что приложение пишет JSON-логи в файл:

   - `OCR_LOG_FILE_PATH=logs/app.json.log`
   - `OCR_LOG_LEVEL=INFO`

2. Запустите приложение из корня репозитория:

   ```bash
   uvicorn ocr_platform.api.main:app --reload
   ```

3. В другом терминале поднимите observability-стек:

   ```bash
   cd observability
   docker compose up -d
   ```

4. Откройте Grafana:

   - URL: `http://localhost:3000`
   - login/password: `admin/admin`
   - Dashboard: `OCR Pipeline Overview`

## Проверка потока логов

Сгенерируйте пару запросов в `POST /documents/ingest`, затем в Grafana Explore проверьте:

```logql
{service="ocr-document-parser"}
```

Если данных нет:

- проверьте, что файл `logs/app.json.log` создается и пополняется;
- проверьте `docker compose logs promtail`;
- убедитесь, что repo смонтирован как `/workspace` и promtail читает `/workspace/logs/*.log`.

## Алерты в Slack/Telegram

Файл: `grafana/provisioning/alerting/contact-points.yml`

- `SLACK_WEBHOOK_URL` и `TELEGRAM_WEBHOOK_URL` оставлены как placeholders.
- Для production лучше хранить эти URL в секретах окружения Grafana.

## Retention и cost-control

- По умолчанию `retention_period: 336h` (14 дней).
- Для снижения стоимости:
  - не используйте высококардинальные labels (`document_id`, `pipeline_run_id`);
  - храните их в JSON payload, а не в labels;
  - ограничивайте verbose-события на high-RPS окружениях.

## Когда думать про ELK/OpenSearch

Рекомендуется миграция/dual-write только если нужен тяжелый полнотекстовый поиск или сложные forensic-запросы.

Базовая стратегия:

1. Loki оставить для operational monitoring.
2. Добавить parallel shipping в OpenSearch для аналитических запросов.
3. Перекладывать use-cases постепенно, не ломая текущие алерты.

## Нужно ли отдельный микросервис или Rabbit для логов?

- Отдельный микросервис логирования обычно не нужен.
- RabbitMQ для логов обычно избыточен и усложняет диагностику.
- RabbitMQ используйте для доменных событий, а логирование — через стандартный pipeline stdout/file -> collector -> Loki.

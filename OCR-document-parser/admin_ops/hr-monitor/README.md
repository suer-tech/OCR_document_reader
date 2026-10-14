# Уведомления сервера кандидатов в общий Telegram-бот

Отдельный host observer читает состояния Docker-проекта `hh-agent` и фиксированный read-only запрос к `agent_runs`, `agent_tasks`, `agent_attempts`. Приложение, PostgreSQL schema и application credentials не меняются. Прямого доступа OCR-бота к Docker, БД или shell сервера кандидатов нет.

## События

- Ошибка этапа: один логический инцидент на task + безопасный код, без повторов каждой retry-попытки той же ошибки.
- Конечная ошибка run: отдельное уведомление, не смешанное с recoverable retry.
- Недоступность обязательного сервиса, unhealthy, OOM, увеличение restart count, БД/история ошибок недоступна, усечение истории, диск <10%, память >95%, активный run без прогресса >1 часа. Превышение часа не меняет состояние кандидата и не является правилом оценки.
- Восстановление инфраструктуры и отдельная потеря свежего monitoring snapshot после двух неудачных опросов.

Наблюдатель обновляет snapshot каждые 15 секунд, бот опрашивает его раз в 20 секунд; при доступных источниках уведомление обычно приходит в пределах минуты. Snapshot старше 90 секунд отвергается. История ограничена 24 часами/1000 событий, усечение явно сигнализируется. На первой активации старые ошибки не рассылаются; текущие проблемы инфраструктуры показываются сразу. Уже обнаруженные события и delivery state каждого получателя сохраняются в существующем `/state` SQLite. Недоступный Telegram повторяется; ответ, потерянный после фактической доставки, может дать duplicate, поскольку у Telegram нет idempotency key.

Не передаются имена, entity IDs, документы, raw error text, logs, prompts, credentials. Только SHA-256 fingerprints, allowlisted stage/code, UTC timestamp и числовые метаданные. Unknown strings сворачиваются в `TECHNICAL_ERROR`/`unknown`. Эти операционные сообщения дополняют предметные уведомления с карточкой кандидата, а не подменяют REP-013.

## Установка наблюдателя на VPS кандидатов

1. Создать пользователя `hh-ops-monitor` с home и shell `/bin/sh`, без пароля и без docker/sudo groups. Каталог `/var/lib/hh-ops-monitor` — root:hh-ops-monitor, `0750`.
2. Установить root-owned пакет `/opt/hh-ops-monitor/admin_ops/{__init__,hr_snapshot,hr_protocol}.py`, mode `0644`; каталог не должен быть доступен на запись SSH-пользователю.
3. Установить service/timer из этого каталога в `/etc/systemd/system`, выполнить `systemctl daemon-reload` и `systemctl enable --now hh-ops-monitor.timer`. Проверить свежий `status.json` и `sources` без вывода исходных приватных данных.
4. Отдельный ed25519 ключ генерируется на OCR VPS в `/state/hr-monitor/id_ed25519`, mode `0600`, каталог `0700`, владелец UID 10001. Приватный ключ не копируется в чат, репозиторий, CLI arguments или логи.
5. В authorized_keys SSH-пользователя допускается только публичная часть этого ключа с `from="<OCR_VPS_IP>",restrict,command="/usr/bin/cat /var/lib/hh-ops-monitor/status.json"`. Home и `.ssh` root-owned, `.ssh` group-readable только этой учётной записи. Никакого произвольного shell/port/agent forwarding/PTY.
6. `known_hosts` на OCR VPS закрепляется по host public key, полученному через уже проверенное доверенное SSH-подключение. StrictHostKeyChecking остаётся включённым.
7. В OCR `.env`: `OPS_HR_MONITOR_ENABLED=true`, `OPS_HR_MONITOR_HOST=<IP>`, `OPS_HR_MONITOR_USER=hh-ops-monitor`, необязательный `OPS_HR_MONITOR_URL=https://<public-domain>/`. Ключ и known_hosts находятся в существующем state volume. Пересобрать/пересоздать только `admin-bot`.

## Проверки и откат

`python -m pytest admin_ops/tests -q`. Независимый `admin_ops/tests/run_hr_incident_postgres_acceptance.py` запускает отдельный synthetic PostgreSQL 16 без опубликованных портов, production mounts или provider calls; требует `OPS_HR_MONITOR_TEST_CONSENT=synthetic-only`. Не использовать production database для тестовых ошибок. Внешняя Telegram-доставка проверяется отдельным явно помеченным synthetic сообщением.

Отключение: `OPS_HR_MONITOR_ENABLED=false` и пересоздание только OCR admin-bot; `systemctl disable --now hh-ops-monitor.timer` на сервере кандидатов. Состояние, ключи и историю не удалять; candidate runtime не перезапускать. Известные ограничения общей приёмки приложения кандидатов (включая INT-005/TST-011) этим контуром не закрываются.

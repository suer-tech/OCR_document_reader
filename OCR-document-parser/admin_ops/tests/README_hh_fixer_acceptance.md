# Приёмка Telegram-команд для сервиса кандидатов

`test_hh_fixer_acceptance.py` написан независимым агентом, который не менял реализацию. Ожидаемое поведение задано задачей: явные команды `/fix_hh` и `/analyze_hh` выбирают кандидатный сервис, а действующие OCR-команды сохраняют прежний маршрут.

## Зафиксированный RED

До исправления реальный `AdminBot.handle` получил синтетическое личное сообщение администратора `/fix_hh Заполни организационные моменты`. Бот ответил «Неизвестная команда» и не вызвал fixer. Проверка ожидала `/fix` с `target=hh` и упала с `AssertionError`; ошибки загрузки, отсутствующей новой сигнатуры или `TypeError` не использовались как продуктовый RED. JUnit сохранён в `evidence/hh-fixer-red.junit.xml` и сгенерирован pytest.

## Проверяемые сценарии

- Личное сообщение администратора `/fix_hh` отправляет запрос с `target=hh` и сохраняет предложение с привязкой к настроенным `suer-tech/ai-candidate-screening` и `production`.
- Обычный `/fix` остаётся OCR-командой. Сообщения других пользователей и из групп игнорируются; обычный текст не вызывает `/fix` и не создаёт предложение.
- Ответ worker не может переопределить target, репозиторий или ветку. Допустимы безопасный отказ без сохранения либо привязка ответа к доверенной команде.
- `/approve` только создаёт PR. Неверный SHA не разрешает merge. Правильное подтверждение, повторное подтверждение, статус и ручной `/deploy` после перезапуска используют сохранённый target, проверенный SHA, `validate-hh` и `hh-production-deploy.yml`.
- Реальный release boundary отклоняет failed, cancelled и незавершённый CI до первого GitHub-запроса на запись.
- Worker HTTP boundary использует только настроенный репозиторий; произвольные repository/branch из JSON не выбирают clone. Внутренний token обязателен, произвольный target отклоняется.
- Анализ использует `read_only`, выключенные shell/web search, не собирает writable proposal и отклоняет изменение clone. Реальный `CodeReader` возвращает строки исходного кода с номерами, но не раскрывает candidate, fixtures, evidence, credentials и secrets.
- HH policy допускает `.ts`, `.tsx`, `.mjs`, `.js`, `.json` только в `web/server`, `web/app`, `web/tests`; служебные, секретные, кандидатные и выходящие за scope пути отклоняются.
- Временный настоящий Git checkout проверяет diff и пути HH от корня. Удаления, шесть файлов и файл более 500 000 байт запрещены. Небольшая правка существующего файла размером более 100 000 байт допустима, если diff проходит отдельный лимит 50 000 символов. OCR сохраняет прежний лимит 100 000 байт.

## Запуск

Из `OCR-document-parser/`:

```sh
python -m pytest admin_ops/tests/test_hh_fixer_acceptance.py -q
```

Приёмка и связанная OCR-регрессия:

```sh
python -m pytest admin_ops/tests/test_hh_fixer_acceptance.py admin_ops/tests/test_bot.py admin_ops/tests/test_analysis.py admin_ops/tests/test_safety.py admin_ops/tests/test_release.py admin_ops/tests/test_code_read.py -q --junitxml=admin_ops/tests/evidence/hh-fixer-green.junit.xml
```

Существующий GitHub workflow `ocr-ops-ci.yml` запускает весь `OCR-document-parser/admin_ops/tests`, поэтому новый файл автоматически входит в обязательный CI. Тесты используют только синтетические Telegram updates, локальный SQLite и временные Git repositories; Codex, worker transport и GitHub изолированы на внешних границах. Они не запускают production merge/deploy и не подтверждают работоспособность реального Codex/provider/VPS-контура.

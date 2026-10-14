# Приёмка bounded HH write MCP

Тест `test_hh_code_writer_acceptance.py` создан независимо от реализации writer/MCP/launcher. Он проверяет пользовательское ожидание: явный HH fix должен давать проверяемый исходный diff даже когда Docker запрещает user namespaces и native shell недоступен.

## Исходный продуктовый RED

До изменения launcher реальный HTTP boundary `/fix` с `target=hh` получил синтетическую задачу. Namespace-denied runner вернул `bwrap: No permissions to create a new namespace`; endpoint ответил `status=no_changes` без `files`. Приёмка ожидала настоящий bounded diff синтетического исходного файла и упала с `AssertionError`. Отсутствие нового Python module, MCP dependency и TypeError не использовались как продуктовый RED.

JUnit RED и GREEN находятся в `evidence/hh-code-writer-<UTC timestamp>-{red,green}.junit.xml`. Оба отчёта сгенерированы pytest; содержимое вручную не исправлялось.

## Что проверяется

- Настоящий локальный Git clone из синтетического seed repository; seed и чужие файлы не изменяются.
- HTTP HH fix выбирает фиксированный target/repository, запускает `read_only` SDK sandbox с выключенным shell и trusted writable MCP, получает actual `collect_proposal` diff после реальной правки `CodeWriter`.
- Exact unique replacement сохраняет остальные строки. Missing, ambiguous и пустой anchor отклоняются до изменения файла.
- Создание и повторное изменение файлов, предел пяти разных изменённых путей, запрет шестого файла; rejected JSON не создаёт файл и не расходует слот.
- Запрет traversal, dot-путей, secrets, runtime credentials, candidate materials, fixtures/evidence, scripts/workflows/deploy/package changes и неподходящих типов файлов.
- Лимит 500 000 UTF-8 bytes и запрет NUL binary content проверяются до перезаписи исходного файла.
- File и parent-directory symlinks не приводят к изменению внешнего синтетического файла; non-Git root отвергается.
- Настоящий FastMCP `list_tools`/`call_tool`: HH writable context содержит только три read tools и две bounded write tools. Read-only/OCR contexts не дают писать; попытка writable OCR отвергается. Созданный файл доступен последующему read/list.
- Write tool schemas не содержат command/root/target/shell arguments. Неизвестный command tool недоступен.
- Старый HH fixer acceptance сохраняет содержательные clone/target/diff assertions и использует bounded writer вместо прямой записи из fake model runner.

## Воспроизведение

При установленном `mcp==1.30.0` из admin image:

```sh
python -m pytest admin_ops/tests/test_hh_code_writer_acceptance.py admin_ops/tests/test_hh_fixer_acceptance.py -q --junitxml=admin_ops/tests/evidence/hh-code-writer-green.junit.xml
```

Этот контур использует реальные filesystem/Git/MCP boundaries, но изолирует model inference. Linux SDK smoke с реально запрещёнными namespaces, без login/inference, запускается отдельно через `sdk_hh_write_smoke.py` в admin Docker image. Python pytest на Windows не заявляет результат этого Linux smoke или production-like candidate E2E. Обязательный production-like E2E gate остаётся BLOCKED по известной неподготовленной среде/конфликту main specs.

import re

# Заглушки регулярных выражений для базового поиска
# В реальном проекте тут нужно использовать NER-модель или более сложные паттерны.

CREDITOR_BLOCK_RE = re.compile(
    r"^\s*(?:Кредитор|Заявитель)(?:\s*\([^)]*\))?\s*[:]?\s*\n?\s*"
    r"(.*?)"
    r"(?=\n\s*(?:ИНН|ОГРН|Адрес|Юридический|Почтовый|Представитель|Финансовый|Должник|Дата|Телефон|Тел|Email|\d{6}|\()|\s+ИНН|\n\s*\n|$)",
    re.IGNORECASE | re.DOTALL | re.MULTILINE,
)
CLAIMS_AMOUNT_RE = re.compile(
    r"сумм[аеу]\s+требований\s*[:\-]?\s*(\d+[\d\s\.,]*)\s*(?:руб|р\.)", re.IGNORECASE
)
GROUNDS_RE = re.compile(r"основани[ея]\s*[:\-]?\s*(договор[^\n\.]+)", re.IGNORECASE)
CASE_NUMBER_RE = re.compile(r"Дел[оу]\s*[№N]?\s*([АA]\d{1,3}-\d+/\d{4})", re.IGNORECASE)


def extract_creditor(text: str) -> str | None:
    val = extract_creditor_with_ollama_llm(text)
    if val:
        return val

    # Резервный поиск по регулярному выражению, если Ollama недоступна/ошибка 502
    match = CREDITOR_BLOCK_RE.search(text)
    if match:
        extracted = match.group(1).strip()
        # Очистим от лишних переносов строк
        extracted = re.sub(r"\s+", " ", extracted)
        return extracted
    return None


def extract_case_number(text: str) -> str | None:
    match = CASE_NUMBER_RE.search(text)
    return match.group(1) if match else None


def extract_claims_amount_with_ollama_llm(text: str) -> dict | None:
    """
    Интеллектуальное извлечение суммы требований РТК и сведений о госпошлине
    с использованием пошагового рассуждения (SGR) на модели gpt-oss:20b через Ollama API.
    """
    from ocr_platform.config.settings import get_settings
    from ocr_platform.observability.logging import get_logger
    import requests
    import json

    local_logger = get_logger(__name__)
    settings = get_settings()

    # Берем последние 6000 символов, так как просительная часть обычно находится в конце документа.
    search_text = text[-6000:].strip() if len(text) > 6000 else text.strip()
    if not search_text:
        return None

    base_url = settings.ollama_ocr_url.rstrip("/")
    url = base_url if base_url.endswith("/api/chat") else f"{base_url}/api/chat"

    prompt = (
        "Ты — профессиональный юридический аналитик и эксперт по банкротству.\n"
        "Твоя задача — проанализировать предоставленный текст заявления о включении в реестр требований кредиторов (РТК) "
        "и извлечь структурированную информацию о сумме требований кредитора.\n\n"
        "ИНСТРУКЦИЯ ПОИСКА:\n"
        "1. Найди в тексте заявления финальный блок требований. Обычно он начинается со слов \"ПРОШУ:\", \"ПРОСИМ:\", \"ПРОСИТ СУД:\" или аналогичных.\n"
        "2. Анализируй требования строго внутри этого блока! Вся информация выше этого блока (в описательной части заявления) может содержать промежуточные расчеты и другие судебные дела — игнорируй их, если они не продублированы в просительной части.\n\n"
        "МЕТОДОЛОГИЯ АНАЛИЗА (SGR / Step-by-Step Reasoning):\n"
        "Сначала выполни пошаговые рассуждения в поле \"reasoning\":\n"
        "- Подсчитай количество кредитных договоров или обязательств, задолженность по которым просят включить в реестр в блоке ПРОШУ.\n"
        "- Выпиши все суммы, которые просят включить (основной долг, проценты, неустойки). Сложи их, чтобы получить общую сумму задолженности по всем обязательствам.\n"
        "- Найди упоминание госпошлины. Выясни, входит ли госпошлина в эту общую сумму требований в блоке ПРОШУ или она указана отдельно/взыскивается отдельно.\n\n"
        "ФОРМАТ ОТВЕТА:\n"
        "Верни ответ СТРОГО в формате JSON с помощью следующей схемы:\n"
        "{\n"
        "  \"reasoning\": \"подробное пошаговое рассуждение на русском языке\",\n"
        "  \"commitments_count\": <целое число обязательств/кредитных договоров>,\n"
        "  \"total_debt_amount\": <общая сумма задолженности по всем обязательствам в рублях (float)>,\n"
        "  \"debt_components\": [\"список строковых описаний компонентов задолженности, например: основной долг, проценты\"],\n"
        "  \"is_duty_included_in_total\": <true, если госпошлина входит в общую сумму задолженности; false, если пошлина указана отдельно/не входит>,\n"
        "  \"duty_amount\": <сумма госпошлины в рублях, если она упомянута в документе (float, иначе 0.0)>\n"
        "}\n\n"
        "Не добавляй никаких вводных слов перед JSON или после него. Используй только валидный JSON.\n\n"
        f"Текст заявления:\n{search_text}"
    )

    payload = {
        "model": "gpt-oss:20b",
        "messages": [
            {
                "role": "user",
                "content": prompt
            }
        ],
        "stream": False,
        "options": {
            "temperature": 0.0
        }
    }

    headers = {}
    if settings.ollama_ocr_token:
        headers["Authorization"] = f"Bearer {settings.ollama_ocr_token}"

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=120.0)
        if response.status_code == 200:
            res_data = response.json()
            content = res_data.get("message", {}).get("content", "").strip()
            # Извлекаем JSON из markdown-блоков
            json_match = re.search(r"```json\s*(.*?)\s*```", content, re.DOTALL)
            json_str = json_match.group(1) if json_match else content
            data = json.loads(json_str)
            local_logger.info(
                "ollama_llm_claims_amount_success",
                commitments_count=data.get("commitments_count"),
                total_debt_amount=data.get("total_debt_amount"),
                is_duty_included_in_total=data.get("is_duty_included_in_total"),
            )
            return data
        else:
            local_logger.warning(
                "ollama_llm_claims_amount_http_error",
                status_code=response.status_code,
                response_text=response.text,
            )
    except Exception as exc:
        local_logger.exception("ollama_llm_claims_amount_failed", error=str(exc))

    return None


def extract_claims_amount(text: str) -> str | None:
    # 1. Попробуем получить значение через Ollama (с логикой госпошлины)
    try:
        llm_data = extract_claims_amount_with_ollama_llm(text)
        if llm_data:
            total_debt = float(llm_data.get("total_debt_amount") or 0.0)
            is_duty_included = bool(llm_data.get("is_duty_included_in_total"))
            duty = float(llm_data.get("duty_amount") or 0.0)

            if is_duty_included and duty > 0:
                final_amount = total_debt - duty
            else:
                final_amount = total_debt

            if final_amount > 0:
                return f"{final_amount:.2f}".replace(".", ",")
    except Exception as exc:
        from ocr_platform.observability.logging import get_logger
        get_logger(__name__).warning("extract_claims_amount_llm_error_falling_back", error=str(exc))

    # 2. Резервный вариант (Fallback): поиск по регулярным выражениям
    block_match = re.search(
        r"(?i)\b(ПРОШУ(?:\s+СУД)?|ПРОСИТ\s+СУД|ПРОСИМ)\b[:\s]*(.*)",
        text,
        flags=re.DOTALL,
    )
    if not block_match:
        return None

    block = block_match.group(2)

    def parse_float(s: str) -> float:
        s = re.sub(r"[ \xA0]", "", s)
        s = s.replace(",", ".")
        try:
            return float(s)
        except ValueError:
            return 0.0

    numbers = {}

    # 1. С указанием валюты
    for m in re.finditer(
        r"((?:(?:\d{1,3}(?:[ \xA0]\d{3})+)|(?:\d+))(?:[,.]\d{1,2})?)\s*(?:\([^)]+\)\s*)?(?:руб|р\.|коп|₽|рублей|рубля)",
        block,
        flags=re.IGNORECASE,
    ):
        numbers[m.start(1)] = (parse_float(m.group(1)), m.start(1), m.end(1))

    # 2. После слов размере/сумме
    for m in re.finditer(
        r"(?:размере|сумме|размер|сумма)\s*[:]?\s*((?:(?:\d{1,3}(?:[ \xA0]\d{3})+)|(?:\d+))(?:[,.]\d{1,2})?)",
        block,
        flags=re.IGNORECASE,
    ):
        numbers[m.start(1)] = (parse_float(m.group(1)), m.start(1), m.end(1))

    # 3. Числа с копейками (две цифры после точки/запятой)
    for m in re.finditer(
        r"((?:(?:\d{1,3}(?:[ \xA0]\d{3})+)|(?:\d+))[,.]\d{2})(?!\d)", block
    ):
        numbers[m.start(1)] = (parse_float(m.group(1)), m.start(1), m.end(1))

    if not numbers:
        # Fallback на любые числа, если вообще ничего не нашли
        num_pattern = r"((?:(?:\d{1,3}(?:[ \xA0]\d{3})+)|(?:\d+))(?:[,.]\d{1,2})?)"
        for m in re.finditer(num_pattern, block):
            val = parse_float(m.group(1))
            # Игнорируем длинные номера без точек/запятых, которые могут быть договорами
            if val > 10000000 and "," not in m.group(1) and "." not in m.group(1):
                continue
            numbers[m.start(1)] = (val, m.start(1), m.end(1))

    if not numbers:
        return None

    nums_list = list(numbers.values())
    total_amount = max(n[0] for n in nums_list)

    # Ищем сумму госпошлины ВО ВСЕМ ТЕКСТЕ, а не только в блоке ПРОШУ
    global_money_pattern = r"((?:(?:\d{1,3}(?:[ \xA0]\d{3})+)|(?:\d+))(?:[,.]\d{1,2})?)\s*(?:\([^)]+\)\s*)?(?:руб|р\.|коп|₽|рублей|рубля)"
    global_numbers = []
    for m in re.finditer(global_money_pattern, text, flags=re.IGNORECASE):
        global_numbers.append(parse_float(m.group(1)))

    duty_amount = 0.0

    # Ищем сумму госпошлины — предпочитаем прямой порядок (слово → сумма)
    # Вариант А: сумма идет ПОСЛЕ слова пошлина (основной)
    m_forward = re.search(
        r"(?i)(?:госпошлин|государственн\w*\s+пошлин\w*)[^\d]{0,100}?((?:(?:\d{1,3}(?:[ \xA0]\d{3})+)|(?:\d+))(?:[,.]\d{1,2})?)",
        text,
    )
    if m_forward:
        candidate = parse_float(m_forward.group(1))
        if candidate > 0 and candidate in global_numbers and candidate < total_amount:
            duty_amount = candidate

    # Вариант Б: сумма идет ДО слова пошлина (только если вариант А не сработал)
    if duty_amount == 0.0:
        m_backward = re.search(
            r"(?i)((?:(?:\d{1,3}(?:[ \xA0]\d{3})+)|(?:\d+))(?:[,.]\d{1,2})?)[^\d]{0,30}?(?:госпошлин|государственн\w*\s+пошлин\w*)",
            text,
        )
        if m_backward:
            candidate = parse_float(m_backward.group(1))
            if (
                candidate > 0
                and candidate in global_numbers
                and candidate < total_amount
            ):
                duty_amount = candidate

    if duty_amount >= total_amount:
        duty_amount = 0.0

    final_amount = total_amount - duty_amount
    return f"{final_amount:.2f}".replace(".", ",")


# Список регулярных выражений для 16 целевых оснований, отсортированных по убыванию специфичности.
# Это позволяет точно сопоставлять словоформы (склонения) и приводить их к каноническому виду.
GROUNDS_PATTERNS = [
    # 1. Договор на предоставление коммунальных услуг
    (
        re.compile(
            r"\bдоговор[а-я]{0,3}(?:\s+(?:на|о))?\s+предоставлен[а-я]{2,4}\s+коммунальн[а-я]{2,4}\s+услуг[а-я]{0,3}\b",
            re.IGNORECASE,
        ),
        "договор на предоставление коммунальных услуг",
    ),
    # 2. договор предоставления кредита
    (
        re.compile(
            r"\bдоговор[а-я]{0,3}(?:\s+(?:о|на))?\s+предоставлен[а-я]{2,4}\s+кредит[а-я]{0,3}\b",
            re.IGNORECASE,
        ),
        "кредитный договор",
    ),
    # 3. договор потребительского микрозайма
    (
        re.compile(
            r"\bдоговор[а-я]{0,3}\s+потребительск[а-я]{2,4}\s+микро(?:займ|заем|заём)[а-я]{0,3}\b",
            re.IGNORECASE,
        ),
        "договор потребительского микрозайма",
    ),
    # 4. договор потребительского займа
    (
        re.compile(
            r"\bдоговор[а-я]{0,3}\s+потребительск[а-я]{2,4}\s+(?:займ|заем|заём)[а-я]{0,3}\b",
            re.IGNORECASE,
        ),
        "договор потребительского займа",
    ),
    # 5. договор кредитной карты
    (
        re.compile(
            r"\bдоговор[а-я]{0,3}\s+кредитн[а-я]{2,4}\s+карт[а-я]{0,2}\b", re.IGNORECASE
        ),
        "кредитный договор",
    ),
    # 6. договор банковского счета
    (
        re.compile(
            r"\bдоговор[а-я]{0,3}\s+банковск[а-я]{2,4}\s+счет[а-я]{0,3}\b",
            re.IGNORECASE,
        ),
        "договор банковского счета",
    ),
    # 7. договор энергоснабжения
    (
        re.compile(r"\bдоговор[а-я]{0,3}\s+энергоснабжен[а-я]{2,4}\b", re.IGNORECASE),
        "договор энергоснабжения",
    ),
    # 8. кредитный договор (должен быть ДО «договор займа», иначе документы о кредите
    #    могут ошибочно попасть под паттерн займа)
    (
        re.compile(r"\bкредитн[а-я]{2,4}\s+договор[а-я]{0,3}\b", re.IGNORECASE),
        "кредитный договор",
    ),
    # 9. договор займа
    (
        re.compile(
            r"\bдоговор[а-я]{0,3}\s+(?:займ|заем|заём)[а-я]{0,3}\b", re.IGNORECASE
        ),
        "договор займа",
    ),
    # 10. задолженность по уплате налога
    (
        re.compile(
            r"\bобязанност[а-я]{0,3}\s+по\s+уплат[а-я]{1,3}\s+налог[а-я]{0,3}\b",
            re.IGNORECASE,
        ),
        "задолженность по уплате налога",
    ),
    (
        re.compile(
            r"\bзадолженност[а-я]{0,2}\s+по\s+уплат[а-я]{1,3}\s+налог[а-я]{0,3}\b",
            re.IGNORECASE,
        ),
        "задолженность по уплате налога",
    ),
    # 11. Налоговая задолженность
    (
        re.compile(r"\bналогов[а-я]{2,4}\s+задолженност[а-я]{0,2}\b", re.IGNORECASE),
        "налоговая задолженность",
    ),
    # 12. исполнительный лист
    (
        re.compile(r"\bисполнительн[а-я]{2,4}\s+лист[а-я]{0,3}\b", re.IGNORECASE),
        "исполнительный лист",
    ),
    # 13. исполнительный документ
    (
        re.compile(r"\bисполнительн[а-я]{2,4}\s+документ[а-я]{0,3}\b", re.IGNORECASE),
        "исполнительный документ",
    ),
    # 14. судебный приказ
    (
        re.compile(r"\bсудебн[а-я]{2,4}\s+приказ[а-я]{0,3}\b", re.IGNORECASE),
        "судебный приказ",
    ),
    # 15. судебный акт
    (
        re.compile(r"\bсудебн[а-я]{2,4}\s+акт[а-я]{0,3}\b", re.IGNORECASE),
        "судебный акт",
    ),
    # 16. административное правонарушение
    (
        re.compile(
            r"\bадминистративн[а-я]{2,4}\s+правонарушен[а-я]{2,4}\b", re.IGNORECASE
        ),
        "административное правонарушение",
    ),
]


def extract_grounds(text: str) -> str | None:
    if not text:
        return None

    # 1. Ищем совпадения с 16 целевыми каноническими основаниями во всем тексте
    for pattern, canonical_name in GROUNDS_PATTERNS:
        if pattern.search(text):
            return canonical_name

    # 2. Если совпадений не найдено, используем базовый поиск (для обратной совместимости)
    match = GROUNDS_RE.search(text)
    return match.group(1).strip().lower() if match else None


def extract_creditor_with_ollama_llm(text: str) -> str | None:
    """
    Интеллектуальное извлечение кредитора из всего текста документа (до 30000 символов)
    с помощью модели gpt-oss:20b на удаленном сервере Ollama с Bearer-авторизацией.
    Использует пошаговое рассуждение (SGR) для сопоставления упоминаний и исправления ошибок OCR.
    """
    from ocr_platform.config.settings import get_settings
    from ocr_platform.observability.logging import get_logger
    import requests
    import json

    local_logger = get_logger(__name__)
    settings = get_settings()

    search_text = text[:30000].strip()
    if not search_text:
        return None

    base_url = settings.ollama_ocr_url.rstrip("/")
    if base_url.endswith("/api/chat"):
        url = base_url
    else:
        url = f"{base_url}/api/chat"

    prompt = (
        "Ты — профессиональный юридический аналитик и эксперт по банкротству.\n"
        "Твоя задача — извлечь точное наименование Кредитора (Заявителя) из предоставленного текста заявления о включении требований в реестр (РТК).\n\n"
        "ИНСТРУКЦИЯ АНАЛИЗА:\n"
        "1. Шаг 1: Найди наименование кредитора в шапке документа (в самом начале, обычно в первых строках после слов \"Кредитор:\", \"Заявитель:\", \"от...\").\n"
        "2. Шаг 2: Найди все упоминания этого кредитора (и возможные вариации/опечатки его названия) в остальной части документа.\n"
        "3. Шаг 3: Сравни все найденные варианты. Если в шапке допущена OCR-ошибка (например, \"ПАО Сбербан\" или \"ПКО АйДи Коллек\"), а в теле документа несколько раз упоминается правильное полное название (\"ПАО Сбербанк\" или \"ООО ПКО «АйДи Коллект»\"), выбери наиболее частотное и корректное (полное, без опечаток) наименование.\n"
        "4. Шаг 4: Приведи название к правильному ОПФ и формату. Исправь опечатки OCR (000 -> ООО, cократи до общепринятых ОПФ вроде ПАО, ООО, АО, НАО, ПКО, если в тексте они записаны криво).\n\n"
        "ФОРМАТ ОТВЕТА:\n"
        "Верни ответ СТРОГО в формате JSON с помощью следующей схемы:\n"
        "{\n"
        "  \"reasoning\": \"Подробные пошаговые рассуждения на русском языке: 1) какой кредитор найден в шапке, 2) какие упоминания найдены в теле документа, 3) сравнение и выбор наиболее частого и корректного варианта.\",\n"
        "  \"creditor_name\": \"Наиболее частое и корректное наименование кредитора (например, ПАО Сбербанк, ООО ПКО «АйДи Коллект»)\"\n"
        "}\n\n"
        "Не добавляй никаких вводных слов перед JSON или после него. Используй только валидный JSON.\n\n"
        f"Текст документа:\n{search_text}"
    )

    payload = {
        "model": "gpt-oss:20b",
        "messages": [
            {
                "role": "user",
                "content": prompt
            }
        ],
        "stream": False,
        "options": {
            "temperature": 0.0
        }
    }

    headers = {}
    if settings.ollama_ocr_token:
        headers["Authorization"] = f"Bearer {settings.ollama_ocr_token}"

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=120.0)
        if response.status_code == 200:
            res_data = response.json()
            content = res_data.get("message", {}).get("content", "").strip()
            # Извлекаем JSON из markdown-блоков
            json_match = re.search(r"```json\s*(.*?)\s*```", content, re.DOTALL)
            json_str = json_match.group(1) if json_match else content
            data = json.loads(json_str)
            extracted = data.get("creditor_name", "").strip()
            if extracted.lower() in ("none", "нет", "не указан", "unknown"):
                return None
            local_logger.info(
                "ollama_llm_creditor_success",
                creditor=extracted,
                reasoning=data.get("reasoning"),
            )
            return extracted
        else:
            local_logger.warning(
                "ollama_llm_creditor_http_error",
                status_code=response.status_code,
                response_text=response.text,
            )
    except Exception as exc:
        local_logger.exception("ollama_llm_creditor_failed", error=str(exc))

    return None

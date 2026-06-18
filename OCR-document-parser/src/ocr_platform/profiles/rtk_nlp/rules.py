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


def extract_claims_amount(text: str) -> str | None:
    # Ищем блок после "ПРОШУ", "ПРОСИТ СУД", "ПРОСИМ"
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
    # 8. договор займа
    (
        re.compile(
            r"\bдоговор[а-я]{0,3}\s+(?:займ|заем|заём)[а-я]{0,3}\b", re.IGNORECASE
        ),
        "договор займа",
    ),
    # 9. кредитный договор
    (
        re.compile(r"\bкредитн[а-я]{2,4}\s+договор[а-я]{0,3}\b", re.IGNORECASE),
        "кредитный договор",
    ),
    # 10. задолженность по уплате налога
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
    Интеллектуальное извлечение кредитора из шапки документа (первые 3000 символов)
    с помощью модели gpt-oss:20b на удаленном сервере Ollama с Bearer-авторизацией.
    """
    from ocr_platform.config.settings import get_settings
    from ocr_platform.observability.logging import get_logger
    import requests

    local_logger = get_logger(__name__)
    settings = get_settings()

    header_text = text[:2000].strip()
    if not header_text:
        return None

    base_url = settings.ollama_ocr_url.rstrip("/")
    if base_url.endswith("/api/chat"):
        url = base_url
    else:
        url = f"{base_url}/api/chat"

    payload = {
        "model": "gpt-oss:20b",
        "messages": [
            {
                "role": "user",
                "content": (
                    "Ты — экстрактор названий кредиторов из юридических документов о банкротстве.\n\n"
                    "Из текста выдели полное наименование лица, которое выступает Кредитором или Заявителем.\n"
                    "Имя кредитора всегда указано в самом начале документа (первые строки, шапка). "
                    "Не обращай внимания на другие организации, упомянутые ниже в тексте.\n\n"
                    "Примеры правильных ответов:\n"
                    "  «ПАО АКБ [(АВАНГАРД)] ИНН 7702070139» → ПАО АКБ «АВАНГАРД»\n"
                    '  «ООО ПКО "АйДи Коллект"» → ООО ПКО «АйДи Коллект»\n'
                    "  «Кредитор: ПАО Сбербанк» → ПАО Сбербанк\n"
                    '  «Общество с ограниченной ответственностью "Феникс"» → ООО «Феникс»\n'
                    "  «Наименование: ПАО ВТБ» → ПАО ВТБ\n"
                    "  «Наименование: ООО «Энергосбытовая компания Башкортостана»» → ООО «Энергосбытовая компания Башкортостана»\n\n"
                    "Примеры НЕПРАВИЛЬНЫХ ответов (так не делай!):\n"
                    "  ✗ «освобожден от уплаты государственной пошлины» — это фраза из документа, а не название\n"
                    "  ✗ «в соответствии со статьей 213.8» — не название\n"
                    "  ✗ «Агентство: Общество с ограниченной ответственностью» — неполное, без указания названия организации\n"
                    "  ✗ «Bank ВТБ» — используй русский язык: «ПАО ВТБ» или «Банк ВТБ (ПАО)»\n"
                    "  ✗ «— 000 «СФО Титан»» — исправь OCR-ошибку: должно быть «ООО «СФО Титан»»\n"
                    "  ✗ «Акционерное общество кТБанк>» — исправь мусор: должно быть «АО «ТБанк»»\n\n"
                    "Правила:\n"
                    "- Если после ОПФ (ПАО, ООО, АО, НАО, ПКО) идёт название в скобках [(бренд)] или (бренд) — "
                    "это торговое наименование, включи его в ответ в кавычках-ёлочках.\n"
                    "- Если после названия идут реквизиты (ИНН, ОГРН, адреса, телефон, email) — отбрось их.\n"
                    "- Если видишь OCR-ошибки (000 вместо ООО, латиница вместо кириллицы, лишние символы <>#*) — "
                    "попробуй восстановить правильное написание.\n"
                    "- Верни ТОЛЬКО одно название. Никаких списков, никаких альтернатив через запятую или новую строку. Только один вариант.\n"
                    "- Никаких пояснений, вводных слов, кавычек вокруг всего названия, точек в конце, markdown-разметки.\n"
                    "- Если не уверен или не нашёл — ответь ровно одним словом: None\n\n"
                    f"Текст документа:\n{header_text}"
                ),
            }
        ],
        "stream": False,
    }

    headers = {}
    if settings.ollama_ocr_token:
        headers["Authorization"] = f"Bearer {settings.ollama_ocr_token}"

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=120.0)
        if response.status_code == 200:
            res_data = response.json()
            extracted = res_data.get("message", {}).get("content", "").strip()
            # Take only the first line (LLM sometimes returns bullet list of alternatives)
            extracted = extracted.split("\n")[0].strip()
            # Strip markdown bullet markers, surrounding quotes, whitespace
            extracted = extracted.lstrip("-*•").strip().strip("\"'").strip()
            if extracted.lower() in ("none", "нет", "не указан", "unknown"):
                return None
            local_logger.info("ollama_llm_creditor_success", creditor=extracted)
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

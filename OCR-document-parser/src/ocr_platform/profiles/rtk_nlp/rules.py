import re

# Заглушки регулярных выражений для базового поиска
# В реальном проекте тут нужно использовать NER-модель или более сложные паттерны.

CREDITOR_BLOCK_RE = re.compile(
    r"^\s*(?:Кредитор|Заявитель(?:\s*\(Кредитор\))?)\s*[:]?\s*\n?\s*"
    r"(.*?)"
    r"(?=\n\s*(?:ИНН|ОГРН|Адрес|Юридический|Почтовый|Представитель|Финансовый|Должник|Дата|Телефон|Тел|Email|\d{6}|\()|\s+ИНН|\n\s*\n|$)",
    re.IGNORECASE | re.DOTALL | re.MULTILINE
)
CLAIMS_AMOUNT_RE = re.compile(r"сумм[аеу]\s+требований\s*[:\-]?\s*(\d+[\d\s\.,]*)\s*(?:руб|р\.)", re.IGNORECASE)
GROUNDS_RE = re.compile(r"основани[ея]\s*[:\-]?\s*(договор[^\n\.]+)", re.IGNORECASE)

def extract_creditor(text: str) -> str | None:
    # 1. Сначала ищем по классическому блоку "Кредитор: ..."
    match = CREDITOR_BLOCK_RE.search(text)
    if match:
        raw_name = match.group(1)
        name = re.sub(r'\s+', ' ', raw_name).strip()
        name = re.sub(r'\(.*?\)', '', name).strip()
        if name:
            return name
            
    # 2. Если не нашли, ищем в тексте требований после ПРОШУ / ПРОСИТ СУД / ПРОСИМ
    block_match = re.search(r"(?i)\b(ПРОШУ(?:\s+СУД)?|ПРОСИТ\s+СУД|ПРОСИМ)\b[:\s]*(.*)", text, flags=re.DOTALL)
    if block_match:
        block = block_match.group(2)
        # Ищем паттерн "требование [КРЕДИТОР] в реестр / в размере / в рамках"
        req_match = re.search(r"(?i)(?:требовани[ея]?\s+)(.*?)(?:\s+в\s+реестр|\s+в\s+третью|\s+в\s+размере|\s+в\s+рамках|\s*[:])", block, flags=re.DOTALL)
        if req_match:
            raw_name = req_match.group(1).strip()
            name = re.sub(r'\s+', ' ', raw_name).strip()
            if name:
                return name
                
    return None

def extract_claims_amount(text: str) -> str | None:
    # Ищем блок после "ПРОШУ", "ПРОСИТ СУД", "ПРОСИМ"
    block_match = re.search(r"(?i)\b(ПРОШУ(?:\s+СУД)?|ПРОСИТ\s+СУД|ПРОСИМ)\b[:\s]*(.*)", text, flags=re.DOTALL)
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
    for m in re.finditer(r"((?:(?:\d{1,3}(?:[ \xA0]\d{3})+)|(?:\d+))(?:[,.]\d{1,2})?)\s*(?:\([^)]+\)\s*)?(?:руб|р\.|коп|₽|рублей|рубля)", block, flags=re.IGNORECASE):
        numbers[m.start(1)] = (parse_float(m.group(1)), m.start(1), m.end(1))
        
    # 2. После слов размере/сумме
    for m in re.finditer(r"(?:размере|сумме|размер|сумма)\s*[:]?\s*((?:(?:\d{1,3}(?:[ \xA0]\d{3})+)|(?:\d+))(?:[,.]\d{1,2})?)", block, flags=re.IGNORECASE):
        numbers[m.start(1)] = (parse_float(m.group(1)), m.start(1), m.end(1))
        
    # 3. Числа с копейками (две цифры после точки/запятой)
    for m in re.finditer(r"((?:(?:\d{1,3}(?:[ \xA0]\d{3})+)|(?:\d+))[,.]\d{2})(?!\d)", block):
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
    
    # Ищем сумму госпошлины. 
    # Вариант А: сумма идет ПОСЛЕ слова пошлина
    m_forward = re.search(r"(?i)(?:госпошлин|государственн\w*\s+пошлин\w*)[^\d]{0,100}?((?:(?:\d{1,3}(?:[ \xA0]\d{3})+)|(?:\d+))(?:[,.]\d{1,2})?)", text)
    # Вариант Б: сумма идет ДО слова пошлина
    m_backward = re.search(r"(?i)((?:(?:\d{1,3}(?:[ \xA0]\d{3})+)|(?:\d+))(?:[,.]\d{1,2})?)[^\d]{0,80}?(?:госпошлин|государственн\w*\s+пошлин\w*)", text)
    
    duty_candidates = []
    if m_forward:
        duty_candidates.append(parse_float(m_forward.group(1)))
    if m_backward:
        duty_candidates.append(parse_float(m_backward.group(1)))
        
    # Проверяем, что найденный кандидат вообще является денежной суммой в тексте
    valid_candidates = [c for c in duty_candidates if c in global_numbers]
    if valid_candidates:
        non_zero = [c for c in valid_candidates if c > 0]
        if non_zero:
            # Если найдено несколько (например, сумма ДО и сумма ПОСЛЕ пошлины)
            # госпошлина — это всегда меньшая сумма из соседних
            duty_amount = min(non_zero)
            
    if duty_amount >= total_amount:
        duty_amount = 0.0
        
    final_amount = total_amount - duty_amount
    return f"{final_amount:.2f}".replace(".", ",")


def extract_grounds(text: str) -> str | None:
    match = GROUNDS_RE.search(text)
    return match.group(1).strip() if match else None

import re

# Заглушки регулярных выражений для базового поиска
# В реальном проекте тут нужно использовать NER-модель или более сложные паттерны.

CREDITOR_BLOCK_RE = re.compile(
    r"(?:Кредитор|Заявитель(?:\s*\(Кредитор\))?)\s*[:]\s*"
    r"(.*?)"
    r"(?=\n\s*(?:ИНН|ОГРН|Адрес|Юридический|Почтовый|Представитель|Дата|\d{6}|\()|\s+ИНН)",
    re.IGNORECASE | re.DOTALL
)
CLAIMS_AMOUNT_RE = re.compile(r"сумм[аеу]\s+требований\s*[:\-]?\s*(\d+[\d\s\.,]*)\s*(?:руб|р\.)", re.IGNORECASE)
GROUNDS_RE = re.compile(r"основани[ея]\s*[:\-]?\s*(договор[^\n\.]+)", re.IGNORECASE)

def extract_creditor(text: str) -> str | None:
    match = CREDITOR_BLOCK_RE.search(text)
    if not match:
        return None
    
    raw_name = match.group(1)
    
    # Убираем переносы строк и лишние пробелы
    name = re.sub(r'\s+', ' ', raw_name).strip()
    
    # Убираем скобки и всё, что внутри
    name = re.sub(r'\(.*?\)', '', name).strip()
    
    return name if name else None

def extract_claims_amount(text: str) -> str | None:
    match = CLAIMS_AMOUNT_RE.search(text)
    return match.group(1).strip() if match else None

def extract_grounds(text: str) -> str | None:
    match = GROUNDS_RE.search(text)
    return match.group(1).strip() if match else None

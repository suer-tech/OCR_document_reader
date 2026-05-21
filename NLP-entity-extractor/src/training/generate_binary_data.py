import json
import random
import itertools
from pathlib import Path

random.seed(42)

# ================================
# POSITIVE TEMPLATES AND SYNONYMS
# ================================
PREFIXES = [
    "Обязать финансового управляющего",
    "Финансовому управляющему",
    "Суд обязывает финансового управляющего",
    "Предложить финансовому управляющему",
    "Надлежит",
    "",  # Без префикса (просто начало предложения с действия)
]

POS_TEMPLATES = [
    # 1. "до DATE представить"
    "{prefix} {deadline_prep} {date} {action} {target}",
    
    # 2. "не позднее чем за N дней до истечения срока"
    "{prefix} {action_prep} {target} не позднее чем за {days} дней до истечения срока",
    "{prefix} {action} {target} за {days} дней до завершения процедуры",
    
    # 3. "заблаговременно представить"
    "{prefix} заблаговременно {action} {target}",
    
    # 4. "отчет ... представить ... в срок до DATE"
    "{target} {action} в арбитражный суд в срок до {date}",
    "{target} {action} в суд до {date}",
]

DATES = ["09 ноября 2026 года", "15.01.2025", "30 сентября 2024 года", "1 декабря 2025 г.", "12 мая 2026", "25.12.2025"]
ACTIONS = ["представить", "направить", "предоставить суду", "сдать"]
ACTION_PREPS = ["необходимо представить", "обязан направить", "следует предоставить"]
TARGETS = [
    "документы по итогам процедуры реализации имущества", 
    "отчет о результатах реализации имущества", 
    "документы, предусмотренные статьей 213.28 Закона о банкротстве",
    "отчет финансового управляющего",
    "отчет о проделанной работе"
]
DEADLINE_PREPS = ["до", "в срок до", "не позднее", "заблаговременно"]
DAYS = ["3", "5", "за три", "за пять"]

def generate_positives() -> list[str]:
    generated = set()
    for _ in range(1000):
        t = random.choice(POS_TEMPLATES)
        text = t.format(
            prefix=random.choice(PREFIXES),
            date=random.choice(DATES),
            action=random.choice(ACTIONS),
            target=random.choice(TARGETS),
            deadline_prep=random.choice(DEADLINE_PREPS),
            days=random.choice(DAYS),
            action_prep=random.choice(ACTION_PREPS)
        )
        # Убираем двойные пробелы, если префикс был пустой
        text = " ".join(text.split()).capitalize()
        generated.add(text)
    return list(generated)

# ================================
# NEGATIVE TEMPLATES
# ================================
NEG_TEMPLATES = [
    # Общие фразы (без отчета)
    "признать должника несостоятельным (банкротом) и ввести процедуру реализации имущества.",
    "утвердить финансовым управляющим Иванова И.И.",
    "судебное заседание по рассмотрению отчета финансового управляющего назначить на {date}.",
    "взыскать с должника в пользу кредитора задолженность в размере 100 000 руб.",
    "отказать в удовлетворении ходатайства об отложении судебного заседания.",
    "отчет о результатах процедуры реструктуризации долгов рассмотрен судом {date}.",
    "обязать должника передать финансовому управляющему банковские карты и пароли.",
    "рассмотрев в открытом судебном заседании {target}, суд установил следующее.",
    "финансовый управляющий представил {target} {date}.",
    
    # Тонкие случаи: требуют отчет, но БЕЗ указания "до/заблаговременно/за N дней"
    "{prefix} {action} {target}.",
    "{prefix} {action} в суд {target} с приложением документов, предусмотренных статьей 213.28.",
    "Сведения о публикации представить в суд. {prefix} {action} в суд {target}.",
    "По итогам процедуры {prefix} {action} {target}.",
    "{action} {target} к судебному заседанию.",
]

def generate_negatives() -> list[str]:
    generated = set()
    for _ in range(1000):
        t = random.choice(NEG_TEMPLATES)
        text = t.format(
            prefix=random.choice(PREFIXES),
            date=random.choice(DATES),
            action=random.choice(ACTIONS),
            target=random.choice(TARGETS)
        )
        text = " ".join(text.split()).capitalize()
        generated.add(text)
    return list(generated)

def main():
    positives = generate_positives()
    negatives = generate_negatives()
    
    # Label 1 = Early Report Required
    # Label 0 = Not Required
    dataset = [{"text": text, "label": 1} for text in positives] + \
              [{"text": text, "label": 0} for text in negatives]
              
    random.shuffle(dataset)
    
    # 80/20 split
    split_idx = int(len(dataset) * 0.8)
    train_data = dataset[:split_idx]
    valid_data = dataset[split_idx:]
    
    out_dir = Path("data")
    out_dir.mkdir(exist_ok=True)
    
    with open(out_dir / "train_cls.jsonl", "w", encoding="utf-8") as f:
        for item in train_data:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
            
    with open(out_dir / "valid_cls.jsonl", "w", encoding="utf-8") as f:
        for item in valid_data:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
            
    print(f"Generated {len(train_data)} train examples and {len(valid_data)} validation examples.")

if __name__ == "__main__":
    main()

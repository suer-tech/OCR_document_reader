import argparse
import json
from pathlib import Path

import numpy as np
from datasets import Dataset, DatasetDict
from sklearn.metrics import accuracy_score, f1_score, precision_score, recall_score
from transformers import (
    AutoModelForSequenceClassification,
    AutoTokenizer,
    Trainer,
    TrainingArguments,
)

from inference.transformer import MODEL_ID


def compute_metrics(eval_pred):
    logits, labels = eval_pred
    predictions = np.argmax(logits, axis=-1)
    return {
        "accuracy": accuracy_score(labels, predictions),
        "f1": f1_score(labels, predictions, average="binary"),
        "precision": precision_score(labels, predictions, average="binary"),
        "recall": recall_score(labels, predictions, average="binary"),
    }


def load_jsonl(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8") as f:
        return [json.loads(line) for line in f]


def main():
    parser = argparse.ArgumentParser(description="Train Binary Classifier for Early Report Detection")
    parser.add_argument("--train-data", type=Path, default=Path("data/train_cls.jsonl"))
    parser.add_argument("--valid-data", type=Path, default=Path("data/valid_cls.jsonl"))
    parser.add_argument("--model-name", type=str, default="cointegrated/rubert-tiny2")
    parser.add_argument("--output-dir", type=Path, default=Path("models/exports/early-report-classifier"))
    parser.add_argument("--epochs", type=int, default=5)
    parser.add_argument("--batch-size", type=int, default=16)
    args = parser.parse_args()

    train_records = load_jsonl(args.train_data)
    valid_records = load_jsonl(args.valid_data)

    tokenizer = AutoTokenizer.from_pretrained(args.model_name)
    model = AutoModelForSequenceClassification.from_pretrained(
        args.model_name,
        num_labels=2,
        id2label={0: "NOT_REQUIRED", 1: "REQUIRED"},
        label2id={"NOT_REQUIRED": 0, "REQUIRED": 1},
    )

    dataset = DatasetDict(
        {
            "train": Dataset.from_list(train_records),
            "validation": Dataset.from_list(valid_records),
        }
    )

    def tokenize_function(examples):
        return tokenizer(examples["text"], padding="max_length", truncation=True, max_length=512)

    tokenized_datasets = dataset.map(tokenize_function, batched=True)

    training_args = TrainingArguments(
        output_dir=str(args.output_dir),
        eval_strategy="epoch",
        save_strategy="epoch",
        learning_rate=2e-5,
        per_device_train_batch_size=args.batch_size,
        per_device_eval_batch_size=args.batch_size,
        num_train_epochs=args.epochs,
        weight_decay=0.01,
        load_best_model_at_end=True,
        metric_for_best_model="f1",
    )

    trainer = Trainer(
        model=model,
        args=training_args,
        train_dataset=tokenized_datasets["train"],
        eval_dataset=tokenized_datasets["validation"],
        compute_metrics=compute_metrics,
    )

    trainer.train()
    
    # Save the model
    trainer.save_model(args.output_dir)
    tokenizer.save_pretrained(args.output_dir)
    
    # Evaluate and save metrics
    metrics = trainer.evaluate()
    (args.output_dir / "metadata.json").write_text(
        json.dumps(
            {
                "backend": "transformers-sequence-classification",
                "source_model": args.model_name,
                "metrics": metrics,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print("Training Complete. Metrics:")
    print(json.dumps(metrics, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

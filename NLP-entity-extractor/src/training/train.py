from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import numpy as np
from datasets import Dataset, DatasetDict
from seqeval.metrics import f1_score, precision_score, recall_score
from transformers import (
    AutoModelForTokenClassification,
    AutoTokenizer,
    DataCollatorForTokenClassification,
    Trainer,
    TrainingArguments,
)

from inference.transformer import MODEL_ID

from .dataset import load_jsonl

LABELS = [
    "O",
    "B-LAST_NAME",
    "I-LAST_NAME",
    "B-FIRST_NAME",
    "I-FIRST_NAME",
    "B-MIDDLE_NAME",
    "I-MIDDLE_NAME",
]
LABEL_TO_ID = {label: index for index, label in enumerate(LABELS)}
ID_TO_LABEL = {index: label for label, index in LABEL_TO_ID.items()}


def find_component_spans(text: str, record: dict) -> dict[str, tuple[int, int]]:
    parts = [record.get("last_name"), record.get("first_name"), record.get("patronymic")]
    if not all(parts):
        raise ValueError(f"Missing FIO components in record {record.get('document_id')}")
    pattern = re.compile(
        rf"(?P<last>{re.escape(parts[0])})\s+(?P<first>{re.escape(parts[1])})(?:\s+(?P<middle>{re.escape(parts[2])}))?",
        re.IGNORECASE,
    )
    match = pattern.search(text)
    if not match:
        raise ValueError(f"Unable to align FIO spans in record {record.get('document_id')}")
    result = {
        "LAST_NAME": match.span("last"),
        "FIRST_NAME": match.span("first"),
    }
    if match.group("middle"):
        result["MIDDLE_NAME"] = match.span("middle")
    return result


def prepare_examples(records: list[dict]) -> list[dict]:
    prepared = []
    for record in records:
        text = record.get("text") or ""
        if not text.strip():
            continue
        spans = find_component_spans(text, record)
        prepared.append({"text": text, "spans": spans})
    return prepared


def tokenize_and_align(batch, tokenizer):
    tokenized = tokenizer(batch["text"], truncation=True, max_length=512, return_offsets_mapping=True)
    labels_batch = []
    for offsets, spans in zip(tokenized["offset_mapping"], batch["spans"]):
        labels = []
        for start, end in offsets:
            if start == end:
                labels.append(-100)
                continue
            assigned = "O"
            for entity_name, (entity_start, entity_end) in spans.items():
                if start >= entity_start and end <= entity_end:
                    prefix = "B" if start == entity_start else "I"
                    assigned = f"{prefix}-{entity_name}"
                    break
            labels.append(LABEL_TO_ID[assigned])
        labels_batch.append(labels)
    tokenized["labels"] = labels_batch
    tokenized.pop("offset_mapping")
    return tokenized


def compute_metrics(eval_pred):
    logits, labels = eval_pred
    predictions = np.argmax(logits, axis=-1)
    true_predictions = []
    true_labels = []
    for prediction, label in zip(predictions, labels):
        pred_tags = []
        label_tags = []
        for pred_id, label_id in zip(prediction, label):
            if label_id == -100:
                continue
            pred_tags.append(ID_TO_LABEL[int(pred_id)])
            label_tags.append(ID_TO_LABEL[int(label_id)])
        true_predictions.append(pred_tags)
        true_labels.append(label_tags)
    return {
        "precision": precision_score(true_labels, true_predictions),
        "recall": recall_score(true_labels, true_predictions),
        "f1": f1_score(true_labels, true_predictions),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Fine-tune a local Russian NER model for FIO extraction")
    parser.add_argument("train_data", type=Path)
    parser.add_argument("--valid-data", type=Path, required=True)
    parser.add_argument("--model-name", default=MODEL_ID)
    parser.add_argument("--output-dir", type=Path, default=Path("models/exports/fio-ner-finetuned"))
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--batch-size", type=int, default=4)
    args = parser.parse_args()

    train_records = prepare_examples(load_jsonl(args.train_data))
    valid_records = prepare_examples(load_jsonl(args.valid_data))

    tokenizer = AutoTokenizer.from_pretrained(args.model_name)
    model = AutoModelForTokenClassification.from_pretrained(
        args.model_name,
        num_labels=len(LABELS),
        id2label=ID_TO_LABEL,
        label2id=LABEL_TO_ID,
        ignore_mismatched_sizes=True,
    )

    dataset = DatasetDict(
        {
            "train": Dataset.from_list(train_records),
            "validation": Dataset.from_list(valid_records),
        }
    )
    tokenized = dataset.map(lambda batch: tokenize_and_align(batch, tokenizer), batched=True, remove_columns=dataset["train"].column_names)

    training_args = TrainingArguments(
        output_dir=str(args.output_dir),
        eval_strategy="epoch",
        save_strategy="epoch",
        logging_strategy="epoch",
        learning_rate=3e-5,
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
        train_dataset=tokenized["train"],
        eval_dataset=tokenized["validation"],
        tokenizer=tokenizer,
        data_collator=DataCollatorForTokenClassification(tokenizer=tokenizer),
        compute_metrics=compute_metrics,
    )
    trainer.train()
    trainer.save_model(args.output_dir)
    tokenizer.save_pretrained(args.output_dir)
    metrics = trainer.evaluate()
    (args.output_dir / "metadata.json").write_text(
        json.dumps(
            {
                "backend": "transformers-token-classification",
                "source_model": args.model_name,
                "labels": LABELS,
                "metrics": metrics,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(json.dumps(metrics, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

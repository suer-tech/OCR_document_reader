"""Prepare a PRIVATE, auditable RTK3 subset. No network or model calls.

Original triples are copied byte-for-byte. Public/business corrections are not
silently used as document-extraction targets. Unresolved differences are masked,
not replaced with OCR predictions. The prepared output must not be committed.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
from hashlib import sha256
import html
import json
from pathlib import Path
import re
import shutil

from ocr_platform.services import rtk3_requisites as req
from ocr_platform.services.rtk3_evidence import money, source_case


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


MONEY = {"principal_debt", "financial_sanctions", "pau_only_amount", "claimed_amount", "total_amount", "total_claimed_amount"}


def normalized(name, value):
    if name in MONEY:
        number = money(value)
        return format(number, ".2f") if number is not None else value
    if name == "claims" and isinstance(value, list):
        return sorted([{k: normalized(k, v) for k, v in row.items()} for row in value],
                      key=lambda row: str(row.get("priority_queue")))
    return re.sub(r"\s+", " ", value).strip() if isinstance(value, str) else value


def amounts_consistent(fields):
    rows = fields.get("claims", {}).get("value")
    if not isinstance(rows, list) or not rows:
        return False
    nets = []
    for row in rows:
        vals = {key: money(row.get(key)) for key in MONEY - {"total_claimed_amount"}}
        if any(v is None for v in vals.values()):
            return False
        net = vals["principal_debt"] + vals["financial_sanctions"]
        if net != vals["claimed_amount"] or net + vals["pau_only_amount"] != vals["total_amount"]:
            return False
        nets.append(net)
    return sum(nets) == money(fields.get("total_claimed_amount", {}).get("value"))


def prepare(source: Path, output: Path):
    if source.resolve() == output.resolve() or source.resolve() in output.resolve().parents:
        raise ValueError("Output must be separate from the supplied dataset")
    output.mkdir(parents=True, exist_ok=True)
    stats = Counter()
    by_field = defaultdict(Counter)
    evaluation = defaultdict(Counter)
    manifest = []
    for folder in sorted(source.iterdir()):
        if not folder.is_dir():
            continue
        originals = [folder / name for name in ("ocr.json", "human.json", "source.pdf")]
        if not all(p.is_file() for p in originals):
            raise ValueError(f"Incomplete sample: {folder.name}")
        destination = output / folder.name
        destination.mkdir(exist_ok=True)
        hashes = {}
        for path in originals:
            hashes[path.name] = sha256(path.read_bytes()).hexdigest()
            saved = destination / path.name
            if saved.exists() and sha256(saved.read_bytes()).hexdigest() != hashes[path.name]:
                raise ValueError(f"Refusing to overwrite a different original: {saved}")
            shutil.copy2(path, saved)
        ocr, human = read(folder / "ocr.json"), read(folder / "human.json")
        text, original_fields = ocr["raw_text"], ocr["fields"]
        fields = deepcopy(human["fields"])
        labels, external, review, changes = {}, {}, {}, []
        compact_text = req.compact(text)

        def change(name, value, reason, quote):
            if req.compact(quote) not in compact_text:
                raise ValueError(f"Manual correction lacks a literal excerpt: {folder.name}/{name}")
            previous = deepcopy(fields[name]["value"])
            fields[name]["value"] = value
            changes.append({"field": name, "before": previous, "after": value, "reason": reason, "quote": quote})

        # Individually reviewed PDF corrections, gated by exact source evidence.
        # All other disagreements stay pending, rather than treating OCR as gold.
        if folder.name == "p3983":
            rows = deepcopy(fields["claims"]["value"])
            third = next(row for row in rows if row["priority_queue"] == "3 очередь")
            if money(third["principal_debt"]) != money("3435.87"):
                raise ValueError("Unexpected p3983 annotation revision")
            third.update(principal_debt="0.00", financial_sanctions="3435.87")
            change("claims", rows, "Пени и штрафы ошибочно размечены основным долгом.",
                   "задолженность по пени и штрафам в сумме 3 435,87 руб.")
        if folder.name == "p4006":
            quote = re.search(r"В удовлетворении заявления.{0,1500}?отказать\.", text, re.S)
            if not quote or "залогом" not in quote[0]:
                raise ValueError("Missing p4006 explicit collateral refusal")
            change("secured_by_pledge", False, "Суд прямо отказал в признании требования залоговым.", quote[0])
            change("pledge_resolution", "no", "Суд прямо отказал в признании требования залоговым.", quote[0])
        if folder.name == "p3994":
            rows = deepcopy(fields["claims"]["value"])
            if len(rows) != 1 or rows[0]["priority_queue"] != "За реестром":
                raise ValueError("Unexpected p3994 annotation revision")
            rows[0]["priority_queue"] = "3 очередь"
            change("claims", rows, "Отдельный учёт санкций внутри третьей очереди не означает «За реестром».",
                   "признать обоснованными и включить в третью очередь реестра требований кредиторов")

        document, main, case_quote = source_case(text)
        resolved = {"document_case_number": (document, case_quote), "case_number": (main, case_quote)}
        ruling_date = req.ruling_date(text)
        resolved["inclusion_date"] = (ruling_date.value, ruling_date.quote)
        for name in ("debtor_inn", "creditor_inn"):
            # Evaluation must not give the extractor the human creditor target.
            r = req.source_inn(text, name, original_fields.get("creditor", {}).get("value"))
            resolved[name] = (r.value, r.quote)
        for name in ("case_number", "inclusion_date", "debtor_inn", "creditor_inn"):
            old = original_fields[name]["value"]
            candidate = resolved[name][0] or old
            target = human["fields"][name]["value"]
            evaluation[name]["samples"] += 1
            evaluation[name]["original_match"] += old == target
            evaluation[name]["local_rules_match"] += candidate == target
            evaluation[name]["nonempty_conflict"] += bool(candidate and candidate != target)

        for name, info in fields.items():
            value = info["value"]
            old = original_fields.get(name, {}).get("value")
            status, quote = "", ""
            if name == "processing_started_at":
                external[name] = {"value": value, "origin": "processing_metadata"}
            elif name == "kad_publication_date":
                external[name] = {"value": value, "origin": "confirmed_emu_value", "reason": "Дата публикации не входит в результат извлечения РТК-3."}
                labels[name] = {"value": None, "status": "not_present_in_document", "quote": ""}
            elif name in ("debtor_inn", "creditor_inn") and value and str(value) not in re.sub(r"\s", "", text):
                external[name] = {"value": value, "origin": "confirmed_emu_value", "reason": "Подтверждённые цифры отсутствуют в исходном тексте."}
                if not resolved[name][0] and not req.INN.findall(text):
                    labels[name] = {"value": None, "status": "not_present_in_document", "quote": ""}
                else:
                    review[name] = {"value": value, "reason": "Нужна проверка реквизитов участника; внешнее значение не является меткой извлечения."}
            elif name in resolved and resolved[name][0]:
                actual, quote = resolved[name]
                if actual == value:
                    status = "source_bound_rule"
                elif name == "document_case_number" and main == fields.get("case_number", {}).get("value"):
                    change(name, actual, "Для document_case_number сохранён полный индекс документа; основное дело хранится отдельно.", quote)
                    value, status = actual, "source_bound_rule"
                else:
                    review[name] = {"value": value, "source_value": actual, "quote": quote, "reason": "Подтверждённая форма расходится с реквизитом источника; не заменено автоматически."}
            elif name in ("secured_by_pledge", "pledge_resolution") and folder.name == "p4006":
                status = "manually_checked_pdf"
                quote = next(c["quote"] for c in changes if c["field"] == name)
            elif name == "claims" and folder.name == "p3983":
                status = "manually_checked_pdf"
                quote = next(c["quote"] for c in changes if c["field"] == name)
            elif name in ("claims", "total_claimed_amount") and not amounts_consistent(fields):
                review[name] = {"value": value, "reason": "Пустая денежная ячейка или несогласованная арифметика подтверждённой формы."}
            elif normalized(name, value) == normalized(name, old) and value not in (None, "", []):
                status = "human_confirmed_ocr_agreement"
            elif name == "grounds_ocr" and isinstance(value, str) and value and req.compact(value) in compact_text:
                status, quote = "literal_source", value
            else:
                review[name] = {"value": value, "ocr_value": old, "reason": "Расхождение ещё не подтверждено независимой проверкой; исключено из обучающей цели."}
            if status:
                labels[name] = {"value": normalized(name, value), "status": status, "quote": quote}
            if name in labels:
                by_field[name]["included"] += 1
            if name in external:
                by_field[name]["external"] += 1
            if name in review:
                by_field[name]["pending"] += 1

        # A correction to a single aspect of claims does not certify its other
        # monetary components. p3994 remains pending despite its corrected queue.
        write(destination / "human.cleaned.json", {"fields": fields, "notice": "Рабочая копия, не безусловный эталон. Для обучения используйте document_labels.json и маску полей."})
        write(destination / "document_labels.json", {"fields": labels, "semantics": "An omitted field is masked, not a null/negative target. Null labels are explicit source-absence labels."})
        write(destination / "external_data.json", {"fields": external})
        write(destination / "review_required.json", {"fields": review})
        write(destination / "changes.json", changes)
        stats["samples"] += 1
        stats["included_field_targets"] += len(labels)
        stats["external_values"] += len(external)
        stats["pending_fields"] += len(review)
        stats["changes"] += len(changes)
        stats["samples_with_pending_fields"] += bool(review)
        manifest.append({"bundle": folder.name, "source_sha256": hashes,
                         "included_fields": sorted(labels), "masked_fields": sorted(set(fields) - set(labels)),
                         "external_fields": sorted(external), "pending_fields": sorted(review), "changes": changes})

    summary = {"source": str(source.resolve()), "output": str(output.resolve()), "counts": dict(stats),
               "fields": dict(by_field), "local_requisite_evaluation": dict(evaluation),
               "limitations": ["Это не полный повторный прогон LLM.", "Согласие OCR и человека сохранено как отдельный уровень доверия, не как независимая проверка PDF.",
                               "Спорные поля исключены из целей; null не подставляется вместо маски.", "Исходная папка не изменена."]}
    write(output / "summary.json", summary)
    (output / "manifest.jsonl").write_text("".join(json.dumps(row,ensure_ascii=False)+"\n" for row in manifest),encoding="utf-8")
    report = ['<!doctype html><meta charset="utf-8"><title>РТК3: подготовленный train</title><style>body{font:16px/1.6 system-ui;max-width:1150px;margin:40px auto;padding:0 20px;color:#203045}pre{white-space:pre-wrap}td,th{padding:10px;border-bottom:1px solid #ccc;text-align:left}details{margin:18px 0}</style><h1>РТК3: рабочий обучающий набор</h1>',
              f'<p>{stats["samples"]} исходных документов сохранены. Поля внешнего происхождения и неразобранные расхождения отделены. Отсутствующее поле в document_labels — маска, а не целевой null. Полный LLM-прогон не выполнялся; прод не менялся.</p>',
              '<pre>'+html.escape(json.dumps(summary,ensure_ascii=False,indent=2))+'</pre>', '<h2>Документы и изменения</h2>']
    for row in manifest:
        b = row['bundle']
        report.append(f'<details><summary>{b}: {len(row["included_fields"])} целей, {len(row["pending_fields"])} полей на проверку</summary><p><a href="{b}/source.pdf">PDF</a> · <a href="{b}/human.cleaned.json">Рабочая разметка</a> · <a href="{b}/document_labels.json">Цели извлечения</a> · <a href="{b}/external_data.json">Внешние данные</a> · <a href="{b}/review_required.json">Спорные поля</a></p><pre>{html.escape(json.dumps(row["changes"],ensure_ascii=False,indent=2))}</pre></details>')
    (output / 'REPORT.html').write_text('\n'.join(report),encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False,indent=2))
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    prepare(args.source, args.output)

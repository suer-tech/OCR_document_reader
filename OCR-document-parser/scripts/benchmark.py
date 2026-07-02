import asyncio
import base64
import json
import time
import re
import argparse
import csv
from datetime import datetime
from pathlib import Path
import httpx

BASE_DIR = Path(__file__).resolve().parent.parent
DATASET_DIR = BASE_DIR.parent / "документы" / "test_rtk" / "решения_датасет"
DATASET_MD = DATASET_DIR / "dataset.md"
REPORT_DIR = BASE_DIR / "reports"
REPORT_DIR.mkdir(parents=True, exist_ok=True)
HISTORY_CSV = REPORT_DIR / "history.csv"


def parse_dataset(file_path):
    content = Path(file_path).read_text(encoding="utf-8")
    lines = content.splitlines()
    dataset = {}
    current_file = None
    for line in lines:
        line_stripped = line.strip()
        if not line_stripped:
            continue
        if line.startswith(" ") or line.startswith("\t"):
            if ":" in line_stripped:
                key, val = line_stripped.split(":", 1)
                key = key.strip()
                val = val.strip()
                if current_file:
                    dataset[current_file][key] = val
        else:
            current_file = line_stripped
            dataset[current_file] = {}
    return dataset


def normalize_amount(val):
    if val is None:
        return None
    val_str = str(val).replace(" ", "").replace("\xa0", "").replace(",", ".")
    try:
        return float(val_str)
    except ValueError:
        return val_str


def normalize_name(name):
    if not name:
        return ""
    name = str(name).lower().replace("ё", "е")
    name = re.sub(r'["\'«»“”]', "", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


def compare_values(field, expected, extracted):
    if field == "claims_amount":
        exp_norm = normalize_amount(expected)
        ext_norm = normalize_amount(extracted)
        if exp_norm is None or ext_norm is None:
            return exp_norm == ext_norm
        if isinstance(exp_norm, float) and isinstance(ext_norm, float):
            return abs(exp_norm - ext_norm) < 0.01
        return exp_norm == ext_norm
    elif field in ("creditor", "grounds"):
        return normalize_name(expected) == normalize_name(extracted)
    else:
        # creditor_inn
        exp_str = str(expected or "").strip()
        ext_str = str(extracted or "").strip()
        return exp_str == ext_str


async def wait_for_pipeline(
    client: httpx.AsyncClient, pipeline_run_id: str, timeout: int = 1800
) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = await client.get(f"/pipeline-runs/{pipeline_run_id}")
            if r.status_code != 200:
                await asyncio.sleep(2)
                continue
            data = r.json()
            status = data.get("status")
            if status in ("done", "failed"):
                return data
        except Exception:
            pass
        await asyncio.sleep(2)
    return {"status": "timeout"}


async def process_document(client: httpx.AsyncClient, file_path: Path) -> dict:
    doc_bytes = file_path.read_bytes()
    b64 = base64.b64encode(doc_bytes).decode("utf-8")
    file_name = file_path.name

    ingest_payload = {
        "content_base64": b64,
        "content_type": "pdf",
        "source_type": "portal",
        "document_type": "rtk",
        "idempotency_key": f"benchmark-{file_name}-{time.time_ns()}",
    }

    try:
        r = await client.post("/documents/ingest", json=ingest_payload)
        if r.status_code not in (200, 202):
            return {"error": f"Ingest failed: {r.status_code} {r.text}"}

        data = r.json()
        doc_id = data.get("document_id") or data.get("id")
        run_id = data.get("pipeline_run_id")

        run_res = await wait_for_pipeline(client, run_id)
        if run_res.get("status") != "done":
            return {
                "error": f"Pipeline failed: {run_res.get('status')}. Error: {run_res.get('last_error')}"
            }

        res_resp = await client.get(f"/documents/{doc_id}/result")
        if res_resp.status_code != 200:
            return {"error": f"Result fetch failed: {res_resp.status_code}"}

        return res_resp.json()
    except Exception as e:
        import traceback

        traceback.print_exc()
        return {"error": f"Exception occurred: {type(e).__name__}: {e}"}


def save_history(timestamp, note, accuracy, avg_time, total_time, errors):
    file_exists = HISTORY_CSV.exists()
    with open(HISTORY_CSV, mode="a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(
                [
                    "Timestamp",
                    "Note",
                    "Accuracy (%)",
                    "Avg Time (s)",
                    "Total Time (s)",
                    "Errors",
                ]
            )
        writer.writerow(
            [
                timestamp,
                note,
                f"{accuracy:.2f}",
                f"{avg_time:.2f}",
                f"{total_time:.2f}",
                errors,
            ]
        )


async def main():
    parser = argparse.ArgumentParser(description="Run RTK Benchmark")
    parser.add_argument(
        "--note",
        type=str,
        default="Default Run",
        help="Notes or description for this benchmark run",
    )
    parser.add_argument(
        "--api-url",
        type=str,
        default="http://localhost:8000",
        help="API URL to test against",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Limit the number of documents to process (0 = all)",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=3,
        help="Number of concurrent documents to process",
    )
    args = parser.parse_args()

    ground_truth = parse_dataset(DATASET_MD)
    if args.limit > 0:
        ground_truth = dict(list(ground_truth.items())[: args.limit])
    print(f"Loaded {len(ground_truth)} ground truth entries from {DATASET_MD}")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_md_path = REPORT_DIR / f"rtk_benchmark_{timestamp}.md"

    sem = asyncio.Semaphore(args.concurrency)

    async def process_one(
        filename: str, expected: dict, idx: int, total: int, client: httpx.AsyncClient
    ) -> tuple[str, dict]:
        async with sem:
            clean_name = (
                filename.replace("2/", "", 1) if filename.startswith("2/") else filename
            )
            filepath = DATASET_DIR / clean_name
            if not filepath.exists():
                print(
                    f"[{idx}/{total}] WARNING: file not found: {filename} ({clean_name})"
                )
                return filename, {
                    "error": f"file not found: {clean_name}",
                    "expected": expected,
                    "extracted": {},
                    "time": 0,
                    "tokens": "N/A",
                }

            print(
                f"[{idx}/{total}] Processing {filename}...",
                end="",
                flush=True,
            )
            t0 = time.time()
            res = await process_document(client, filepath)
            dt = time.time() - t0

            if "error" in res:
                print(f" ERROR: {res['error']} ({dt:.1f}s)")
                return filename, {
                    "error": res["error"],
                    "expected": expected,
                    "extracted": {},
                    "time": dt,
                    "tokens": res.get("usage", "N/A"),
                }
            else:
                print(f" DONE ({dt:.1f}s)")
                extracted_fields = {}
                fields_data = res.get("extracted_fields")
                if fields_data and isinstance(fields_data, dict):
                    extracted_fields = fields_data
                else:
                    fields_data = res.get("fields", {})
                    for f_name, f_val in fields_data.items():
                        extracted_fields[f_name] = f_val.get("value")
                return filename, {
                    "expected": expected,
                    "extracted": extracted_fields,
                    "time": dt,
                    "tokens": res.get("usage", "N/A"),
                }

    async with httpx.AsyncClient(base_url=args.api_url, timeout=120.0) as client:
        tasks = []
        for idx, (filename, expected) in enumerate(ground_truth.items(), 1):
            tasks.append(
                process_one(filename, expected, idx, len(ground_truth), client)
            )
        raw_results = await asyncio.gather(*tasks)
        results = dict(raw_results)

        # Generate Markdown Report
        markdown = []
        markdown.append("# Отчет о тестировании извлечения данных РТК")
        markdown.append(f"Дата запуска: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        markdown.append(f"Заметка: `{args.note}`")
        markdown.append(f"API URL: `{args.api_url}`")
        markdown.append("")

        total_fields = 0
        total_correct = 0
        total_time = 0.0
        error_count = 0

        table_rows = []

        for filename, data in results.items():
            expected = data["expected"]
            extracted = data["extracted"]
            dt = data.get("time", 0.0)
            tokens = data.get("tokens", "N/A")
            total_time += dt

            markdown.append(f"## Документ: {filename}")
            markdown.append(
                f"**Затрачено времени**: {dt:.2f} сек. | **Токены**: {tokens}"
            )

            if "error" in data:
                markdown.append(f"**Ошибка**: {data['error']}")
                markdown.append("")
                error_count += 1
                continue

            doc_fields = ["creditor_inn", "creditor", "claims_amount", "grounds"]
            doc_correct = 0

            markdown.append(
                "| Поле | Ожидаемое значение | Извлеченное значение | Результат |"
            )
            markdown.append("| --- | --- | --- | --- |")

            for field in doc_fields:
                exp_val = expected.get(field)
                ext_val = extracted.get(field)
                is_ok = compare_values(field, exp_val, ext_val)

                status_str = "✅ Совпало" if is_ok else "❌ Не совпало"
                markdown.append(f"| `{field}` | {exp_val} | {ext_val} | {status_str} |")

                total_fields += 1
                if is_ok:
                    total_correct += 1
                    doc_correct += 1

            doc_score = (doc_correct / len(doc_fields)) * 100 if doc_fields else 0
            markdown.append(
                f"\nТочность по документу: **{doc_score:.1f}%** ({doc_correct}/{len(doc_fields)})"
            )
            markdown.append("")

            table_rows.append(
                f"| {filename} | {doc_score:.1f}% ({doc_correct}/{len(doc_fields)}) |"
            )

        overall_score = (total_correct / total_fields) * 100 if total_fields > 0 else 0
        avg_time = total_time / len(results) if len(results) > 0 else 0

        summary = []
        summary.append("## Общая сводка")
        summary.append(f"- Всего полей для проверки: **{total_fields}**")
        summary.append(f"- Совпало успешно: **{total_correct}**")
        summary.append(f"- Ошибок/несовпадений: **{total_fields - total_correct}**")
        summary.append(f"- Документов с ошибкой API: **{error_count}**")
        summary.append(
            f"- Общее время обработки: **{total_time:.2f} сек.** (в среднем **{avg_time:.2f} сек.** на документ)"
        )
        summary.append(f"- Общий скор (Accuracy): **{overall_score:.2f}%**")
        summary.append("")
        summary.append("### Сводная таблица по документам")
        summary.append("| Документ | Скор (Верно / Всего) |")
        summary.append("| --- | --- |")
        summary.extend(table_rows)

        markdown.insert(5, "\n".join(summary) + "\n")

        report_md_path.write_text("\n".join(markdown), encoding="utf-8")
        print(f"Report generated successfully: {report_md_path}")
        print(f"Overall Accuracy: {overall_score:.2f}%")

        save_history(
            timestamp, args.note, overall_score, avg_time, total_time, error_count
        )
        print(f"Metrics saved to {HISTORY_CSV}")


if __name__ == "__main__":
    asyncio.run(main())

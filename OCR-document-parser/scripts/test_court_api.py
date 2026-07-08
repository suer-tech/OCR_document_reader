import asyncio
import json
import time
from datetime import datetime
from pathlib import Path
import httpx

BASE_DIR = Path(__file__).resolve().parent.parent
TEST_DIR = BASE_DIR.parent / "документы" / "test_court"
RESULTS_DIR = BASE_DIR / "reports"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

API_URL = "http://localhost:8000"
POLL_TIMEOUT = 1800
POLL_INTERVAL = 2


async def wait_for_pipeline(client: httpx.AsyncClient, pipeline_run_id: str) -> dict:
    deadline = time.time() + POLL_TIMEOUT
    while time.time() < deadline:
        try:
            r = await client.get(f"/pipeline-runs/{pipeline_run_id}")
            if r.status_code != 200:
                await asyncio.sleep(POLL_INTERVAL)
                continue
            data = r.json()
            status = data.get("status")
            if status in ("done", "failed"):
                return data
        except Exception:
            pass
        await asyncio.sleep(POLL_INTERVAL)
    return {"status": "timeout"}


async def process_document(client: httpx.AsyncClient, file_path: Path) -> dict:
    file_name = file_path.name
    idempotency_key = f"test-court-{file_name}-{time.time_ns()}"

    try:
        with open(file_path, "rb") as f:
            files = {"file": (file_name, f, "application/pdf")}
            data = {
                "source_type": "external",
                "document_type": "court_decision",
                "idempotency_key": idempotency_key,
            }
            r = await client.post("/documents/upload", data=data, files=files)

        if r.status_code not in (200, 202):
            return {
                "file": file_name,
                "error": f"Upload failed: {r.status_code} {r.text}",
            }

        resp = r.json()
        doc_id = resp.get("document_id")
        run_id = resp.get("pipeline_run_id")

        run_res = await wait_for_pipeline(client, run_id)
        if run_res.get("status") != "done":
            return {
                "file": file_name,
                "error": f"Pipeline {run_res.get('status')}: {run_res.get('last_error')}",
            }

        res_resp = await client.get(f"/documents/{doc_id}/result")
        if res_resp.status_code != 200:
            return {
                "file": file_name,
                "error": f"Result fetch failed: {res_resp.status_code}",
            }

        result = res_resp.json()
        result["_file"] = file_name
        return result

    except Exception as e:
        return {"file": file_name, "error": f"Exception: {type(e).__name__}: {e}"}


async def main():
    pdf_files = sorted(TEST_DIR.glob("*.pdf"))
    if not pdf_files:
        print(f"No PDF files found in {TEST_DIR}")
        return

    print(f"Found {len(pdf_files)} files in {TEST_DIR}")
    print(f"API URL: {API_URL}")
    print()

    results = []
    async with httpx.AsyncClient(base_url=API_URL, timeout=300.0) as client:
        for idx, file_path in enumerate(pdf_files, 1):
            print(
                f"[{idx}/{len(pdf_files)}] Processing {file_path.name}...",
                end="",
                flush=True,
            )
            t0 = time.time()
            res = await process_document(client, file_path)
            dt = time.time() - t0

            if "error" in res:
                print(f" ERROR ({dt:.1f}s): {res['error']}")
            else:
                print(f" DONE ({dt:.1f}s)")
            results.append(res)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = RESULTS_DIR / f"court_decision_results_{timestamp}.json"
    output_path.write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\nResults saved to {output_path}")

    summary_path = RESULTS_DIR / f"court_decision_summary_{timestamp}.md"
    md_lines = [
        f"# Тестирование court_decision",
        f"Дата: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"API URL: {API_URL}",
        f"Всего файлов: {len(results)}",
        "",
        "| Файл | Статус | Время (с) |",
        "| --- | --- | --- |",
    ]
    for res in results:
        status = "✅ Done" if "error" not in res else f"❌ {res['error']}"
        md_lines.append(
            f"| {res.get('_file', res.get('file', '?'))} | {status} | {res.get('_time', 0):.1f} |"
        )
    summary_path.write_text("\n".join(md_lines), encoding="utf-8")
    print(f"Summary saved to {summary_path}")


if __name__ == "__main__":
    asyncio.run(main())

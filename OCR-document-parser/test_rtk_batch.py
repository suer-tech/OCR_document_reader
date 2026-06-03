import asyncio
import base64
import json
import time
from datetime import datetime
from pathlib import Path

import httpx

API_URL = "http://localhost:8000"
BASE_DIR = Path(__file__).resolve().parent.parent
TEST_DIR = BASE_DIR / "документы" / "test_rtk"
RESULTS_FILE = BASE_DIR / "OCR-document-parser" / "test_rtk_results.json"


async def wait_for_pipeline(
    client: httpx.AsyncClient, pipeline_run_id: str, timeout: int = 300
) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = await client.get(f"/pipeline-runs/{pipeline_run_id}")
        if r.status_code != 200:
            return {"error": f"status {r.status_code}: {r.text}"}
        data = r.json()
        status = data.get("status")
        if status in ("done", "failed"):
            return data
        await asyncio.sleep(2)
    return {"error": "timeout"}


async def process_document(file_path: Path) -> dict:
    doc_bytes = file_path.read_bytes()
    b64 = base64.b64encode(doc_bytes).decode("utf-8")

    file_name = file_path.name

    ext = file_path.suffix.lower()
    content_type = "pdf" if ext == ".pdf" else "image"

    async with httpx.AsyncClient(base_url=API_URL, timeout=30) as client:
        t0 = time.time()

        ingest_payload = {
            "content_base64": b64,
            "content_type": content_type,
            "source_type": "portal",
            "document_type": "rtk",
            "idempotency_key": f"test-batch-{file_name}-{time.time_ns()}",
        }
        ingest_resp = await client.post("/documents/ingest", json=ingest_payload)
        if ingest_resp.status_code not in (200, 202):
            return {
                "file": file_name,
                "error": f"ingest failed: {ingest_resp.status_code} {ingest_resp.text}",
            }

        ingest_data = ingest_resp.json()
        document_id = ingest_data.get("document_id")
        pipeline_run_id = ingest_data.get("pipeline_run_id")

        pipeline_result = await wait_for_pipeline(client, pipeline_run_id)
        ingest_latency = time.time() - t0

        result = {
            "file": file_name,
            "document_id": document_id,
            "pipeline_run_id": pipeline_run_id,
            "ingest_latency_sec": round(ingest_latency, 2),
            "pipeline_status": pipeline_result.get("status"),
        }

        if pipeline_result.get("status") == "done":
            doc_resp = await client.get(f"/documents/{document_id}/result")
            if doc_resp.status_code == 200:
                doc_data = doc_resp.json()
                result["result"] = doc_data

        return result


async def main():
    files = sorted(TEST_DIR.glob("*.pdf"))
    print(f"Found {len(files)} PDF files in {TEST_DIR}")
    print("-" * 80)

    all_results = []
    total_start = time.time()

    RESULTS_FILE.parent.mkdir(parents=True, exist_ok=True)

    for i, f in enumerate(files, 1):
        print(f"[{i}/{len(files)}] Processing: {f.name} ...", end=" ", flush=True)
        start = time.time()
        res = await process_document(f)
        elapsed = time.time() - start
        status = res.get("pipeline_status", "error")
        print(f"{elapsed:.1f}s  status={status}")
        all_results.append(res)

        # Save incrementally after every file
        partial = {
            "timestamp": datetime.now().isoformat(),
            "total_files": len(files),
            "processed": i,
            "total_time_sec": round(time.time() - total_start, 2),
            "results": all_results,
        }
        RESULTS_FILE.write_text(
            json.dumps(partial, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    total_elapsed = time.time() - total_start

    output = {
        "timestamp": datetime.now().isoformat(),
        "total_files": len(files),
        "total_time_sec": round(total_elapsed, 2),
        "results": all_results,
    }

    RESULTS_FILE.write_text(
        json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("-" * 80)
    print(f"Results saved to {RESULTS_FILE}")
    print(
        f"Total time: {total_elapsed:.1f}s for {len(files)} files ({total_elapsed / len(files):.1f}s avg)"
    )


if __name__ == "__main__":
    asyncio.run(main())

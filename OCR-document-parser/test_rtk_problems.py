import asyncio
import base64
import json
import time
from pathlib import Path

import httpx

API_URL = "http://localhost:8000"
BASE_DIR = Path(__file__).resolve().parent.parent
TEST_DIR = BASE_DIR / "документы" / "test_rtk"

PROBLEM_FILES = [
    "ВОКСИС РТК_0003.pdf",
    "СФО ТИТАН РТК_0001.pdf",
    "ФЕНИКС РТК_0001.pdf",
    "ТБАНК РТК_0003.pdf",
    "ВТБ РТК_0001 (1).pdf",
    "ВТБ РТК_0001.pdf",
    "ВТБ РТК_0004.pdf",
    "Правовой центр ОДА РТК_0001.pdf",
]


async def main():
    results = {}
    async with httpx.AsyncClient(base_url=API_URL, timeout=60) as client:
        for fname in PROBLEM_FILES:
            fpath = TEST_DIR / fname
            if not fpath.exists():
                results[fname] = "FILE NOT FOUND"
                print(f"{fname}: FILE NOT FOUND")
                continue
            b64 = base64.b64encode(fpath.read_bytes()).decode()
            payload = {
                "content_base64": b64,
                "content_type": "pdf",
                "source_type": "portal",
                "document_type": "rtk",
                "idempotency_key": f"test-problem-{fname}-{time.time_ns()}",
            }
            r = await client.post("/documents/ingest", json=payload, timeout=60)
            data = r.json()
            doc_id = data.get("document_id")
            pipeline_id = data.get("pipeline_run_id")

            deadline = time.time() + 300
            status = "pending"
            while time.time() < deadline:
                pr = await client.get(f"/pipeline-runs/{pipeline_id}")
                pr_data = pr.json()
                status = pr_data.get("status")
                if status in ("done", "failed"):
                    break
                await asyncio.sleep(3)

            if status == "done" and doc_id:
                dr = await client.get(f"/documents/{doc_id}/result")
                if dr.status_code == 200:
                    doc_result = dr.json()
                    creditor = "N/A"
                    fields = doc_result.get("extracted_fields", {})
                    if isinstance(fields, dict):
                        creditor = fields.get("creditor_name", "N/A")
                    results[fname] = creditor
                else:
                    results[fname] = f"RESULT ERR: {dr.status_code}"
            else:
                results[fname] = f"PIPELINE: {status}"

            print(f"{fname}: {results[fname]}")

    Path("test_problem_results.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("\nDone. Saved to test_problem_results.json")


if __name__ == "__main__":
    asyncio.run(main())

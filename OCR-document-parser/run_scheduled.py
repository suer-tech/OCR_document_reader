import subprocess
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent


def run_benchmark(note: str):
    cmd = [
        sys.executable,
        "scripts/benchmark.py",
        "--note",
        note,
        "--concurrency",
        "3",
    ]
    print(f"[{time.strftime('%H:%M:%S')}] Starting benchmark: {note}")
    result = subprocess.run(cmd, cwd=str(BASE))
    if result.returncode != 0:
        print(
            f"[{time.strftime('%H:%M:%S')}] Benchmark {note} FAILED with code {result.returncode}"
        )
    else:
        print(f"[{time.strftime('%H:%M:%S')}] Benchmark {note} completed successfully")


if __name__ == "__main__":
    # Run 2: after 3 hours from first run (~21:38)
    # Run 3: after 6 hours from first run (~00:38)
    # Run 4: after 9 hours from first run (~03:38)

    intervals = [10800, 21600, 32400]  # 3h, 6h, 9h from now
    notes = ["Run 2 - after 3h", "Run 3 - after 6h", "Run 4 - after 9h"]

    start = time.time()
    for delay, note in zip(intervals, notes):
        wait = delay - (time.time() - start)
        if wait > 0:
            hours = wait / 3600
            print(
                f"[{time.strftime('%H:%M:%S')}] Waiting {wait:.0f}s ({hours:.1f}h) for {note}..."
            )
            time.sleep(wait)
        run_benchmark(note)

    print(f"[{time.strftime('%H:%M:%S')}] All scheduled runs completed")

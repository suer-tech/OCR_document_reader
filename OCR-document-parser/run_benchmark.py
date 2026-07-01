import sys
import time
import subprocess
import httpx

def main():
    print("Starting API Server in background...")
    server_process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "src.ocr_platform.api.main:app", "--host", "127.0.0.1", "--port", "8000"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True
    )
    
    try:
        # Wait for server to be ready
        print("Waiting for server to become ready...")
        ready = False
        for _ in range(30):
            try:
                r = httpx.get("http://127.0.0.1:8000/docs", timeout=1.0)
                if r.status_code == 200:
                    ready = True
                    break
            except httpx.RequestError:
                pass
            time.sleep(1)
            
        if not ready:
            print("ERROR: Server failed to start in time. Check uvicorn logs if any.")
            server_process.terminate()
            sys.exit(1)
            
        print("Server is ready! Running benchmark...")
        
        # Run benchmark script with all arguments passed to this wrapper
        benchmark_cmd = [sys.executable, "scripts/benchmark.py"] + sys.argv[1:]
        subprocess.run(benchmark_cmd, check=False)
        
    finally:
        print("Shutting down API Server...")
        server_process.terminate()
        try:
            server_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server_process.kill()
            
if __name__ == "__main__":
    main()

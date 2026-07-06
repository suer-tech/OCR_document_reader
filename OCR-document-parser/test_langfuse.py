#!/usr/bin/env python3
"""Test script to verify Langfuse connectivity from inside the worker container."""
import os
print("=== Langfuse Environment Variables ===")
print(f"LANGFUSE_BASE_URL: {os.environ.get('LANGFUSE_BASE_URL', 'NOT SET')}")
print(f"LANGFUSE_PUBLIC_KEY: {os.environ.get('LANGFUSE_PUBLIC_KEY', 'NOT SET')}")
print(f"LANGFUSE_SECRET_KEY: {os.environ.get('LANGFUSE_SECRET_KEY', 'NOT SET')[:20]}...")

print("\n=== Testing HTTP Connectivity ===")
import httpx
try:
    r = httpx.get(f"{os.environ.get('LANGFUSE_BASE_URL', 'http://langfuse:3000')}/api/public/health", timeout=5)
    print(f"Health endpoint: {r.status_code} -> {r.text[:200]}")
except Exception as e:
    print(f"Health endpoint FAILED: {e}")

print("\n=== Testing Langfuse SDK v4 ===")
from langfuse import Langfuse, observe, get_client

lf = get_client()
print(f"Langfuse client initialized, _base_url: {lf._base_url}")

# Auth check
try:
    lf.auth_check()
    print("Auth check: PASSED")
except Exception as e:
    print(f"Auth check: FAILED -> {e}")

print("\n=== Testing @observe decorator ===")

@observe(name="test-connectivity-trace")
def test_function():
    return "Hello from test!"

result = test_function()
print(f"Function returned: {result}")

print("\n=== Flushing ===")
lf.flush()
print("Flush complete!")
print("\nIf no errors above, check Langfuse dashboard for 'test-connectivity-trace'")

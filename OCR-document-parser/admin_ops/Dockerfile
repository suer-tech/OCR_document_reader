FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates && \
    rm -rf /var/lib/apt/lists/* && \
    useradd --create-home --uid 10001 ops

# Separate lightweight image: no OCR models, documents, production Docker socket or API credentials.
RUN pip install --no-cache-dir \
    'openai-codex==0.154.0' \
    'mcp==1.30.0' \
    'fastapi>=0.111,<1' \
    'uvicorn>=0.30,<1' \
    'httpx>=0.27,<1' \
    'sqlalchemy>=2.0,<3' \
    'psycopg[binary]>=3.2,<4' \
    'pydantic-settings>=2.2,<3'

COPY admin_ops /app/admin_ops
COPY src/ocr_platform/storage/models.py /app/src/ocr_platform/storage/models.py
ENV PYTHONPATH=/app:/app/src
USER ops

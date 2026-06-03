FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
# CPU inference: потоки задаются в docker-compose под лимит cpus контейнера
ENV OMP_NUM_THREADS=8
ENV MKL_NUM_THREADS=8
ENV TOKENIZERS_PARALLELISM=true
# Intel KMP: привязка потоков к ядрам для меньшей миграции
ENV KMP_AFFINITY=granularity=fine,compact,1,0
ENV KMP_BLOCKTIME=1

WORKDIR /app

# libgomp1 — OpenMP runtime; poppler — pdf2image; tesseract — OCR; libgl1, libglib2.0-0 — OpenCV / Docling dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential gcc g++ libgomp1 \
    poppler-utils \
    tesseract-ocr tesseract-ocr-rus \
    libgl1 libglib2.0-0 && \
    pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cpu && \
    rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md /app/
COPY src /app/src
COPY models /app/models
COPY observability /app/observability

RUN pip install --no-cache-dir -e ".[nlp]"

EXPOSE 8000

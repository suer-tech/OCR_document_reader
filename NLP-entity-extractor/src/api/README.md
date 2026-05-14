# API Module

Contains the FastAPI app, request schemas, and PDF ingestion service.

Implemented files:

- `app.py`: HTTP entrypoint
- `schemas.py`: request and response models
- `services/pdf.py`: Base64 decode and PDF text extraction
- `services/extractor.py`: API-to-inference orchestration

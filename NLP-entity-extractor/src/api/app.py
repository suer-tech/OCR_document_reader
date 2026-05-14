from __future__ import annotations

import os

from fastapi import FastAPI, HTTPException

from api.schemas import ExtractRequest, ExtractResponse, HealthResponse
from api.services.extractor import FioExtractionService
from api.services.pdf import PdfDecodeError, PdfTextExtractionError

MODEL_DIR = os.getenv("FIO_MODEL_DIR")
service = FioExtractionService(model_dir=MODEL_DIR)
app = FastAPI(title="NLP FIO Reader", version="0.2.0")


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    predictor = service.predictor
    return HealthResponse(
        status="ok",
        model_version=predictor.model_version,
        backend=predictor.backend,
    )


@app.post("/extract", response_model=ExtractResponse)
def extract_fio(request: ExtractRequest) -> ExtractResponse:
    try:
        return service.extract_from_base64(request.pdf_base64, document_id=request.document_id)
    except PdfDecodeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except PdfTextExtractionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

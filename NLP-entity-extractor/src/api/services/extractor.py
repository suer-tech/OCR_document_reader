from __future__ import annotations

from api.schemas import ExtractResponse, FioPayload
from inference.predict import FioPredictor

from .pdf import decode_pdf_base64, extract_text_from_pdf_bytes


class FioExtractionService:
    def __init__(self, model_dir: str | None = None) -> None:
        self.model_dir = model_dir
        self._predictor: FioPredictor | None = None

    @property
    def predictor(self) -> FioPredictor:
        if self._predictor is None:
            self._predictor = FioPredictor(model_dir=self.model_dir)
        return self._predictor

    def extract_from_base64(self, pdf_base64: str, document_id: str | None = None) -> ExtractResponse:
        pdf_bytes = decode_pdf_base64(pdf_base64)
        text = extract_text_from_pdf_bytes(pdf_bytes)
        prediction = self.predictor.predict(text)
        fields = prediction.fields
        return ExtractResponse(
            applicant_fio=FioPayload(
                last_name=fields.applicant_fio.last_name,
                first_name=fields.applicant_fio.first_name,
                patronymic=fields.applicant_fio.patronymic,
                normalized=fields.applicant_fio.normalized,
            ),
            judge_fio=FioPayload(
                last_name=fields.judge_fio.last_name,
                first_name=fields.judge_fio.first_name,
                patronymic=fields.judge_fio.patronymic,
                normalized=fields.judge_fio.normalized,
            ),
            court_name=fields.court_name,
            case_number=fields.case_number,
            inn=fields.inn,
            decision_date=fields.decision_date,
            procedure_end_date=fields.procedure_end_date,
            procedure_type=fields.procedure_type,
            confidence=prediction.confidence,
            source_text_span=prediction.source_text_span,
            source_text_preview=prediction.source_text_preview,
            model_version=prediction.model_version,
            document_id=document_id,
        )
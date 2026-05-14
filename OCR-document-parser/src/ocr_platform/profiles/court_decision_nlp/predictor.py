"""Предиктор полей судебного решения (NER + правила)."""

from __future__ import annotations

from .contracts import PredictionResult
from .loader import load_extractor


class FioPredictor:
    def __init__(self, model_dir: str | None = None, max_batch_size: int | None = None) -> None:
        self.extractor = load_extractor(model_dir, max_batch_size=max_batch_size)

    @property
    def model_version(self) -> str:
        return getattr(self.extractor, "model_version", "unknown")

    @property
    def backend(self) -> str:
        return self.extractor.__class__.__name__

    def predict(self, text: str) -> PredictionResult:
        return self.extractor.predict(text)

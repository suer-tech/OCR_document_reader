from __future__ import annotations

import json
from pathlib import Path

from .transformer import MODEL_ID, TransformerTokenClassifierExtractor


class ModelLoadError(RuntimeError):
    pass


def load_extractor(model_dir: str | Path | None = None) -> TransformerTokenClassifierExtractor:
    model_path = Path(model_dir)
    metadata_path = model_path / "metadata.json"
    if not metadata_path.exists():
        return TransformerTokenClassifierExtractor.bootstrap_local_model(model_path, model_id=MODEL_ID)

    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    backend = metadata.get("backend")
    if backend != "transformers-token-classification":
        raise ModelLoadError(f"Unsupported model backend: {backend}")
    return TransformerTokenClassifierExtractor.from_pretrained(model_path)

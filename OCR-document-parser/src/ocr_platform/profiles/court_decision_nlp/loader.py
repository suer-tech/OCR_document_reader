"""Загрузка NER-модели для извлечения ФИО."""

from __future__ import annotations

import json
from pathlib import Path

from .transformer import MODEL_ID, TransformerTokenClassifierExtractor

# Кэш: модель и токенизатор загружаются один раз при первом запросе, переиспользуются далее
_extractor_cache: dict[tuple[str, int | None], TransformerTokenClassifierExtractor] = {}


class ModelLoadError(RuntimeError):
    pass


def load_extractor(
    model_dir: str | Path | None = None,
    max_batch_size: int | None = None,
) -> TransformerTokenClassifierExtractor:
    model_path = Path(model_dir)
    cache_key = (str(model_path.resolve()), max_batch_size)

    if cache_key in _extractor_cache:
        return _extractor_cache[cache_key]

    metadata_path = model_path / "metadata.json"
    if not metadata_path.exists():
        extractor = TransformerTokenClassifierExtractor.bootstrap_local_model(model_path, model_id=MODEL_ID)
        _extractor_cache[cache_key] = extractor
        return extractor

    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    backend = metadata.get("backend")
    if backend != "transformers-token-classification":
        raise ModelLoadError(f"Unsupported model backend: {backend}")

    extractor = TransformerTokenClassifierExtractor.from_pretrained(model_path, max_batch_size=max_batch_size)
    _extractor_cache[cache_key] = extractor
    return extractor

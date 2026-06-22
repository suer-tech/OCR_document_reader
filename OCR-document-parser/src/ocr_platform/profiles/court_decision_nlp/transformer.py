"""NER-экстрактор на основе трансформера (token classification)."""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List

import torch
from transformers import AutoModelForSequenceClassification, AutoModelForTokenClassification, AutoTokenizer, pipeline

from ocr_platform.observability.logging import get_logger

logger = get_logger(__name__)

from .contracts import CourtDecisionFields, FioComponents, PredictionResult
from .postprocess import normalize_fio_components, normalize_whitespace
from .rules import (
    extract_case_number,
    extract_court_name,
    extract_decision_date,
    extract_early_report_deadline,
    extract_fio_with_ollama_llm,
    extract_inn,
    extract_motivating_part,
    extract_procedure_end_date_with_meta,
    extract_procedure_type,
    extract_resolutive_part,
)

MODEL_ID = "Gherman/bert-base-NER-Russian"
WINDOW_SIZE = 1000
WINDOW_OVERLAP = 200
ROLE_ANCHORS = {
    "applicant": [
        "рассмотрев заявление",
        "заявление",
        "в отношении",
        "признать",
        "должник",
        "гражданин",
        "обратился",
    ],
    "judge": [
        "в составе судьи",
        "судьи",
        "судья",
        "кому выдана",
    ],
}
ROLE_CONTEXTS = {
    "applicant": {
        "positive": {
            "заявлен": 4.0,
            "должник": 4.0,
            "граждан": 3.0,
            "признать": 3.0,
            "банкрот": 2.0,
            "обратил": 2.0,
        },
        "negative": {
            "судья": -4.0,
            "финансов": -2.0,
            "управля": -2.0,
            "представител": -1.5,
        },
    },
    "judge": {
        "positive": {
            "судья": 5.0,
            "в составе судьи": 5.0,
            "председательств": 3.0,
            "кому выдана": 4.0,
        },
        "negative": {
            "заявлен": -3.0,
            "должник": -3.0,
            "граждан": -2.0,
            "финансов": -1.5,
            "управля": -1.5,
        },
    },
}


@dataclass(slots=True)
class EntitySpan:
    label: str
    score: float
    word: str
    start: int
    end: int


@dataclass(slots=True)
class FioCandidate:
    fio: FioComponents
    start: int
    end: int
    score: float
    context_score: float

    @property
    def total_score(self) -> float:
        completeness = sum(1 for part in (self.fio.last_name, self.fio.first_name, self.fio.patronymic) if part)
        completeness_bonus = completeness * 2.0
        full_bonus = 2.0 if completeness == 3 else 0.0
        return self.score + self.context_score + completeness_bonus + full_bonus


def _configure_torch_threads() -> None:
    """Выставить число потоков PyTorch из OMP_NUM_THREADS (для CPU inference)."""
    try:
        n = int(os.environ.get("OMP_NUM_THREADS", "8"))
        if n > 0:
            torch.set_num_threads(n)
    except (ValueError, TypeError):
        pass


def _fio_str_to_components(fio_str: str) -> FioComponents:
    """Разобрать строку ФИО от Ollama в объект FioComponents.

    Поддерживает форматы:
    - «Фамилия Имя Отчество» → last_name, first_name, patronymic
    - «Фамилия И.О.» → last_name, first_name=«И.», patronymic=«О.»
    - «Фамилия И.» → last_name, first_name=«И.»

    Возвращает FioComponents с нормализованными значениями.
    """
    import re as _re
    fio_str = fio_str.strip()
    if not fio_str:
        return FioComponents()

    # Сплит по пробелу — первое слово всегда фамилия
    parts = fio_str.split()
    if not parts:
        return FioComponents()

    last_name = parts[0]
    first_name: str | None = None
    patronymic: str | None = None

    if len(parts) == 1:
        # Только фамилия
        pass
    elif len(parts) == 2:
        # «Фамилия Имя» или «Фамилия И.О.»
        second = parts[1]
        # Проверяем формат «И.О.» — два инициала через точку
        if _re.match(r"^[А-ЯЁA-Z]\.[А-ЯЁA-Z]\.$", second):
            first_name = second[0] + "."
            patronymic = second[2] + "."
        else:
            first_name = second
    elif len(parts) >= 3:
        # «Фамилия Имя Отчество»
        first_name = parts[1]
        patronymic = " ".join(parts[2:])

    return FioComponents(
        last_name=last_name or None,
        first_name=first_name or None,
        patronymic=patronymic or None,
    )


class TransformerTokenClassifierExtractor:
    def __init__(
        self,
        model_path: Path,
        model_id: str = MODEL_ID,
        max_batch_size: int = 12,
    ) -> None:
        _configure_torch_threads()

        self.model_path = model_path
        self.model_id = model_id
        self.max_batch_size = max_batch_size

        self.tokenizer = AutoTokenizer.from_pretrained(
            model_path,
            use_fast=True,
        )
        self.model = AutoModelForTokenClassification.from_pretrained(
            model_path,
            torch_dtype=torch.float32,
            low_cpu_mem_usage=True,
        )
        self.model.eval()

        try:
            self.model = self.model.to_bettertransformer()
        except Exception as exc:
            logger.debug("bettertransformer_skipped", reason=str(exc))

        self.pipeline = pipeline(
            "token-classification",
            model=self.model,
            tokenizer=self.tokenizer,
            aggregation_strategy="simple",
            device=-1,
        )
        self.model_version = model_path.name

        self.cls_pipeline = None
        cls_path = model_path.parent / "early-report-classifier"
        if cls_path.exists():
            cls_tokenizer = AutoTokenizer.from_pretrained(cls_path)
            cls_model = AutoModelForSequenceClassification.from_pretrained(cls_path)
            self.cls_pipeline = pipeline(
                "text-classification",
                model=cls_model,
                tokenizer=cls_tokenizer,
                device=-1,
            )

    def extract_entities(self, texts: str | List[str]) -> List[dict] | List[List[dict]]:
        """Извлечение сущностей. При списке текстов — батчевая обработка."""
        if isinstance(texts, str):
            texts = [texts]
        return self.pipeline(texts, batch_size=self.max_batch_size)

    @classmethod
    def bootstrap_local_model(cls, output_dir: str | Path, model_id: str = MODEL_ID) -> "TransformerTokenClassifierExtractor":
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        tokenizer = AutoTokenizer.from_pretrained(model_id)
        model = AutoModelForTokenClassification.from_pretrained(model_id)
        tokenizer.save_pretrained(output_path)
        model.save_pretrained(output_path)
        metadata = {
            "backend": "transformers-token-classification",
            "source_model": model_id,
            "labels": list(model.config.id2label.values()),
        }
        (output_path / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        return cls(output_path, model_id=model_id)

    @classmethod
    def from_pretrained(
        cls,
        model_path: str | Path,
        max_batch_size: int | None = None,
    ) -> "TransformerTokenClassifierExtractor":
        model_path = Path(model_path)
        metadata_path = model_path / "metadata.json"
        model_id = MODEL_ID
        if metadata_path.exists():
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            model_id = metadata.get("source_model", MODEL_ID)
        kwargs: dict = {}
        if max_batch_size is not None:
            kwargs["max_batch_size"] = max_batch_size
        return cls(model_path, model_id=model_id, **kwargs)

    def predict(self, text: str) -> PredictionResult:
        normalized_text = normalize_whitespace(text)
        entities = list(self._predict_entities(normalized_text))
        applicant = self._select_candidate(normalized_text, entities, role="applicant")
        judge = self._select_candidate(normalized_text, entities, role="judge")
        if applicant and judge and applicant.fio.normalized == judge.fio.normalized:
            judge = self._select_distinct_tail_candidate(normalized_text, entities, applicant.fio.normalized)

        applicant_fio = normalize_fio_components(applicant.fio) if applicant else FioComponents()
        judge_fio = normalize_fio_components(judge.fio) if judge else FioComponents()

        # ---------------------------------------------------------------
        # Ollama LLM: пытаемся улучшить/исправить ФИО судьи и должника.
        # При успехе — заменяем результат NER, при ошибке — NER остаётся.
        # ---------------------------------------------------------------
        try:
            ollama_result = extract_fio_with_ollama_llm(normalized_text)
            if ollama_result:
                if ollama_result.get("judge_fio"):
                    # Парсим строку «Фамилия Имя Отчество» или «Фамилия И.О.» в компоненты
                    judge_fio = _fio_str_to_components(ollama_result["judge_fio"])
                if ollama_result.get("debtor_fio"):
                    applicant_fio = _fio_str_to_components(ollama_result["debtor_fio"])
        except Exception:
            pass  # Ollama недоступна — продолжаем с NER

        best_candidate = applicant or judge
        confidence_basis = max(applicant.total_score if applicant else 0.0, judge.total_score if judge else 0.0)
        confidence = max(0.0, min(1.0, 1 / (1 + math.exp(-0.55 * (confidence_basis - 6.0))))) if confidence_basis else 0.0
        span = normalized_text[best_candidate.start:best_candidate.end] if best_candidate else None
        preview = normalized_text[max(0, best_candidate.start - 120): best_candidate.end + 120] if best_candidate else (normalized_text[:300] or None)

        procedure_end_date, is_calculated = extract_procedure_end_date_with_meta(normalized_text)

        # Determine early report deadline via sequence classifier if available
        early_report_deadline, early_report_deadline_source = None, None
        if self.cls_pipeline is not None and procedure_end_date:
            import re
            match = re.search(r"Р\s*Е\s*Ш\s*И\s*Л", normalized_text, re.IGNORECASE)
            search_text = normalized_text[match.start():] if match else normalized_text
            
            # Разделяем на предложения и фильтруем мусор
            sentences = [s.strip() for s in re.split(r'[.;]', search_text) if s.strip()]
            
            report_sentences = []
            for s in sentences:
                s_lower = s.lower()
                has_actor = "управляющ" in s_lower or "арбитражн" in s_lower or "финансов" in s_lower
                has_object = any(w in s_lower for w in ["отчет", "документ", "заблаговремен", "результат"])
                
                is_debtor_action = any(w in s_lower for w in [
                    "передать финансов", "передать арбитражн", "документацию должника", "документации должника",
                    "банковские карты", "печати", "штампы", "передать управляющ", "уведомить финансов", "выдать финансов"
                ])
                
                if has_actor and has_object and not is_debtor_action:
                    report_sentences.append(s)
                    
            context_text = " ".join(report_sentences) if report_sentences else search_text[:500]
            
            # Text-classification returns [{'label': 'REQUIRED', 'score': 0.99...}]
            pred = self.cls_pipeline(context_text, truncation=True, max_length=512)[0]
            if pred["label"] == "REQUIRED" and pred["score"] > 0.5:
                from .rules import _subtract_days_from_date, DEFAULT_ADVANCE_DAYS
                early_report_deadline = _subtract_days_from_date(procedure_end_date, DEFAULT_ADVANCE_DAYS)
                early_report_deadline_source = "Модель классификации early-report-classifier"
            else:
                early_report_deadline = None
                early_report_deadline_source = None

        return PredictionResult(
            fields=CourtDecisionFields(
                applicant_fio=applicant_fio,
                judge_fio=judge_fio,
                court_name=extract_court_name(normalized_text),
                case_number=extract_case_number(normalized_text),
                inn=extract_inn(normalized_text),
                decision_date=extract_decision_date(normalized_text),
                procedure_end_date=procedure_end_date,
                procedure_type=extract_procedure_type(normalized_text),
                early_report_deadline=early_report_deadline,
                early_report_deadline_source=early_report_deadline_source,
                motivating_part=extract_motivating_part(normalized_text),
                resolutive_part=extract_resolutive_part(normalized_text),
                procedure_end_date_is_calculated=is_calculated,
            ),
            confidence=confidence,
            source_text_span=span,
            source_text_preview=preview,
            model_version=self.model_version,
        )

    def _predict_entities(self, text: str) -> Iterable[EntitySpan]:
        chunks_with_starts = list(self._iter_chunks(text))
        if not chunks_with_starts:
            return
        chunk_texts = [c[0] for c in chunks_with_starts]
        chunk_starts = [c[1] for c in chunks_with_starts]
        batch_results = self.extract_entities(chunk_texts)
        for i, entities in enumerate(batch_results):
            chunk_start = chunk_starts[i]
            for item in entities:
                label = item.get("entity_group") or item.get("entity") or ""
                label = label.replace("B-", "").replace("I-", "")
                if label not in {"LAST_NAME", "FIRST_NAME", "MIDDLE_NAME"}:
                    continue
                yield EntitySpan(
                    label=label,
                    score=float(item["score"]),
                    word=str(item["word"]),
                    start=int(item["start"]) + chunk_start,
                    end=int(item["end"]) + chunk_start,
                )

    def _iter_chunks(self, text: str) -> Iterable[tuple[str, int]]:
        if len(text) <= WINDOW_SIZE:
            yield text, 0
            return
        start = 0
        while start < len(text):
            end = min(len(text), start + WINDOW_SIZE)
            if end < len(text):
                while end > start + 200 and end < len(text) and not text[end - 1].isspace():
                    end -= 1
            chunk = text[start:end]
            if chunk:
                yield chunk, start
            if end >= len(text):
                break
            start = max(end - WINDOW_OVERLAP, start + 1)

    def _select_candidate(self, text: str, entities: list[EntitySpan], role: str) -> FioCandidate | None:
        entities = self._deduplicate_entities(sorted(entities, key=lambda item: (item.start, item.end)))
        candidates: list[FioCandidate] = []
        for index, entity in enumerate(entities):
            if entity.label not in {"LAST_NAME", "FIRST_NAME"}:
                continue
            current = [entity]
            next_index = index + 1
            while next_index < len(entities):
                nxt = entities[next_index]
                if nxt.start - current[-1].end > 4:
                    break
                if nxt.label in {item.label for item in current}:
                    break
                current.append(nxt)
                next_index += 1
            candidate = self._candidate_from_entities(text, current, role=role)
            if candidate is not None:
                candidates.append(candidate)

        if not candidates:
            return None

        anchored = self._select_by_anchor(text, candidates, role)
        if anchored is not None:
            return anchored

        if role == "judge":
            candidates.sort(key=lambda item: (item.start, item.total_score), reverse=True)
        else:
            candidates.sort(key=lambda item: (item.total_score, -item.start), reverse=True)
        return candidates[0]

    def _select_by_anchor(self, text: str, candidates: list[FioCandidate], role: str) -> FioCandidate | None:
        lowered = text.lower()
        ranked: list[tuple[int, FioCandidate]] = []
        if role == "judge":
            anchor_positions = []
            for anchor in ROLE_ANCHORS[role]:
                position = lowered.rfind(anchor)
                if position >= 0:
                    anchor_positions.append(position)
            if not anchor_positions:
                return None
            anchor_index = max(anchor_positions)
            for candidate in candidates:
                distance = candidate.start - anchor_index
                if 0 <= distance <= 120:
                    ranked.append((distance, candidate))
        else:
            for candidate in candidates:
                best_distance: int | None = None
                for anchor in ROLE_ANCHORS[role]:
                    search_from = 0
                    while True:
                        index = lowered.find(anchor, search_from)
                        if index < 0:
                            break
                        distance = candidate.start - index
                        if 0 <= distance <= 160:
                            if best_distance is None or distance < best_distance:
                                best_distance = distance
                        search_from = index + 1
                if best_distance is not None:
                    ranked.append((best_distance, candidate))
        if not ranked:
            return None
        ranked.sort(key=lambda item: (item[0], -item[1].total_score))
        return ranked[0][1]

    def _select_distinct_tail_candidate(
        self,
        text: str,
        entities: list[EntitySpan],
        excluded_normalized: str | None,
    ) -> FioCandidate | None:
        entities = self._deduplicate_entities(sorted(entities, key=lambda item: (item.start, item.end)))
        candidates: list[FioCandidate] = []
        for index, entity in enumerate(entities):
            if entity.label not in {"LAST_NAME", "FIRST_NAME"}:
                continue
            current = [entity]
            next_index = index + 1
            while next_index < len(entities):
                nxt = entities[next_index]
                if nxt.start - current[-1].end > 4:
                    break
                if nxt.label in {item.label for item in current}:
                    break
                current.append(nxt)
                next_index += 1
            candidate = self._candidate_from_entities(text, current, role="judge")
            if candidate is None:
                continue
            if excluded_normalized and candidate.fio.normalized == excluded_normalized:
                continue
            candidates.append(candidate)
        if not candidates:
            return None
        candidates.sort(key=lambda item: item.start, reverse=True)
        return candidates[0]

    def _deduplicate_entities(self, entities: list[EntitySpan]) -> list[EntitySpan]:
        result: list[EntitySpan] = []
        seen: set[tuple[int, int, str]] = set()
        for entity in entities:
            key = (entity.start, entity.end, entity.label)
            if key not in seen:
                result.append(entity)
                seen.add(key)
        return result

    def _candidate_from_entities(self, text: str, entities: list[EntitySpan], role: str) -> FioCandidate | None:
        by_label = {entity.label: entity for entity in entities}
        if "LAST_NAME" not in by_label or "FIRST_NAME" not in by_label:
            return None

        start = min(entity.start for entity in entities)
        end = max(entity.end for entity in entities)
        window = text[max(0, start - 120): min(len(text), end + 120)].lower()
        local_before = text[max(0, start - 80):start].lower()
        local_after = text[end:min(len(text), end + 80)].lower()
        role_context = ROLE_CONTEXTS[role]
        context_score = sum(weight for marker, weight in role_context["positive"].items() if marker in window)
        context_score += sum(weight for marker, weight in role_context["negative"].items() if marker in window)
        if role == "applicant":
            if any(marker in local_before or marker in local_after for marker in ("заявлен", "должник", "граждан", "обратил", "рассмотрев заявление")):
                context_score += 4.0
            if any(marker in local_before for marker in ("судья", "в составе судьи")):
                context_score -= 5.0
        elif role == "judge":
            if any(marker in local_before or marker in local_after for marker in ("судья", "в составе судьи", "составе судьи", "кому выдана")):
                context_score += 5.0
            if any(marker in local_after for marker in ("заявлен", "должник", "граждан", "обратил")):
                context_score -= 4.0
        avg_score = sum(entity.score for entity in entities) / len(entities)
        return FioCandidate(
            fio=FioComponents(
                last_name=by_label.get("LAST_NAME").word if by_label.get("LAST_NAME") else None,
                first_name=by_label.get("FIRST_NAME").word if by_label.get("FIRST_NAME") else None,
                patronymic=by_label.get("MIDDLE_NAME").word if by_label.get("MIDDLE_NAME") else None,
            ),
            start=start,
            end=end,
            score=avg_score * 5.0,
            context_score=context_score,
        )

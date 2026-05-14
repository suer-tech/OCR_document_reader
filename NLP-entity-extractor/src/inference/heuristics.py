from __future__ import annotations

from .contracts import CourtDecisionFields, FioComponents, PredictionResult


class HeuristicFioExtractor:
    model_version = "heuristic-disabled"

    def predict(self, text: str) -> PredictionResult:
        return PredictionResult(
            fields=CourtDecisionFields(
                applicant_fio=FioComponents(),
                judge_fio=FioComponents(),
                court_name=None,
                case_number=None,
                inn=None,
                decision_date=None,
                procedure_end_date=None,
                procedure_type=None,
            ),
            confidence=0.0,
            source_text_span=None,
            source_text_preview=text[:300] or None,
            model_version=self.model_version,
        )
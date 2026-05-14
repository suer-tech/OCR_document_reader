"""Профиль court_decision_nlp: судебные решения, локальная NLP-экстракция (без LLM).

Вся логика из NLP-entity-extractor: NER (transformer), правила (rules), постобработка (postprocess).
"""

from .extractor import CourtDecisionNlpExtractor

__all__ = ["CourtDecisionNlpExtractor"]

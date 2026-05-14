"""Экстрактор для профиля unknown — без извлечения полей."""

from __future__ import annotations

from typing import Any, Dict


class UnknownExtractor:
    """Обработчик профиля unknown: возвращает пустой результат."""

    def extract(
        self,
        text: str,
        profile_config: dict[str, Any],
        profile_id: str,
        pipeline_run_id: str,
        document_id: str,
    ) -> Dict[str, dict]:
        return {}

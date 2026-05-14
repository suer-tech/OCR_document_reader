"""Базовый протокол для обработчиков профилей."""

from __future__ import annotations

from typing import Any, Dict, Protocol


class ProfileHandler(Protocol):
    """Протокол экстрактора полей для профиля.

    Профили возвращают «чистый результат» без обращения к БД, логам, API.
    Сохранение, метрики и MLflow остаются в services.
    """

    def extract(
        self,
        text: str,
        profile_config: dict[str, Any],
        profile_id: str,
        pipeline_run_id: str,
        document_id: str,
    ) -> Dict[str, dict]:
        """
        Извлечь структурированные поля из текста документа.

        Returns:
            Словарь {field_name: {"reasoning": str, "value": Any, "confidence": float}}
        """
        ...

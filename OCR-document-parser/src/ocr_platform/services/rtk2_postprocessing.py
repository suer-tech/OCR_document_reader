from __future__ import annotations

import re

_NO_HEARING = re.compile(r"без\s+проведения\s+судебного\s+заседания", re.IGNORECASE)
_SCHEDULED_HEARING = re.compile(
    r"(?:назначить.{0,80}судебное\s+заседание|"
    r"судебное\s+заседание.{0,80}(?:назначено|состоится)|"
    r"рассмотрение.{0,80}назначить\s+на)",
    re.IGNORECASE | re.DOTALL,
)
_SURNAME_AND_INITIALS = re.compile(
    r"^[А-ЯЁ][А-ЯЁа-яё-]+\s+(?:[А-ЯЁ]\.\s*){1,2}$", re.IGNORECASE
)


def guard_rtk2_results(text: str, fields: dict[str, dict]) -> dict[str, dict]:
    """Keep a missing hearing and a partial name from becoming factual fields."""
    hearing = fields.get("hearing_date")
    if (
        hearing
        and hearing.get("value") is not None
        and _NO_HEARING.search(text)
        and not _SCHEDULED_HEARING.search(text)
    ):
        hearing.update(
            value=None,
            confidence=1.0,
            reasoning=(
                "Документ прямо предусматривает рассмотрение без судебного заседания; "
                "дата заседания не назначена."
            ),
            source="rtk2_no_hearing_guard",
        )

    manager = fields.get("financial_manager_full_name")
    if manager and isinstance(manager.get("value"), str):
        partial_name = manager["value"].strip()
        if _SURNAME_AND_INITIALS.fullmatch(partial_name):
            manager.update(
                value=None,
                confidence=0.0,
                reasoning=(
                    f"В документе указаны только фамилия и инициалы ({partial_name}); "
                    "полное ФИО не установлено."
                ),
                source="rtk2_partial_name_guard",
            )
    return fields

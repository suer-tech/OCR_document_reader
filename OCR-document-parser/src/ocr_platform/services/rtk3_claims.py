from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from ocr_platform.api.schemas import ValidationIssue


CLAIMED_AMOUNT_NOT_DETERMINED = "claimed_amount_by_queue_not_determined"
CLAIMED_AMOUNT_MISMATCH = "claimed_amount_total_mismatch"
CLAIMED_AMOUNT_REVIEW_CODES = {CLAIMED_AMOUNT_NOT_DETERMINED, CLAIMED_AMOUNT_MISMATCH}


def _decimal_amount(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return amount if amount.is_finite() and amount >= 0 else None


def format_claimed_amount(value: Any) -> str | None:
    """Format the new field only; never round or derive a monetary amount."""
    if value is None:
        return None
    amount = _decimal_amount(value)
    if amount is not None:
        try:
            cents = amount.quantize(Decimal("0.01"))
            if cents == amount:
                return format(abs(cents), ".2f")
        except InvalidOperation:
            pass
    raise ValueError("claimed_amount must be a non-negative amount with at most two decimal places")


def claimed_amount_issues(fields: dict) -> list[ValidationIssue]:
    """Check RTK3 queue totals without changing any extracted values."""
    claims = fields.get("claims", {})
    claims = claims.get("value") if isinstance(claims, dict) else None
    # A missing optional amount in a single queue does not add a review reason.
    if not isinstance(claims, list) or len(claims) <= 1:
        return []

    total = fields.get("total_claimed_amount", {})
    total = _decimal_amount(total.get("value")) if isinstance(total, dict) else None
    claimed = [
        _decimal_amount(claim.get("claimed_amount")) if isinstance(claim, dict) else None
        for claim in claims
    ]
    if all(amount is not None for amount in claimed):
        if total is not None and sum(claimed, Decimal(0)) != total:
            return [ValidationIssue(
                code=CLAIMED_AMOUNT_MISMATCH,
                message="Сумма заявленных требований по очередям не совпадает с общей заявленной суммой.",
                field_name="claims",
                severity="error",
            )]
        return []

    included = [
        _decimal_amount(claim.get("total_amount")) if isinstance(claim, dict) else None
        for claim in claims
    ]
    # Fully included claims are an explicit exception. Keep missing values null:
    # the consumer may use included amounts, but OCR must not invent allocations.
    if total is not None and all(amount is not None for amount in included):
        if sum(included, Decimal(0)) == total:
            return []
    return [ValidationIssue(
        code=CLAIMED_AMOUNT_NOT_DETERMINED,
        message="Не удалось распределить заявленную сумму между очередями требований.",
        field_name="claims",
        severity="error",
    )]

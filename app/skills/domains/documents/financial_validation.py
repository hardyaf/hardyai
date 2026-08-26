from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation


_MASKED = re.compile(r"^\*{4}[A-Z0-9]{1,4}$")
_CURRENCY = {"USD", "EUR", "GBP", "CAD", "AUD"}


def validated_decimal(value: object) -> Decimal | None:
    candidate = str(value or "").strip().replace(",", "")
    if not candidate or "e" in candidate.casefold():
        return None
    try:
        parsed = Decimal(candidate)
    except InvalidOperation:
        return None
    if not parsed.is_finite() or parsed < 0 or parsed.as_tuple().exponent < -2:
        return None
    return parsed


def validated_date(value: object) -> str | None:
    candidate = str(value or "").strip()
    try:
        return date.fromisoformat(candidate).isoformat()
    except ValueError:
        return None


def validated_currency(value: object) -> str | None:
    candidate = str(value or "").strip().upper()
    return candidate if candidate in _CURRENCY else None


def validated_masked_identifier(value: object) -> str | None:
    candidate = str(value or "").strip().upper()
    return candidate if _MASKED.fullmatch(candidate) else None


def reconcile_totals(fields: dict[str, object], *, tolerance: Decimal = Decimal("0.01")) -> dict:
    subtotal = validated_decimal(fields.get("subtotal"))
    tax = validated_decimal(fields.get("tax_amount"))
    total = validated_decimal(fields.get("total_amount") or fields.get("amount_due"))
    if subtotal is None or total is None:
        return {"state": "insufficient_evidence", "difference": None, "passed": None}
    expected = subtotal + (tax or Decimal("0"))
    difference = total - expected
    return {
        "state": "reconciled" if abs(difference) <= tolerance else "mismatch",
        "difference": format(difference, "f"),
        "passed": abs(difference) <= tolerance,
    }


def prior_period_change(current: object, prior: object, *, threshold: Decimal = Decimal("0.25")) -> dict:
    current_value, prior_value = validated_decimal(current), validated_decimal(prior)
    if current_value is None or prior_value is None or prior_value == 0:
        return {"state": "insufficient_evidence", "ratio": None, "unusual": None}
    ratio = (current_value - prior_value) / prior_value
    return {
        "state": "compared",
        "ratio": format(ratio.quantize(Decimal("0.0001")), "f"),
        "unusual": abs(ratio) >= threshold,
    }

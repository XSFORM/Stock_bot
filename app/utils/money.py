from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any

_CENT = Decimal("0.01")


def _to_decimal(value: Any) -> Decimal:
    if value is None:
        return Decimal("0")
    return Decimal(str(value))


def round_money(value: Any) -> float:
    return float(_to_decimal(value).quantize(_CENT, rounding=ROUND_HALF_UP))


def calc_line_total(unit_price: Any, qty: Any) -> float:
    display_price = _to_decimal(unit_price).quantize(_CENT, rounding=ROUND_HALF_UP)
    line_total = (display_price * _to_decimal(qty)).quantize(_CENT, rounding=ROUND_HALF_UP)
    return float(line_total)


def _item_value(item: Any, key: str) -> Any:
    getter = getattr(item, "get", None)
    if callable(getter):
        return getter(key)
    try:
        return item[key]
    except Exception:
        return None


def calc_document_total(items: list[Any], unit_price_key: str) -> float:
    total = Decimal("0")
    for item in items:
        total += _to_decimal(calc_line_total(_item_value(item, unit_price_key), _item_value(item, "qty")))
    return float(total.quantize(_CENT, rounding=ROUND_HALF_UP))

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class CartItem:
    brand: str
    model: str
    qty: float
    unit_price: float
    price_mode: str


@dataclass
class Cart:
    client_name: str
    items: List[CartItem] = field(default_factory=list)


# ключ = telegram user id (у тебя будет один)
CARTS: Dict[int, Cart] = {}

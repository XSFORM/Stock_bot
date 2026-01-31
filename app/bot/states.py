from dataclasses import dataclass, field
from typing import Dict, List, Optional

@dataclass
class CartItem:
    brand: str
    model: str
    name: str
    qty: float
    price: float
    price_mode: str  # wh / wh10 / custom

@dataclass
class Cart:
    client_id: int
    client_name: str
    items: List[CartItem] = field(default_factory=list)

CARTS: Dict[int, Cart] = {}  # user_id -> cart

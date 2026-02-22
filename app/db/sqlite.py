from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any, Optional, Tuple

from app.constants import WAREHOUSES

BASE_DIR = Path(__file__).resolve().parents[1]  # .../app

DB_PATH = Path(os.getenv("DB_PATH", str(BASE_DIR / "db" / "stock.db")))
SCHEMA_PATH = BASE_DIR / "db" / "schema.sql"


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_db() -> None:
    conn = _connect()
    try:
        if SCHEMA_PATH.exists():
            conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
            
        _ensure_clients_columns(conn)
        for code, title in WAREHOUSES.items():
            conn.execute(
                "INSERT OR IGNORE INTO warehouses(code, title) VALUES(?, ?)",
                (code, title),
            )
        conn.commit()
        
        seed_brands_from_products()
        
    finally:
        conn.close()


# -------- clients --------

def add_client(name: str, phone: str = "", note: str = "") -> tuple[bool, str]:
    name = (name or "").strip()
    phone = (phone or "").strip()
    note = (note or "").strip()

    if not name:
        return False, "empty name"

    conn = _connect()
    try:
        try:
            conn.execute(
                "INSERT INTO clients(name, phone, note) VALUES(?, ?, ?)",
                (name, phone, note),
            )
            conn.commit()
            return True, ""
        except Exception:
            return False, "Client already exists"
    finally:
        conn.close()
        
def list_brands() -> list[str]:
    conn = _connect()
    try:
        rows = conn.execute("SELECT name FROM brands ORDER BY name").fetchall()
        return [r["name"] for r in rows]
    finally:
        conn.close()

def list_brand_model_prefixes(brand_name: str) -> list[str]:
    brand_name = (brand_name or "").strip()
    if not brand_name:
        return []
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT prefix FROM brand_model_prefixes WHERE brand_name=? ORDER BY prefix",
            (brand_name,),
        ).fetchall()
        return [r["prefix"] for r in rows]
    finally:
        conn.close()


def add_brand_model_prefix(brand_name: str, prefix: str) -> tuple[bool, str]:
    brand_name = (brand_name or "").strip()
    prefix = (prefix or "").strip()

    # normalize: user may type "tf-" -> store "tf"
    if prefix.endswith("-"):
        prefix = prefix[:-1].strip()

    if not brand_name:
        return False, "brand is empty"
    if not prefix:
        return False, "prefix is empty"

    conn = _connect()
    try:
        try:
            conn.execute(
                "INSERT INTO brand_model_prefixes(brand_name, prefix) VALUES (?, ?)",
                (brand_name, prefix),
            )
            conn.commit()
            return True, ""
        except Exception:
            return False, "prefix already exists"
    finally:
        conn.close()

def add_brand(name: str) -> tuple[bool, str]:
    name = (name or "").strip()
    if not name:
        return False, "Brand name is empty"

    conn = _connect()
    try:
        try:
            conn.execute("INSERT INTO brands(name) VALUES (?)", (name,))
            conn.commit()
            return True, ""
        except Exception:
            # likely UNIQUE constraint
            return False, "Brand already exists"
    finally:
        conn.close()


def seed_brands_from_products() -> None:
    """One-time helper: populate brands table from existing products.brand values."""
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT DISTINCT brand FROM products WHERE brand IS NOT NULL AND TRIM(brand) != ''"
        ).fetchall()
        for r in rows:
            b = (r["brand"] or "").strip()
            if not b:
                continue
            conn.execute("INSERT OR IGNORE INTO brands(name) VALUES (?)", (b,))
        conn.commit()
    finally:
        conn.close()        


def list_clients() -> list[dict[str, Any]]:
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT id, name, phone, note FROM clients ORDER BY name"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_client_by_name(name: str) -> Optional[dict[str, Any]]:
    conn = _connect()
    try:
        r = conn.execute(
            "SELECT id, name FROM clients WHERE lower(name)=lower(?)",
            (name.strip(),),
        ).fetchone()
        return dict(r) if r else None
    finally:
        conn.close()
        
def get_client(client_id: int) -> Optional[dict[str, Any]]:
    conn = _connect()
    try:
        r = conn.execute(
            "SELECT id, name, phone, note FROM clients WHERE id=?",
            (int(client_id),),
        ).fetchone()
        return dict(r) if r else None
    finally:
        conn.close()


def update_client(client_id: int, name: str, phone: str = "", note: str = "") -> tuple[bool, str]:
    name = (name or "").strip()
    phone = (phone or "").strip()
    note = (note or "").strip()
    if not name:
        return False, "empty name"

    conn = _connect()
    try:
        try:
            conn.execute(
                "UPDATE clients SET name=?, phone=?, note=? WHERE id=?",
                (name, phone, note, int(client_id)),
            )
            conn.commit()
            return True, ""
        except Exception:
            return False, "Client name already exists"
    finally:
        conn.close()        
        
def _ensure_clients_columns(conn: sqlite3.Connection) -> None:
    cols = conn.execute("PRAGMA table_info(clients);").fetchall()
    existing = {c["name"] for c in cols}

    if "phone" not in existing:
        conn.execute("ALTER TABLE clients ADD COLUMN phone TEXT NOT NULL DEFAULT '';")
    if "note" not in existing:
        conn.execute("ALTER TABLE clients ADD COLUMN note TEXT NOT NULL DEFAULT '';")        


# -------- products --------

def get_product_id_by_brand_model(brand: str, model: str) -> int | None:
    brand = (brand or "").strip()
    model = (model or "").strip()
    if not brand or not model:
        return None

    conn = _connect()
    try:
        row = conn.execute(
            "SELECT id FROM products WHERE brand=? AND model=?",
            (brand, model),
        ).fetchone()
        return int(row["id"]) if row else None
    finally:
        conn.close()


def add_or_get_product_id(
    brand: str,
    model: str,
    name: str,
    wh_price: float,
) -> tuple[int, bool]:
    """
    Returns: (product_id, created_new)
    If product exists -> updates name/wh_price (current) and returns existing id.
    """
    brand = (brand or "").strip()
    model = (model or "").strip()
    name = (name or "").strip()
    wh_price = float(wh_price)

    conn = _connect()
    try:
        row = conn.execute(
            "SELECT id FROM products WHERE brand=? AND model=?",
            (brand, model),
        ).fetchone()

        if row:
            pid = int(row["id"])
            # keep "current" values up-to-date
            conn.execute(
                "UPDATE products SET name=?, wh_price=? WHERE id=?",
                (name, wh_price, pid),
            )
            conn.commit()
            return pid, False

        cur = conn.execute(
            "INSERT INTO products(brand, model, name, wh_price) VALUES (?, ?, ?, ?)",
            (brand, model, name, wh_price),
        )
        conn.commit()
        return int(cur.lastrowid), True
    finally:
        conn.close()

def add_product(brand: str, model: str, name: str, wh_price: float) -> int:
    brand = (brand or "").strip()
    model = (model or "").strip()
    name = (name or "").strip()

    conn = _connect()
    try:
        cur = conn.execute(
            """
            INSERT INTO products(brand, model, name, wh_price)
            VALUES (?, ?, ?, ?)
            """,
            (brand, model, name, float(wh_price)),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()

def receive_stock_by_product_id(
    warehouse: str,
    product_id: int,
    qty: float,
    source: str | None = None,
) -> tuple[bool, str]:
    warehouse = (warehouse or "").strip().upper()

    try:
        qty = float(qty)
    except Exception:
        return False, "qty is not a number"
    if qty <= 0:
        return False, "qty must be > 0"

    conn = _connect()
    try:
        srow = conn.execute(
            "SELECT qty FROM stock WHERE warehouse_code=? AND product_id=?",
            (warehouse, int(product_id)),
        ).fetchone()

        if srow:
            conn.execute(
                "UPDATE stock SET qty = qty + ? WHERE warehouse_code=? AND product_id=?",
                (qty, warehouse, int(product_id)),
            )
        else:
            conn.execute(
                "INSERT INTO stock(warehouse_code, product_id, qty) VALUES (?, ?, ?)",
                (warehouse, int(product_id), qty),
            )

        if source:
            conn.execute(
                """
                INSERT INTO stock_ops(op_type, source, warehouse_code, product_id, qty)
                VALUES ('RECEIVE', ?, ?, ?, ?)
                """,
                (source, warehouse, int(product_id), qty),
            )

        conn.commit()
        return True, ""
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()

def list_products() -> list[dict[str, Any]]:
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT id, brand, model, name, wh_price FROM products ORDER BY brand, model"
        ).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            d["wh10_price"] = round(float(d["wh_price"]) * 1.10, 2)
            out.append(d)
        return out
    finally:
        conn.close()


def find_product(brand: str, model: str) -> Optional[dict[str, Any]]:
    conn = _connect()
    try:
        r = conn.execute(
            """
            SELECT id, brand, model, name, wh_price
            FROM products
            WHERE lower(brand)=lower(?) AND lower(model)=lower(?)
            """,
            (brand.strip(), model.strip()),
        ).fetchone()
        if not r:
            return None
        d = dict(r)
        d["wh10_price"] = round(float(d["wh_price"]) * 1.10, 2)
        return d
    finally:
        conn.close()


# -------- stock --------

def _get_stock_qty(conn: sqlite3.Connection, warehouse: str, product_id: int) -> float:
    r = conn.execute(
        "SELECT qty FROM stock WHERE warehouse_code=? AND product_id=?",
        (warehouse, product_id),
    ).fetchone()
    return float(r["qty"]) if r else 0.0


def _set_stock_qty(conn: sqlite3.Connection, warehouse: str, product_id: int, qty: float) -> None:
    conn.execute(
        """
        INSERT INTO stock(warehouse_code, product_id, qty)
        VALUES(?, ?, ?)
        ON CONFLICT(warehouse_code, product_id) DO UPDATE SET qty=excluded.qty
        """,
        (warehouse, product_id, float(qty)),
    )


def receive_stock(
    warehouse: str,
    brand: str,
    model: str,
    qty: float,
    source: str | None = None,
) -> tuple[bool, str]:
    warehouse = (warehouse or "").strip().upper()
    brand = (brand or "").strip()
    model = (model or "").strip()

    try:
        qty = float(qty)
    except Exception:
        return False, "qty is not a number"

    if qty <= 0:
        return False, "qty must be > 0"

    conn = _connect()
    try:
        # 1) find product
        row = conn.execute(
            "SELECT id FROM products WHERE brand=? AND model=?",
            (brand, model),
        ).fetchone()
        if not row:
            return False, f"product not found: {brand} {model}"

        product_id = int(row["id"])

        # 2) upsert stock qty for warehouse_code+product_id
        srow = conn.execute(
            "SELECT qty FROM stock WHERE warehouse_code=? AND product_id=?",
            (warehouse, product_id),
        ).fetchone()

        if srow:
            conn.execute(
                "UPDATE stock SET qty = qty + ? WHERE warehouse_code=? AND product_id=?",
                (qty, warehouse, product_id),
            )
        else:
            conn.execute(
                "INSERT INTO stock(warehouse_code, product_id, qty) VALUES (?, ?, ?)",
                (warehouse, product_id, qty),
            )

        # 3) journal (optional; requires stock_ops table)
        if source:
            conn.execute(
                """
                INSERT INTO stock_ops(op_type, source, warehouse_code, product_id, qty)
                VALUES ('RECEIVE', ?, ?, ?, ?)
                """,
                (source, warehouse, product_id, qty),
            )

        conn.commit()
        return True, ""
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()


def move_stock(src: str, dst: str, brand: str, model: str, qty: float) -> Tuple[bool, str]:
    src = src.strip().upper()
    dst = dst.strip().upper()
    qty = float(qty)

    if qty <= 0:
        return False, "QTY должно быть > 0"
    if src == dst:
        return False, "FROM и TO одинаковые"
    if src not in WAREHOUSES or dst not in WAREHOUSES:
        return False, "Неизвестный склад"

    product = find_product(brand, model)
    if not product:
        return False, "Товар не найден. Добавь через /product_add"

    conn = _connect()
    try:
        pid = int(product["id"])
        src_qty = _get_stock_qty(conn, src, pid)
        if src_qty < qty:
            return False, f"На складе {src} недостаточно: есть {src_qty}, нужно {qty}"

        _set_stock_qty(conn, src, pid, src_qty - qty)
        dst_qty = _get_stock_qty(conn, dst, pid)
        _set_stock_qty(conn, dst, pid, dst_qty + qty)

        conn.commit()
        return True, ""
    finally:
        conn.close()


def move_all(src: str, dst: str = "SHOP") -> tuple[bool, str, int]:
    init_db()
    src = src.strip().upper()
    dst = dst.strip().upper()

    if src not in WAREHOUSES or dst not in WAREHOUSES:
        return False, "Неизвестный склад", 0
    if src == dst:
        return False, "FROM и TO одинаковые", 0

    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT product_id, qty FROM stock WHERE warehouse_code=? AND qty > 0",
            (src,),
        ).fetchall()

        moved = 0
        for r in rows:
            pid = int(r["product_id"])
            qty = float(r["qty"])
            if qty <= 0:
                continue

            dst_qty = _get_stock_qty(conn, dst, pid)
            _set_stock_qty(conn, dst, pid, dst_qty + qty)
            _set_stock_qty(conn, src, pid, 0.0)
            moved += 1

        conn.commit()
        return True, "", moved
    finally:
        conn.close()


def move_all_auto_shop(src: str) -> tuple[bool, str, int, str]:
    """
    Автоматический перенос в нужный магазин:
    CHINA_DEPOT -> SHOP_CHINA
    DEALER_DEPOT -> SHOP_DEALER
    иначе -> SHOP (legacy)
    Возвращает (ok, err, moved, dst)
    """
    src_u = src.strip().upper()
    if src_u == "CHINA_DEPOT":
        dst = "SHOP_CHINA"
    elif src_u == "DEALER_DEPOT":
        dst = "SHOP_DEALER"
    else:
        dst = "SHOP"
    ok, err, moved = move_all(src_u, dst)
    return ok, err, moved, dst


def get_stock(warehouse: Optional[str] = None, q: Optional[str] = None) -> list[dict[str, Any]]:
    wh = warehouse.strip().upper() if warehouse else ""
    term = (q or "").strip().lower()

    conn = _connect()
    try:
        params: list[Any] = []
        where_parts: list[str] = []

        if wh:
            where_parts.append("w.code=?")
            params.append(wh)

        if term:
            like = f"%{term}%"
            where_parts.append(
                "(lower(p.model) LIKE ? OR lower(p.brand) LIKE ? OR lower(p.name) LIKE ?)"
            )
            params.extend([like, like, like])

        where_sql = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""

        rows = conn.execute(
            f"""
            SELECT
              w.code as warehouse,
              p.brand,
              p.model,
              p.name,
              s.qty,
              p.wh_price as wh_price,
              ROUND(p.wh_price * 1.10, 2) as sale_price
            FROM stock s
            JOIN products p ON p.id=s.product_id
            JOIN warehouses w ON w.code=s.warehouse_code
            {where_sql}
            ORDER BY w.code, p.brand, p.model
            """,
            tuple(params),
        ).fetchall()

        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_stock_text(warehouse: Optional[str] = None) -> str:
    rows = get_stock(warehouse)
    if not rows:
        return "Остатков нет."

    lines = ["<b>Остатки:</b>"]
    for r in rows:
        lines.append(f"{r['warehouse']}: {r['brand']} {r['model']} — {float(r['qty'])}")
    return "\n".join(lines)


def search_stock(warehouse: str, q: str, limit: int = 30) -> list[dict[str, Any]]:
    """Search stock for a specific warehouse by brand/model/name query.

    Returns list of dicts: product_id, brand, model, name, qty_available, wh_price, wh10_price.
    """
    wh = warehouse.strip().upper()
    term = (q or "").strip().lower()
    like = f"%{term}%"

    conn = _connect()
    try:
        rows = conn.execute(
            """
            SELECT
              p.id as product_id,
              p.brand,
              p.model,
              p.name,
              s.qty as qty_available,
              p.wh_price,
              ROUND(p.wh_price * 1.10, 2) as wh10_price
            FROM stock s
            JOIN products p ON p.id = s.product_id
            JOIN warehouses w ON w.code = s.warehouse_code
            WHERE w.code = ?
              AND s.qty > 0
              AND (lower(p.brand) LIKE ? OR lower(p.model) LIKE ? OR lower(p.name) LIKE ?)
            ORDER BY p.brand, p.model
            LIMIT ?
            """,
            (wh, like, like, like, int(limit)),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_cart_items_list(cart_id: int) -> tuple[bool, list[dict[str, Any]]]:
    """Return cart items as a list of dicts (for template rendering).

    Each dict has: id, brand, model, name, qty, price_mode, unit_price, total.
    """
    init_db()
    conn = _connect()
    try:
        cart = conn.execute(
            "SELECT id, status FROM carts WHERE id=?", (int(cart_id),)
        ).fetchone()
        if not cart:
            return False, []

        rows = conn.execute(
            """
            SELECT i.id, p.brand, p.model, p.name, i.qty, i.price_mode, i.unit_price, i.total
            FROM cart_items i
            JOIN products p ON p.id = i.product_id
            WHERE i.cart_id = ?
            ORDER BY i.id
            """,
            (int(cart_id),),
        ).fetchall()
        return True, [dict(r) for r in rows]
    finally:
        conn.close()


def cart_set_client(cart_id: int, client_id: int) -> tuple[bool, str]:
    """Change the client on an OPEN cart without affecting cart items."""
    init_db()
    conn = _connect()
    try:
        r = conn.execute(
            "SELECT id, status FROM carts WHERE id=?", (int(cart_id),)
        ).fetchone()
        if not r:
            return False, "Cart not found"
        if r["status"] != "OPEN":
            return False, "Cart is not open"
        c = conn.execute(
            "SELECT id FROM clients WHERE id=?", (int(client_id),)
        ).fetchone()
        if not c:
            return False, "Client not found"
        conn.execute(
            "UPDATE carts SET client_id=? WHERE id=?", (int(client_id), int(cart_id))
        )
        conn.commit()
        return True, ""
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()


def cancel_cart(cart_id: int) -> tuple[bool, str]:
    """Delete an OPEN cart and all its items from DB (physical delete, no stock changes)."""
    init_db()
    conn = _connect()
    try:
        r = conn.execute(
            "SELECT id, status FROM carts WHERE id=?", (int(cart_id),)
        ).fetchone()
        if not r:
            return False, "Cart not found"
        if r["status"] != "OPEN":
            return False, "Cart is not open"
        conn.execute("DELETE FROM cart_items WHERE cart_id=?", (int(cart_id),))
        conn.execute("DELETE FROM carts WHERE id=?", (int(cart_id),))
        conn.commit()
        return True, ""
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()


def update_cart_item(item_id: int, qty: float, unit_price: float) -> tuple[bool, str]:
    """Update qty and unit_price for a cart item; sets price_mode to 'custom'."""
    init_db()
    qty = float(qty)
    unit_price = float(unit_price)
    if qty <= 0:
        return False, "qty must be > 0"
    if unit_price < 0:
        return False, "unit_price must be >= 0"
    total = round(qty * unit_price, 2)
    conn = _connect()
    try:
        r = conn.execute(
            "SELECT i.id, c.status FROM cart_items i JOIN carts c ON c.id=i.cart_id WHERE i.id=?",
            (int(item_id),),
        ).fetchone()
        if not r:
            return False, "Item not found"
        if r["status"] != "OPEN":
            return False, "Cart is not open"
        conn.execute(
            "UPDATE cart_items SET qty=?, unit_price=?, total=?, price_mode='custom' WHERE id=?",
            (qty, unit_price, total, int(item_id)),
        )
        conn.commit()
        return True, ""
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()


def delete_cart_item(item_id: int) -> tuple[bool, str]:
    """Remove a single item from an OPEN cart (no stock changes)."""
    init_db()
    conn = _connect()
    try:
        r = conn.execute(
            "SELECT i.id, c.status FROM cart_items i JOIN carts c ON c.id=i.cart_id WHERE i.id=?",
            (int(item_id),),
        ).fetchone()
        if not r:
            return False, "Item not found"
        if r["status"] != "OPEN":
            return False, "Cart is not open"
        conn.execute("DELETE FROM cart_items WHERE id=?", (int(item_id),))
        conn.commit()
        return True, ""
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()


def get_invoice_by_number(number: int) -> Optional[dict[str, Any]]:
    """Return invoice dict by invoice number."""
    init_db()
    conn = _connect()
    try:
        r = conn.execute(
            """
            SELECT inv.id, inv.number, inv.created_at, inv.currency, inv.total,
                   cl.name as client
            FROM invoices inv
            JOIN carts c ON c.id = inv.cart_id
            JOIN clients cl ON cl.id = c.client_id
            WHERE inv.number = ?
            """,
            (int(number),),
        ).fetchone()
        return dict(r) if r else None
    finally:
        conn.close()


def get_invoice_items_by_number(number: int) -> list[dict[str, Any]]:
    """Return line items for an invoice by invoice number."""
    init_db()
    conn = _connect()
    try:
        rows = conn.execute(
            """
            SELECT p.brand, p.model, p.name, ci.qty, ci.price_mode, ci.unit_price, ci.total
            FROM invoices inv
            JOIN carts c ON c.id = inv.cart_id
            JOIN cart_items ci ON ci.cart_id = c.id
            JOIN products p ON p.id = ci.product_id
            WHERE inv.number = ?
            ORDER BY ci.id
            """,
            (int(number),),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# -------- cart / invoice --------

def cart_add_by_cart_id(
    cart_id: int,
    brand: str,
    model: str,
    qty: float,
    price_mode: str,
    custom_price: Optional[float] = None,
) -> Tuple[bool, str]:
    init_db()
    qty = float(qty)
    if qty <= 0:
        return False, "QTY должно быть > 0"

    price_mode = price_mode.strip().lower()
    if price_mode not in ("wh", "wh10", "custom"):
        return False, "price_mode должен быть: wh / wh10 / custom"

    product = find_product(brand, model)
    if not product:
        return False, "Товар не найден."

    wh_price = float(product["wh_price"])
    if price_mode == "wh":
        unit = round(wh_price, 2)
    elif price_mode == "wh10":
        unit = round(wh_price * 1.10, 2)
    else:
        if custom_price is None:
            return False, "Для custom нужно указать custom_price"
        unit = round(float(custom_price), 2)

    total = round(unit * qty, 2)

    conn = _connect()
    try:
        # ensure cart exists and open
        r = conn.execute("SELECT status FROM carts WHERE id=?", (int(cart_id),)).fetchone()
        if not r:
            return False, "Cart not found"
        if r["status"] != "OPEN":
            return False, "Cart is closed"

        # validate against 1416_SHOP stock
        available = _get_stock_qty(conn, "1416_SHOP", int(product["id"]))
        if qty > available:
            return False, f"Недостаточно на складе 1416_SHOP: доступно {available:.0f}, запрошено {qty:.0f}"

        conn.execute(
            """
            INSERT INTO cart_items(cart_id, product_id, qty, price_mode, unit_price, total)
            VALUES(?, ?, ?, ?, ?, ?)
            """,
            (int(cart_id), int(product["id"]), qty, price_mode, unit, total),
        )
        conn.commit()
        return True, ""
    finally:
        conn.close()


def cart_show_by_cart_id(cart_id: int) -> Tuple[bool, str]:
    init_db()
    conn = _connect()
    try:
        cart = conn.execute(
            """
            SELECT c.id, c.status, cl.name as client_name
            FROM carts c
            JOIN clients cl ON cl.id=c.client_id
            WHERE c.id=?
            """,
            (int(cart_id),),
        ).fetchone()
        if not cart:
            return False, "Корзина не найдена."

        rows = conn.execute(
            """
            SELECT p.brand, p.model, p.name, i.qty, i.price_mode, i.unit_price, i.total
            FROM cart_items i
            JOIN products p ON p.id=i.product_id
            WHERE i.cart_id=?
            ORDER BY i.id
            """,
            (int(cart_id),),
        ).fetchall()

        if not rows:
            return True, "Корзина пустая."

        lines = [f"<b>Корзина: {cart['client_name']}</b>"]
        sum_total = 0.0
        for r in rows:
            d = dict(r)
            sum_total += float(d["total"])
            lines.append(
                f"• {d['brand']} {d['model']} — {d['qty']} шт × {float(d['unit_price']):.2f}$ ({d['price_mode']}) = {float(d['total']):.2f}$"
            )
        lines.append(f"\n<b>Итого:</b> {sum_total:.2f}$")
        return True, "\n".join(lines)
    finally:
        conn.close()


def cart_finish_by_cart_id_shop1416(cart_id: int):
    """
    Finish cart by cart_id, selling strictly from 1416_SHOP.
    Internally maps cart->client_name and uses existing logic.
    """
    conn = _connect()
    try:
        r = conn.execute(
            """
            SELECT cl.name as client_name
            FROM carts c
            JOIN clients cl ON cl.id=c.client_id
            WHERE c.id=?
            """,
            (int(cart_id),),
        ).fetchone()
        if not r:
            return False, "Корзина не найдена.", {}, []
        client_name = r["client_name"]
    finally:
        conn.close()

    # IMPORTANT: sells only from 1416_SHOP
    return cart_finish_from_shop(client_name, "1416_SHOP")

def _get_or_create_client_id(conn: sqlite3.Connection, client_name: str) -> int:
    client = conn.execute(
        "SELECT id FROM clients WHERE lower(name)=lower(?)",
        (client_name.strip(),),
    ).fetchone()
    if client:
        return int(client["id"])
    conn.execute("INSERT INTO clients(name) VALUES(?)", (client_name.strip(),))
    return int(conn.execute("SELECT last_insert_rowid() as id").fetchone()["id"])
    

def get_open_cart() -> Optional[dict]:
    """Return the current OPEN cart (cart_id, client_id, client_name, created_at) or None."""
    init_db()
    conn = _connect()
    try:
        r = conn.execute(
            """
            SELECT c.id as cart_id, c.client_id, cl.name as client_name, c.created_at
            FROM carts c
            JOIN clients cl ON cl.id = c.client_id
            WHERE c.status = 'OPEN'
            ORDER BY c.id DESC
            LIMIT 1
            """,
        ).fetchone()
        return dict(r) if r else None
    finally:
        conn.close()


def cart_start_by_id(client_id: int) -> Tuple[bool, str, Optional[int]]:
    """Start a new OPEN cart for client_id.

    Returns (True, "", cart_id) on success.
    Returns (False, error_msg, None) if an OPEN cart already exists.
    """
    init_db()
    conn = _connect()
    try:
        existing = conn.execute(
            "SELECT id FROM carts WHERE status='OPEN' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if existing:
            return False, "Уже есть активный инвойс. Завершите его перед началом нового.", None
        cid = int(client_id)
        conn.execute("INSERT INTO carts(client_id, status) VALUES(?, 'OPEN')", (cid,))
        cart_id = int(conn.execute("SELECT last_insert_rowid() as id").fetchone()["id"])
        conn.commit()
        return True, "", cart_id
    finally:
        conn.close()


def _get_open_cart_id_by_client_id(conn: sqlite3.Connection, client_id: int) -> Optional[int]:
    r = conn.execute(
        """
        SELECT id
        FROM carts
        WHERE client_id=? AND status='OPEN'
        ORDER BY id DESC
        LIMIT 1
        """,
        (int(client_id),),
    ).fetchone()
    return int(r["id"]) if r else None


def cart_add_by_id(
    client_id: int,
    brand: str,
    model: str,
    qty: float,
    price_mode: str,
    custom_price: Optional[float] = None,
) -> Tuple[bool, str]:
    init_db()
    qty = float(qty)
    if qty <= 0:
        return False, "QTY должно быть > 0"

    price_mode = price_mode.strip().lower()
    if price_mode not in ("wh", "wh10", "custom"):
        return False, "price_mode должен быть: wh / wh10 / custom"

    product = find_product(brand, model)
    if not product:
        return False, "Товар не найден."

    conn = _connect()
    try:
        cart_id = _get_open_cart_id_by_client_id(conn, int(client_id))
        if not cart_id:
            cart_id = cart_start_by_id(int(client_id))

        wh_price = float(product["wh_price"])
        if price_mode == "wh":
            unit = round(wh_price, 2)
        elif price_mode == "wh10":
            unit = round(wh_price * 1.10, 2)
        else:
            if custom_price is None:
                return False, "Для custom нужно указать custom_price"
            unit = round(float(custom_price), 2)

        total = round(unit * qty, 2)

        conn.execute(
            """
            INSERT INTO cart_items(cart_id, product_id, qty, price_mode, unit_price, total)
            VALUES(?, ?, ?, ?, ?, ?)
            """,
            (cart_id, int(product["id"]), qty, price_mode, unit, total),
        )
        conn.commit()
        return True, ""
    finally:
        conn.close()


def cart_show_by_id(client_id: int) -> Tuple[bool, str]:
    init_db()
    conn = _connect()
    try:
        cart_id = _get_open_cart_id_by_client_id(conn, int(client_id))
        if not cart_id:
            return False, "Корзина не начата."

        rows = conn.execute(
            """
            SELECT p.brand, p.model, p.name, i.qty, i.price_mode, i.unit_price, i.total
            FROM cart_items i
            JOIN products p ON p.id=i.product_id
            WHERE i.cart_id=?
            ORDER BY i.id
            """,
            (cart_id,),
        ).fetchall()

        if not rows:
            return True, "Корзина пустая."

        cl = conn.execute("SELECT name FROM clients WHERE id=?", (int(client_id),)).fetchone()
        client_name = cl["name"] if cl else f"#{client_id}"

        lines = [f"<b>Корзина: {client_name}</b>"]
        sum_total = 0.0
        for r in rows:
            d = dict(r)
            sum_total += float(d["total"])
            lines.append(
                f"• {d['brand']} {d['model']} — {d['qty']} шт × {float(d['unit_price']):.2f}$ ({d['price_mode']}) = {float(d['total']):.2f}$"
            )
        lines.append(f"\n<b>Итого:</b> {sum_total:.2f}$")
        return True, "\n".join(lines)
    finally:
        conn.close()


def cart_finish_by_id(client_id: int):
    # MVP: translate id -> name and reuse existing cart_finish(name)
    conn = _connect()
    try:
        r = conn.execute("SELECT name FROM clients WHERE id=?", (int(client_id),)).fetchone()
        if not r:
            return False, "Клиент не найден", {}, []
        name = r["name"]
    finally:
        conn.close()
    return cart_finish(name)    


def cart_start(client_name: str) -> int:
    init_db()
    conn = _connect()
    try:
        cid = _get_or_create_client_id(conn, client_name)
        conn.execute("UPDATE carts SET status='CLOSED' WHERE client_id=? AND status='OPEN'", (cid,))
        conn.execute("INSERT INTO carts(client_id, status) VALUES(?, 'OPEN')", (cid,))
        cart_id = int(conn.execute("SELECT last_insert_rowid() as id").fetchone()["id"])
        conn.commit()
        return cart_id
    finally:
        conn.close()


def _get_open_cart_id(conn: sqlite3.Connection, client_name: str) -> Optional[int]:
    r = conn.execute(
        """
        SELECT c.id
        FROM carts c
        JOIN clients cl ON cl.id=c.client_id
        WHERE lower(cl.name)=lower(?) AND c.status='OPEN'
        ORDER BY c.id DESC
        LIMIT 1
        """,
        (client_name.strip(),),
    ).fetchone()
    return int(r["id"]) if r else None


def cart_add(
    client_name: str,
    brand: str,
    model: str,
    qty: float,
    price_mode: str,
    custom_price: Optional[float] = None,
) -> Tuple[bool, str]:
    init_db()
    qty = float(qty)
    if qty <= 0:
        return False, "QTY должно быть > 0"

    price_mode = price_mode.strip().lower()
    if price_mode not in ("wh", "wh10", "custom"):
        return False, "price_mode должен быть: wh / wh10 / custom"

    product = find_product(brand, model)
    if not product:
        return False, "Товар не найден. Добавь через /product_add"

    conn = _connect()
    try:
        cart_id = _get_open_cart_id(conn, client_name)
        if not cart_id:
            cart_id = cart_start(client_name)

        wh_price = float(product["wh_price"])
        if price_mode == "wh":
            unit = round(wh_price, 2)
        elif price_mode == "wh10":
            unit = round(wh_price * 1.10, 2)
        else:
            if custom_price is None:
                return False, "Для custom нужно указать custom_price"
            unit = round(float(custom_price), 2)

        total = round(unit * qty, 2)

        conn.execute(
            """
            INSERT INTO cart_items(cart_id, product_id, qty, price_mode, unit_price, total)
            VALUES(?, ?, ?, ?, ?, ?)
            """,
            (cart_id, int(product["id"]), qty, price_mode, unit, total),
        )
        conn.commit()
        return True, ""
    finally:
        conn.close()


def cart_show(client_name: str) -> Tuple[bool, str]:
    init_db()
    conn = _connect()
    try:
        cart_id = _get_open_cart_id(conn, client_name)
        if not cart_id:
            return False, "Корзина не начата. Используй /cart_start CLIENT"

        rows = conn.execute(
            """
            SELECT p.brand, p.model, p.name, i.qty, i.price_mode, i.unit_price, i.total
            FROM cart_items i
            JOIN products p ON p.id=i.product_id
            WHERE i.cart_id=?
            ORDER BY i.id
            """,
            (cart_id,),
        ).fetchall()

        if not rows:
            return True, "Корзина пустая."

        lines = [f"<b>Корзина: {client_name}</b>"]
        sum_total = 0.0
        for r in rows:
            d = dict(r)
            sum_total += float(d["total"])
            lines.append(
                f"• {d['brand']} {d['model']} — {d['qty']} шт × {float(d['unit_price']):.2f}$ ({d['price_mode']}) = {float(d['total']):.2f}$"
            )
        lines.append(f"\n<b>Итого:</b> {sum_total:.2f}$")
        return True, "\n".join(lines)
    finally:
        conn.close()


def cart_remove(client_name: str, brand: str, model: str) -> Tuple[bool, str]:
    init_db()
    conn = _connect()
    try:
        cart_id = _get_open_cart_id(conn, client_name)
        if not cart_id:
            return False, "Корзина не начата."

        r = conn.execute(
            """
            SELECT i.id
            FROM cart_items i
            JOIN products p ON p.id=i.product_id
            WHERE i.cart_id=? AND p.brand=? AND p.model=?
            ORDER BY i.id DESC
            LIMIT 1
            """,
            (cart_id, brand.strip().lower(), model.strip().lower()),
        ).fetchone()

        if not r:
            return False, "В корзине такого товара нет."

        conn.execute("DELETE FROM cart_items WHERE id=?", (int(r["id"]),))
        conn.commit()
        return True, ""
    finally:
        conn.close()


def cart_finish_from_shop(client_name: str, shop_code: str) -> Tuple[bool, str, dict[str, Any], list[dict[str, Any]]]:
    """
    Списать из указанного магазина (SHOP_CHINA / SHOP_DEALER / SHOP), закрыть корзину, создать invoice.
    return (ok, err, invoice_dict, items)
    """
    init_db()
    shop = shop_code.strip().upper()
    if shop not in WAREHOUSES:
        return False, "Неизвестный склад магазина", {}, []

    conn = _connect()
    try:
        cart_id = _get_open_cart_id(conn, client_name)
        if not cart_id:
            return False, "Корзина не начата.", {}, []

        items = conn.execute(
            """
            SELECT p.id as product_id, p.brand, p.model, p.name, i.qty, i.unit_price, i.total
            FROM cart_items i
            JOIN products p ON p.id=i.product_id
            WHERE i.cart_id=?
            ORDER BY i.id
            """,
            (cart_id,),
        ).fetchall()

        if not items:
            return False, "Корзина пустая.", {}, []

        # check stock in shop and subtract
        for r in items:
            pid = int(r["product_id"])
            need = float(r["qty"])
            have = _get_stock_qty(conn, shop, pid)
            if have < need:
                return False, f"На складе {shop} не хватает {r['brand']} {r['model']}: есть {have}, нужно {need}", {}, []

        for r in items:
            pid = int(r["product_id"])
            need = float(r["qty"])
            have = _get_stock_qty(conn, shop, pid)
            _set_stock_qty(conn, shop, pid, have - need)

        total_sum = round(sum(float(r["total"]) for r in items), 2)

        last = conn.execute("SELECT COALESCE(MAX(number), 0) as n FROM invoices").fetchone()
        num = int(last["n"]) + 1

        conn.execute(
            "INSERT INTO invoices(cart_id, number, total, currency) VALUES(?, ?, ?, 'USD')",
            (cart_id, num, total_sum),
        )

        conn.execute("UPDATE carts SET status='CLOSED' WHERE id=?", (cart_id,))
        conn.commit()

        invoice = {
            "number": num,
            "client": client_name,
            "date": conn.execute("SELECT created_at FROM invoices WHERE cart_id=?", (cart_id,)).fetchone()["created_at"],
            "total": total_sum,
            "currency": "USD",
            "shop": shop,
        }
        return True, "", invoice, [dict(x) for x in items]
    finally:
        conn.close()
def cart_finish(client_name: str):
    """
    Legacy wrapper: списание из общего магазина SHOP.
    Нужен для совместимости (web/telegram) когда используем legacy SHOP.
    """
    return cart_finish_from_shop(client_name, "SHOP")   

def cart_finish_shop1416(client_name: str):
    return cart_finish_from_shop(client_name, "1416_SHOP")    
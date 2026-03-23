from __future__ import annotations

import hashlib
import os
import secrets
import sqlite3
from pathlib import Path
from typing import Any, Optional

# ── DB path ───────────────────────────────────────────────────────────────────

_ROOT = Path(__file__).resolve().parents[3]  # Stock_bot root
DB_PATH = Path(os.getenv("DB_PATH", str(_ROOT / "data" / "stock.db")))

_SCHEMA_SQL = Path(__file__).resolve().parent / "schema.sql"


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# ── Schema / migrations ───────────────────────────────────────────────────────


def _ensure_products_extra_cols(conn: sqlite3.Connection) -> None:
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(products)")}
    if "barcode" not in cols:
        conn.execute("ALTER TABLE products ADD COLUMN barcode TEXT NOT NULL DEFAULT ''")
    if "note" not in cols:
        conn.execute("ALTER TABLE products ADD COLUMN note TEXT NOT NULL DEFAULT ''")
    if "archived" not in cols:
        conn.execute("ALTER TABLE products ADD COLUMN archived INTEGER NOT NULL DEFAULT 0")


def _ensure_clients_archived(conn: sqlite3.Connection) -> None:
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(clients)")}
    if "archived" not in cols:
        conn.execute("ALTER TABLE clients ADD COLUMN archived INTEGER NOT NULL DEFAULT 0")


def _ensure_price_tokens_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS price_tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            label TEXT NOT NULL DEFAULT '',
            token_hash TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            last_used_at TEXT,
            revoked INTEGER NOT NULL DEFAULT 0,
            mode TEXT NOT NULL DEFAULT 'SIMPLE'
        )
    """)
    
def _ensure_price_tokens_last_used_column(conn: sqlite3.Connection) -> None:
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(price_tokens)")}
    if "last_used_at" not in cols:
        conn.execute("ALTER TABLE price_tokens ADD COLUMN last_used_at TEXT")


def _ensure_price_tokens_mode_column(conn: sqlite3.Connection) -> None:
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(price_tokens)")}
    if "mode" not in cols:
        conn.execute(
            "ALTER TABLE price_tokens ADD COLUMN mode TEXT NOT NULL DEFAULT 'SIMPLE'"
        )


def _ensure_price_tokens_plain_token_column(conn: sqlite3.Connection) -> None:
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(price_tokens)")}
    if "plain_token" not in cols:
        conn.execute("ALTER TABLE price_tokens ADD COLUMN plain_token TEXT")


def _ensure_price_tokens_device_id_column(conn: sqlite3.Connection) -> None:
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(price_tokens)")}
    if "device_id" not in cols:
        conn.execute("ALTER TABLE price_tokens ADD COLUMN device_id TEXT")


def _ensure_receive_invoices_total(conn: sqlite3.Connection) -> None:
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(receive_invoices)")}
    if "total" not in cols:
        conn.execute(
            "ALTER TABLE receive_invoices ADD COLUMN total REAL NOT NULL DEFAULT 0"
        )
        conn.execute(
            """
            UPDATE receive_invoices
            SET total = COALESCE(
                (SELECT SUM(ri.total) FROM receive_items ri WHERE ri.invoice_id = receive_invoices.id),
                0
            )
            """
        )


def init_db() -> None:
    with _connect() as conn:
        conn.executescript(_SCHEMA_SQL.read_text())
        _ensure_products_extra_cols(conn)
        _ensure_clients_archived(conn)
        _ensure_price_tokens_table(conn)
        _ensure_price_tokens_last_used_column(conn)
        _ensure_price_tokens_mode_column(conn)
        _ensure_price_tokens_plain_token_column(conn)
        _ensure_price_tokens_device_id_column(conn)
        _ensure_receive_invoices_total(conn)
        conn.commit()


# ── Helpers ───────────────────────────────────────────────────────────────────


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row)


def _product_with_prices(row: Any) -> dict[str, Any]:
    d = dict(row) if not isinstance(row, dict) else row
    wh = float(d.get("wh_price", 0) or 0)
    d["wh10_price"] = round(wh * 1.10, 4)
    d["wh25_price"] = round(wh * 1.25, 4)
    return d


# ── Price-mode helper for Pocket Price ───────────────────────────────────────


def _apply_price_mode(product: dict[str, Any], mode: str) -> dict[str, Any]:
    """Add computed price fields; always strip wholesale purchase price."""
    wh = float(product.get("wh_price", 0) or 0)
    product["price_wh10"] = round(wh * 1.10, 4)
    product["price_wh25"] = round(wh * 1.25, 4)
    product.pop("wh_price", None)
    if (mode or "SIMPLE").upper() != "FULL":
        product.pop("price_wh10", None)
    return product


# ── Products ──────────────────────────────────────────────────────────────────


def list_products(include_archived: bool = False) -> list[dict[str, Any]]:
    with _connect() as conn:
        if include_archived:
            rows = conn.execute(
                "SELECT * FROM products ORDER BY brand, model"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM products WHERE archived = 0 ORDER BY brand, model"
            ).fetchall()
        return [_product_with_prices(r) for r in rows]


def add_product(brand: str, model: str, name: str, wh_price: float) -> int:
    with _connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO products (brand, model, name, wh_price) VALUES (?, ?, ?, ?)",
            (brand.upper(), model.lower(), name, wh_price),
        )
        conn.commit()
        row = conn.execute(
            "SELECT id FROM products WHERE brand = ? AND model = ?",
            (brand.upper(), model.lower()),
        ).fetchone()
        return row["id"] if row else 0


def add_product_simple(
    brand: str,
    model: str,
    name: str,
    barcode: str,
    note: str,
    wh_price: float,
) -> tuple[bool, str, int]:
    try:
        with _connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO products (brand, model, name, barcode, note, wh_price)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (brand.strip(), model.strip(), name.strip(), barcode.strip(), note.strip(), wh_price),
            )
            conn.commit()
            row = conn.execute(
                "SELECT id FROM products WHERE brand = ? AND model = ?",
                (brand.strip(), model.strip()),
            ).fetchone()
            if not row:
                return False, "product_not_found_after_insert", 0
            return True, "", row["id"]
    except Exception as exc:
        return False, str(exc), 0


def add_or_get_product_id(
    brand: str, model: str, name: str, wh_price: float
) -> tuple[int, bool]:
    with _connect() as conn:
        existing = conn.execute(
            "SELECT id FROM products WHERE brand = ? AND model = ?",
            (brand.strip(), model.strip()),
        ).fetchone()
        if existing:
            return existing["id"], False
        conn.execute(
            "INSERT INTO products (brand, model, name, wh_price) VALUES (?, ?, ?, ?)",
            (brand.strip(), model.strip(), name.strip(), wh_price),
        )
        conn.commit()
        row = conn.execute(
            "SELECT id FROM products WHERE brand = ? AND model = ?",
            (brand.strip(), model.strip()),
        ).fetchone()
        return row["id"], True


def update_product_full(
    product_id: int,
    barcode: str,
    wh_price: float,
    model: str,
    name: str,
) -> tuple[bool, str]:
    try:
        with _connect() as conn:
            conn.execute(
                "UPDATE products SET barcode = ?, wh_price = ?, model = ?, name = ? WHERE id = ?",
                (barcode.strip(), wh_price, model.strip(), name.strip(), product_id),
            )
            conn.commit()
        return True, ""
    except Exception as exc:
        return False, str(exc)


def update_product_wh_price(product_id: int, wh_price: float) -> tuple[bool, str]:
    try:
        with _connect() as conn:
            conn.execute(
                "UPDATE products SET wh_price = ? WHERE id = ?",
                (wh_price, product_id),
            )
            conn.commit()
        return True, ""
    except Exception as exc:
        return False, str(exc)


def set_product_archived(product_id: int, archived: int) -> tuple[bool, str]:
    try:
        with _connect() as conn:
            conn.execute(
                "UPDATE products SET archived = ? WHERE id = ?",
                (archived, product_id),
            )
            conn.commit()
        return True, ""
    except Exception as exc:
        return False, str(exc)


def search_products(
    q: str, limit: int = 30, warehouse: str = ""
) -> list[dict[str, Any]]:
    with _connect() as conn:
        like = f"%{q}%"
        rows = conn.execute(
            "SELECT * FROM products"
            " WHERE archived = 0"
            "   AND (brand LIKE ? OR model LIKE ? OR name LIKE ? OR barcode LIKE ?)"
            " ORDER BY brand, model"
            " LIMIT ?",
            (like, like, like, like, limit),
        ).fetchall()
        result = [_product_with_prices(r) for r in rows]
        if warehouse:
            wh = warehouse.strip()
            for p in result:
                qty_row = conn.execute(
                    "SELECT qty FROM stock WHERE warehouse_code = ? AND product_id = ?",
                    (wh, p["id"]),
                ).fetchone()
                p["qty_in_wh"] = float(qty_row["qty"]) if qty_row else 0.0
            result.sort(key=lambda x: (-x.get("qty_in_wh", 0), x["brand"], x["model"]))
        return result


def get_product_by_barcode(
    barcode: str, mode: str = "SIMPLE"
) -> Optional[dict[str, Any]]:
    """Look up a product by its barcode field."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT id, brand, model, name, wh_price, barcode, note"
            " FROM products WHERE barcode = ? AND archived = 0 LIMIT 1",
            (barcode.strip(),),
        ).fetchone()
        if not row:
            return None
        return _product_with_prices(dict(row))


# ── Stock ─────────────────────────────────────────────────────────────────────


def get_stock(
    warehouse: Optional[str] = None,
    q: Optional[str] = None,
    include_archived: bool = False,
) -> list[dict[str, Any]]:
    with _connect() as conn:
        conditions = []
        params: list[Any] = []
        if not include_archived:
            conditions.append("p.archived = 0")
        if warehouse:
            conditions.append("s.warehouse_code = ?")
            params.append(warehouse)
        if q:
            like = f"%{q}%"
            conditions.append(
                "(p.brand LIKE ? OR p.model LIKE ? OR p.name LIKE ?)"
            )
            params.extend([like, like, like])
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        rows = conn.execute(
            f"""
            SELECT s.warehouse_code, s.qty,
                   p.id AS product_id, p.brand, p.model, p.name,
                   p.wh_price, p.barcode, p.note, p.archived
            FROM stock s
            JOIN products p ON p.id = s.product_id
            {where}
            ORDER BY s.warehouse_code, p.brand, p.model
            """,
            params,
        ).fetchall()
        result = []
        for r in rows:
            d = _row_to_dict(r)
            wh = float(d.get("wh_price", 0) or 0)
            d["wh10_price"] = round(wh * 1.10, 4)
            d["wh25_price"] = round(wh * 1.25, 4)
            d["sale_price"] = d["wh25_price"]  # retail price = wholesale + 25%
            result.append(d)
        return result


def get_stock_qty(warehouse_code: str, product_id: int) -> float:
    with _connect() as conn:
        row = conn.execute(
            "SELECT qty FROM stock WHERE warehouse_code = ? AND product_id = ?",
            (warehouse_code, product_id),
        ).fetchone()
        return float(row["qty"]) if row else 0.0


def get_stock_text(warehouse: Optional[str] = None) -> str:
    rows = get_stock(warehouse)
    if not rows:
        return f"Склад{' ' + warehouse if warehouse else ''} пуст."
    lines = [f"<b>Остатки{' ' + warehouse if warehouse else ''}:</b>"]
    for r in rows:
        lines.append(
            f"• [{r['warehouse_code']}] {r['brand']} {r['model']}"
            f" — {r['name']}: {float(r['qty']):.2f} шт"
        )
    return "\n".join(lines)


def search_stock(
    warehouse: str, q: str, limit: int = 30
) -> list[dict[str, Any]]:
    with _connect() as conn:
        like = f"%{q}%"
        rows = conn.execute(
            """
            SELECT s.warehouse_code, s.qty,
                   p.id AS product_id, p.brand, p.model, p.name,
                   p.wh_price, p.barcode, p.note, p.archived
            FROM stock s
            JOIN products p ON p.id = s.product_id
            WHERE s.warehouse_code = ?
              AND p.archived = 0
              AND s.qty > 0
              AND (p.brand LIKE ? OR p.model LIKE ? OR p.name LIKE ?)
            ORDER BY p.brand, p.model
            LIMIT ?
            """,
            (warehouse, like, like, like, limit),
        ).fetchall()
        result = []
        for r in rows:
            d = _row_to_dict(r)
            wh = float(d.get("wh_price", 0) or 0)
            d["wh10_price"] = round(wh * 1.10, 4)
            d["wh25_price"] = round(wh * 1.25, 4)
            result.append(d)
        return result


def receive_stock(
    warehouse: str, brand: str, model: str, qty: float, source: str = "RECEIVE"
) -> tuple[bool, str]:
    try:
        with _connect() as conn:
            row = conn.execute(
                "SELECT id FROM products WHERE brand = ? AND model = ?",
                (brand.upper(), model.lower()),
            ).fetchone()
            if not row:
                return False, f"Товар {brand} {model} не найден"
            product_id = row["id"]
            conn.execute(
                """
                INSERT INTO stock (warehouse_code, product_id, qty)
                VALUES (?, ?, ?)
                ON CONFLICT(warehouse_code, product_id) DO UPDATE SET qty = qty + excluded.qty
                """,
                (warehouse, product_id, qty),
            )
            conn.execute(
                "INSERT INTO stock_ops (op_type, source, warehouse_code, product_id, qty)"
                " VALUES ('RECEIVE', ?, ?, ?, ?)",
                (source, warehouse, product_id, qty),
            )
            conn.commit()
        return True, ""
    except Exception as exc:
        return False, str(exc)


def receive_stock_by_product_id(
    warehouse: str, product_id: int, qty: float, source: str = "RECEIVE"
) -> tuple[bool, str]:
    try:
        with _connect() as conn:
            conn.execute(
                """
                INSERT INTO stock (warehouse_code, product_id, qty)
                VALUES (?, ?, ?)
                ON CONFLICT(warehouse_code, product_id) DO UPDATE SET qty = qty + excluded.qty
                """,
                (warehouse, product_id, qty),
            )
            conn.execute(
                "INSERT INTO stock_ops (op_type, source, warehouse_code, product_id, qty)"
                " VALUES ('RECEIVE', ?, ?, ?, ?)",
                (source, warehouse, product_id, qty),
            )
            conn.commit()
        return True, ""
    except Exception as exc:
        return False, str(exc)


def move_stock(
    src: str, dst: str, brand: str, model: str, qty: float
) -> tuple[bool, str]:
    try:
        with _connect() as conn:
            row = conn.execute(
                "SELECT id FROM products WHERE brand = ? AND model = ?",
                (brand.upper(), model.lower()),
            ).fetchone()
            if not row:
                return False, f"Товар {brand} {model} не найден"
            product_id = row["id"]
            ok, err = _move_by_id(conn, src, dst, product_id, qty)
            if ok:
                conn.commit()
            return ok, err
    except Exception as exc:
        return False, str(exc)


def move_stock_by_product_id(
    src: str, dst: str, product_id: int, qty: float
) -> tuple[bool, str]:
    try:
        with _connect() as conn:
            ok, err = _move_by_id(conn, src, dst, product_id, qty)
            if ok:
                conn.commit()
            return ok, err
    except Exception as exc:
        return False, str(exc)


def _move_by_id(
    conn: sqlite3.Connection, src: str, dst: str, product_id: int, qty: float
) -> tuple[bool, str]:
    src_row = conn.execute(
        "SELECT qty FROM stock WHERE warehouse_code = ? AND product_id = ?",
        (src, product_id),
    ).fetchone()
    if not src_row or float(src_row["qty"]) < qty:
        return False, "Недостаточно товара на складе"
    conn.execute(
        "UPDATE stock SET qty = qty - ? WHERE warehouse_code = ? AND product_id = ?",
        (qty, src, product_id),
    )
    conn.execute(
        """
        INSERT INTO stock (warehouse_code, product_id, qty)
        VALUES (?, ?, ?)
        ON CONFLICT(warehouse_code, product_id) DO UPDATE SET qty = qty + excluded.qty
        """,
        (dst, product_id, qty),
    )
    conn.execute(
        "INSERT INTO stock_ops (op_type, source, warehouse_code, product_id, qty)"
        " VALUES ('MOVE', ?, ?, ?, ?)",
        (src, dst, product_id, qty),
    )
    return True, ""


def move_all(src: str, dst: str) -> tuple[bool, str, int]:
    try:
        with _connect() as conn:
            rows = conn.execute(
                "SELECT product_id, qty FROM stock WHERE warehouse_code = ? AND qty > 0",
                (src,),
            ).fetchall()
            moved = 0
            for r in rows:
                conn.execute(
                    """
                    INSERT INTO stock (warehouse_code, product_id, qty)
                    VALUES (?, ?, ?)
                    ON CONFLICT(warehouse_code, product_id) DO UPDATE SET qty = qty + excluded.qty
                    """,
                    (dst, r["product_id"], r["qty"]),
                )
                conn.execute(
                    "INSERT INTO stock_ops (op_type, source, warehouse_code, product_id, qty)"
                    " VALUES ('MOVE', ?, ?, ?, ?)",
                    (src, dst, r["product_id"], r["qty"]),
                )
                moved += 1
            conn.execute(
                "UPDATE stock SET qty = 0 WHERE warehouse_code = ?", (src,)
            )
            conn.commit()
        return True, "", moved
    except Exception as exc:
        return False, str(exc), 0


def move_all_auto_shop(src: str) -> tuple[bool, str, int, str]:
    _DEPOT_TO_SHOP: dict[str, str] = {
        "CHINA_DEPOT": "SHOP_CHINA",
        "DEALER_DEPOT": "SHOP_DEALER",
        "TM_DEPO": "1416_SHOP",
    }
    dst = _DEPOT_TO_SHOP.get(src.upper(), src.upper() + "_SHOP")
    ok, err, moved = move_all(src, dst)
    return ok, err, moved, dst


# ── Brands ────────────────────────────────────────────────────────────────────


def list_brands() -> list[str]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT name FROM brands ORDER BY name"
        ).fetchall()
        return [r["name"] for r in rows]


def add_brand(name: str) -> tuple[bool, str]:
    try:
        with _connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO brands (name) VALUES (?)", (name.strip().upper(),)
            )
            conn.commit()
        return True, ""
    except Exception as exc:
        return False, str(exc)


def list_brand_model_prefixes(brand_name: str) -> list[str]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT prefix FROM brand_model_prefixes WHERE brand_name = ? ORDER BY prefix",
            (brand_name,),
        ).fetchall()
        return [r["prefix"] for r in rows]


def _normalize_prefix(raw: str) -> str:
    """Normalize a brand model prefix: strip spaces and trailing '-'/'.' chars."""
    return raw.strip().rstrip("-.")


def add_brand_model_prefix(brand_name: str, prefix: str) -> tuple[bool, str]:
    norm = _normalize_prefix(prefix)
    if not norm:
        return False, "Prefix cannot be empty"
    try:
        with _connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO brand_model_prefixes (brand_name, prefix) VALUES (?, ?)",
                (brand_name.strip(), norm),
            )
            conn.commit()
        return True, ""
    except Exception as exc:
        return False, str(exc)


def update_brand_model_prefix(brand_name: str, old_prefix: str, new_prefix: str) -> tuple[bool, str]:
    norm_new = _normalize_prefix(new_prefix)
    if not norm_new:
        return False, "New prefix cannot be empty"
    norm_old = _normalize_prefix(old_prefix)
    if norm_new == norm_old:
        return True, ""
    try:
        with _connect() as conn:
            # Check if new prefix already exists for this brand
            exists = conn.execute(
                "SELECT 1 FROM brand_model_prefixes WHERE brand_name = ? AND prefix = ?",
                (brand_name.strip(), norm_new),
            ).fetchone()
            if exists:
                return False, f"Prefix '{norm_new}' already exists for this brand"
            conn.execute(
                "UPDATE brand_model_prefixes SET prefix = ? WHERE brand_name = ? AND prefix = ?",
                (norm_new, brand_name.strip(), norm_old),
            )
            conn.commit()
        return True, ""
    except Exception as exc:
        return False, str(exc)


def delete_brand_model_prefix(brand_name: str, prefix: str) -> tuple[bool, str]:
    norm = _normalize_prefix(prefix)
    try:
        with _connect() as conn:
            conn.execute(
                "DELETE FROM brand_model_prefixes WHERE brand_name = ? AND prefix = ?",
                (brand_name.strip(), norm),
            )
            conn.commit()
        return True, ""
    except Exception as exc:
        return False, str(exc)


# ── Clients ───────────────────────────────────────────────────────────────────


def list_clients(include_archived: bool = False) -> list[dict[str, Any]]:
    with _connect() as conn:
        if include_archived:
            rows = conn.execute("SELECT * FROM clients ORDER BY name").fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM clients WHERE archived = 0 ORDER BY name"
            ).fetchall()
        return [_row_to_dict(r) for r in rows]


def add_client(
    name: str, phone: str = "", note: str = ""
) -> tuple[bool, str]:
    try:
        with _connect() as conn:
            conn.execute(
                "INSERT INTO clients (name, phone, note) VALUES (?, ?, ?)",
                (name.strip(), phone.strip(), note.strip()),
            )
            conn.commit()
        return True, ""
    except Exception as exc:
        return False, str(exc)


def get_client(client_id: int) -> Optional[dict[str, Any]]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM clients WHERE id = ?", (client_id,)
        ).fetchone()
        return _row_to_dict(row) if row else None


def update_client(
    client_id: int, name: str, phone: str, note: str
) -> tuple[bool, str]:
    try:
        with _connect() as conn:
            conn.execute(
                "UPDATE clients SET name = ?, phone = ?, note = ? WHERE id = ?",
                (name.strip(), phone.strip(), note.strip(), client_id),
            )
            conn.commit()
        return True, ""
    except Exception as exc:
        return False, str(exc)


def set_client_archived(client_id: int, archived: int) -> tuple[bool, str]:
    try:
        with _connect() as conn:
            conn.execute(
                "UPDATE clients SET archived = ? WHERE id = ?",
                (archived, client_id),
            )
            conn.commit()
        return True, ""
    except Exception as exc:
        return False, str(exc)


def add_client_adjustment(
    client_id: int, amount: float, note: str = ""
) -> tuple[bool, str]:
    """Record a payment/adjustment that reduces client debt (positive amount)."""
    try:
        with _connect() as conn:
            conn.execute(
                "INSERT INTO client_ledger (client_id, amount, note) VALUES (?, ?, ?)",
                (client_id, abs(amount), note.strip()),
            )
            conn.commit()
        return True, ""
    except Exception as exc:
        return False, str(exc)


def add_client_debt(
    client_id: int, amount: float, note: str = ""
) -> tuple[bool, str]:
    """Record an explicit additional debt (stored as negative in ledger)."""
    try:
        with _connect() as conn:
            conn.execute(
                "INSERT INTO client_ledger (client_id, amount, note) VALUES (?, ?, ?)",
                (client_id, -abs(amount), note.strip()),
            )
            conn.commit()
        return True, ""
    except Exception as exc:
        return False, str(exc)


def get_client_balance(client_id: int) -> float:
    """
    Balance = total from done sale invoices - sum(client_ledger.amount).
    Positive balance means client owes money.
    """
    with _connect() as conn:
        inv_row = conn.execute(
            """
            SELECT COALESCE(SUM(i.total), 0) AS total
            FROM invoices i
            JOIN carts c ON c.id = i.cart_id
            WHERE c.client_id = ? AND c.status = 'CLOSED'
            """,
            (client_id,),
        ).fetchone()
        inv_total = float(inv_row["total"]) if inv_row else 0.0

        ledger_row = conn.execute(
            "SELECT COALESCE(SUM(amount), 0) AS paid FROM client_ledger WHERE client_id = ?",
            (client_id,),
        ).fetchone()
        paid = float(ledger_row["paid"]) if ledger_row else 0.0

        return round(inv_total - paid, 4)


def list_clients_with_balance(
    include_archived: bool = False,
) -> list[dict[str, Any]]:
    clients = list_clients(include_archived=include_archived)
    for c in clients:
        c["balance"] = get_client_balance(c["id"])
    return clients


def get_client_history(client_id: int) -> list[dict[str, Any]]:
    with _connect() as conn:
        events: list[dict[str, Any]] = []
        rows = conn.execute(
            """
            SELECT i.number, i.created_at, i.total, i.currency, 'SALE' AS event_type
            FROM invoices i
            JOIN carts c ON c.id = i.cart_id
            WHERE c.client_id = ? AND c.status = 'CLOSED'
            ORDER BY i.created_at DESC
            """,
            (client_id,),
        ).fetchall()
        for r in rows:
            events.append(_row_to_dict(r))
        rows2 = conn.execute(
            "SELECT id, created_at, amount, note, 'LEDGER' AS event_type"
            " FROM client_ledger WHERE client_id = ? ORDER BY created_at DESC",
            (client_id,),
        ).fetchall()
        for r in rows2:
            events.append(_row_to_dict(r))
        events.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        return events


def get_total_clients_debt() -> float:
    with _connect() as conn:
        inv_row = conn.execute(
            """
            SELECT COALESCE(SUM(i.total), 0) AS total
            FROM invoices i
            JOIN carts c ON c.id = i.cart_id
            WHERE c.status = 'CLOSED'
            """
        ).fetchone()
        inv_total = float(inv_row["total"]) if inv_row else 0.0

        ledger_row = conn.execute(
            "SELECT COALESCE(SUM(amount), 0) AS paid FROM client_ledger"
        ).fetchone()
        paid = float(ledger_row["paid"]) if ledger_row else 0.0

        return round(max(0.0, inv_total - paid), 4)


def get_total_stock_value() -> float:
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT COALESCE(SUM(s.qty * p.wh_price), 0) AS val
            FROM stock s
            JOIN products p ON p.id = s.product_id
            WHERE p.archived = 0
            """
        ).fetchone()
        return round(float(row["val"]) if row else 0.0, 4)


# ── Warehouses ────────────────────────────────────────────────────────────────


def list_warehouses() -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute("SELECT code, title FROM warehouses ORDER BY code").fetchall()
        return [_row_to_dict(r) for r in rows]


def add_warehouse(code: str, title: str) -> tuple[bool, str]:
    try:
        with _connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO warehouses (code, title) VALUES (?, ?)",
                (code.strip().upper(), title.strip()),
            )
            conn.commit()
        return True, ""
    except Exception as exc:
        return False, str(exc)


# ── Cart price helper ─────────────────────────────────────────────────────────


def _compute_unit_price(
    wh_price: float, price_mode: str, custom_price: Optional[float]
) -> float:
    if price_mode == "wh":
        return wh_price
    elif price_mode == "wh10":
        return round(wh_price * 1.10, 4)
    elif price_mode == "custom" and custom_price is not None:
        return custom_price
    else:
        return round(wh_price * 1.10, 4)


# ── Cart (old Telegram bot-style) ─────────────────────────────────────────────


def _get_or_create_client_id(conn: sqlite3.Connection, client_name: str) -> int:
    row = conn.execute(
        "SELECT id FROM clients WHERE name = ?", (client_name,)
    ).fetchone()
    if row:
        return row["id"]
    conn.execute("INSERT INTO clients (name) VALUES (?)", (client_name,))
    conn.commit()
    row = conn.execute(
        "SELECT id FROM clients WHERE name = ?", (client_name,)
    ).fetchone()
    return row["id"]


def cart_start(client_name: str) -> None:
    with _connect() as conn:
        client_id = _get_or_create_client_id(conn, client_name)
        conn.execute(
            "UPDATE carts SET status = 'CANCELLED' WHERE client_id = ? AND status = 'OPEN'",
            (client_id,),
        )
        conn.execute(
            "INSERT INTO carts (client_id, warehouse_code) VALUES (?, '1416_SHOP')",
            (client_id,),
        )
        conn.commit()


def cart_add(
    client_name: str,
    brand: str,
    model: str,
    qty: float,
    price_mode: str = "wh10",
    custom_price: Optional[float] = None,
) -> tuple[bool, str]:
    try:
        with _connect() as conn:
            client_id = _get_or_create_client_id(conn, client_name)
            cart_row = conn.execute(
                "SELECT id FROM carts WHERE client_id = ? AND status = 'OPEN'"
                " ORDER BY id DESC LIMIT 1",
                (client_id,),
            ).fetchone()
            if not cart_row:
                return False, "Корзина не открыта. /cart_start CLIENT"
            cart_id = cart_row["id"]

            prod_row = conn.execute(
                "SELECT id, wh_price FROM products WHERE brand = ? AND model = ?",
                (brand.upper(), model.lower()),
            ).fetchone()
            if not prod_row:
                return False, f"Товар {brand} {model} не найден"

            wh_price = float(prod_row["wh_price"])
            unit_price = _compute_unit_price(wh_price, price_mode, custom_price)
            total = round(unit_price * qty, 4)

            existing = conn.execute(
                "SELECT id FROM cart_items WHERE cart_id = ? AND product_id = ?",
                (cart_id, prod_row["id"]),
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE cart_items SET qty = qty + ?, total = total + ? WHERE id = ?",
                    (qty, total, existing["id"]),
                )
            else:
                conn.execute(
                    "INSERT INTO cart_items"
                    " (cart_id, product_id, qty, price_mode, unit_price, total)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (cart_id, prod_row["id"], qty, price_mode, unit_price, total),
                )
            conn.commit()
        return True, ""
    except Exception as exc:
        return False, str(exc)


def cart_show(client_name: str) -> tuple[bool, str]:
    with _connect() as conn:
        client_id_row = conn.execute(
            "SELECT id FROM clients WHERE name = ?", (client_name,)
        ).fetchone()
        if not client_id_row:
            return False, "Клиент не найден"
        cart_row = conn.execute(
            "SELECT id FROM carts WHERE client_id = ? AND status = 'OPEN'"
            " ORDER BY id DESC LIMIT 1",
            (client_id_row["id"],),
        ).fetchone()
        if not cart_row:
            return False, "Корзина пуста или не открыта"
        items = conn.execute(
            """
            SELECT p.brand, p.model, p.name, ci.qty, ci.price_mode, ci.unit_price, ci.total
            FROM cart_items ci
            JOIN products p ON p.id = ci.product_id
            WHERE ci.cart_id = ?
            """,
            (cart_row["id"],),
        ).fetchall()
        if not items:
            return True, "Корзина пуста."
        lines = [f"<b>Корзина ({client_name}):</b>"]
        grand_total = 0.0
        for i in items:
            lines.append(
                f"• {i['brand']} {i['model']} \u00d7 {float(i['qty']):.2f}"
                f" @ {float(i['unit_price']):.2f} = {float(i['total']):.2f}"
            )
            grand_total += float(i["total"])
        lines.append(f"\n<b>Итого: {grand_total:.2f}</b>")
        return True, "\n".join(lines)


def cart_remove(client_name: str, brand: str, model: str) -> tuple[bool, str]:
    try:
        with _connect() as conn:
            client_id_row = conn.execute(
                "SELECT id FROM clients WHERE name = ?", (client_name,)
            ).fetchone()
            if not client_id_row:
                return False, "Клиент не найден"
            cart_row = conn.execute(
                "SELECT id FROM carts WHERE client_id = ? AND status = 'OPEN'"
                " ORDER BY id DESC LIMIT 1",
                (client_id_row["id"],),
            ).fetchone()
            if not cart_row:
                return False, "Корзина не открыта"
            prod_row = conn.execute(
                "SELECT id FROM products WHERE brand = ? AND model = ?",
                (brand.upper(), model.lower()),
            ).fetchone()
            if not prod_row:
                return False, f"Товар {brand} {model} не найден"
            conn.execute(
                "DELETE FROM cart_items WHERE cart_id = ? AND product_id = ?",
                (cart_row["id"], prod_row["id"]),
            )
            conn.commit()
        return True, ""
    except Exception as exc:
        return False, str(exc)


def cart_finish_from_shop(
    client_name: str, shop_warehouse: str
) -> tuple[bool, str, dict, list]:
    try:
        with _connect() as conn:
            client_id_row = conn.execute(
                "SELECT id FROM clients WHERE name = ?", (client_name,)
            ).fetchone()
            if not client_id_row:
                return False, "Клиент не найден", {}, []
            client_id = client_id_row["id"]
            cart_row = conn.execute(
                "SELECT id FROM carts WHERE client_id = ? AND status = 'OPEN'"
                " ORDER BY id DESC LIMIT 1",
                (client_id,),
            ).fetchone()
            if not cart_row:
                return False, "Корзина не открыта", {}, []
            cart_id = cart_row["id"]

            items = conn.execute(
                """
                SELECT ci.id, ci.product_id, ci.qty, ci.price_mode, ci.unit_price, ci.total,
                       p.brand, p.model, p.name
                FROM cart_items ci
                JOIN products p ON p.id = ci.product_id
                WHERE ci.cart_id = ?
                """,
                (cart_id,),
            ).fetchall()
            if not items:
                return False, "Корзина пуста", {}, []

            for item in items:
                qty_row = conn.execute(
                    "SELECT qty FROM stock WHERE warehouse_code = ? AND product_id = ?",
                    (shop_warehouse, item["product_id"]),
                ).fetchone()
                available = float(qty_row["qty"]) if qty_row else 0.0
                if available < float(item["qty"]):
                    return (
                        False,
                        f"Недостаточно {item['brand']} {item['model']} на {shop_warehouse}",
                        {},
                        [],
                    )

            for item in items:
                conn.execute(
                    "UPDATE stock SET qty = qty - ?"
                    " WHERE warehouse_code = ? AND product_id = ?",
                    (item["qty"], shop_warehouse, item["product_id"]),
                )
                conn.execute(
                    "INSERT INTO stock_ops"
                    " (op_type, source, warehouse_code, product_id, qty)"
                    " VALUES ('SALE', 'SHOP', ?, ?, ?)",
                    (shop_warehouse, item["product_id"], item["qty"]),
                )

            total = sum(float(i["total"]) for i in items)
            conn.execute(
                "UPDATE carts SET status = 'CLOSED' WHERE id = ?", (cart_id,)
            )
            next_num = conn.execute(
                "SELECT COALESCE(MAX(number), 0) + 1 AS n FROM invoices"
            ).fetchone()["n"]
            conn.execute(
                "INSERT INTO invoices (cart_id, number, total) VALUES (?, ?, ?)",
                (cart_id, next_num, total),
            )
            conn.commit()

            invoice_row = conn.execute(
                """
                SELECT i.*, c.client_id, c.warehouse_code
                FROM invoices i
                JOIN carts c ON c.id = i.cart_id
                WHERE i.cart_id = ?
                """,
                (cart_id,),
            ).fetchone()
            invoice = _row_to_dict(invoice_row)
            invoice["client"] = client_name
            items_list = [_row_to_dict(i) for i in items]
            return True, "", invoice, items_list
    except Exception as exc:
        return False, str(exc), {}, []


def cart_finish(client_name: str) -> tuple[bool, str, dict, list]:
    return cart_finish_from_shop(client_name, "1416_SHOP")


# ── Cart (new web/ERP style) ──────────────────────────────────────────────────


def get_open_cart() -> Optional[dict[str, Any]]:
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT c.id AS cart_id, c.client_id, c.warehouse_code, c.created_at,
                   cl.name AS client_name
            FROM carts c
            JOIN clients cl ON cl.id = c.client_id
            WHERE c.status = 'OPEN'
            ORDER BY c.id DESC
            LIMIT 1
            """
        ).fetchone()
        return _row_to_dict(row) if row else None


def cart_start_by_id(
    client_id: int, warehouse_code: str = "1416_SHOP"
) -> tuple[bool, str, int]:
    try:
        with _connect() as conn:
            existing = conn.execute(
                "SELECT id FROM carts WHERE status = 'OPEN' LIMIT 1"
            ).fetchone()
            if existing:
                return False, "already_open", existing["id"]
            conn.execute(
                "INSERT INTO carts (client_id, warehouse_code) VALUES (?, ?)",
                (client_id, warehouse_code),
            )
            conn.commit()
            cart_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
            return True, "", cart_id
    except Exception as exc:
        return False, str(exc), 0


def cart_add_by_id(
    client_id: int,
    brand: str,
    model: str,
    qty: float,
    price_mode: str = "wh10",
    custom_price: Optional[float] = None,
) -> tuple[bool, str]:
    with _connect() as conn:
        cart_row = conn.execute(
            "SELECT id FROM carts WHERE client_id = ? AND status = 'OPEN'"
            " ORDER BY id DESC LIMIT 1",
            (client_id,),
        ).fetchone()
        if not cart_row:
            return False, "no_open_cart"
        return _cart_add_item(conn, cart_row["id"], brand, model, qty, price_mode, custom_price)


def cart_add_by_cart_id(
    cart_id: int,
    brand: str,
    model: str,
    qty: float,
    price_mode: str = "wh10",
    custom_price: Optional[float] = None,
) -> tuple[bool, str]:
    with _connect() as conn:
        return _cart_add_item(conn, cart_id, brand, model, qty, price_mode, custom_price)


def _cart_add_item(
    conn: sqlite3.Connection,
    cart_id: int,
    brand: str,
    model: str,
    qty: float,
    price_mode: str,
    custom_price: Optional[float],
) -> tuple[bool, str]:
    try:
        prod_row = conn.execute(
            "SELECT id, wh_price FROM products WHERE brand = ? AND model = ?",
            (brand.strip(), model.strip()),
        ).fetchone()
        if not prod_row:
            return False, f"product_not_found:{brand} {model}"
        wh_price = float(prod_row["wh_price"])
        unit_price = _compute_unit_price(wh_price, price_mode, custom_price)
        total = round(unit_price * qty, 4)
        conn.execute(
            "INSERT INTO cart_items"
            " (cart_id, product_id, qty, price_mode, unit_price, total)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (cart_id, prod_row["id"], qty, price_mode, unit_price, total),
        )
        conn.commit()
        return True, ""
    except Exception as exc:
        return False, str(exc)


def cart_show_by_id(client_id: int) -> tuple[bool, str]:
    with _connect() as conn:
        cart_row = conn.execute(
            "SELECT id FROM carts WHERE client_id = ? AND status = 'OPEN'"
            " ORDER BY id DESC LIMIT 1",
            (client_id,),
        ).fetchone()
        if not cart_row:
            return False, "no_open_cart"
        return _cart_show_text(conn, cart_row["id"])


def cart_show_by_cart_id(cart_id: int) -> tuple[bool, str]:
    with _connect() as conn:
        return _cart_show_text(conn, cart_id)


def _cart_show_text(conn: sqlite3.Connection, cart_id: int) -> tuple[bool, str]:
    items = conn.execute(
        """
        SELECT p.brand, p.model, ci.qty, ci.unit_price, ci.total
        FROM cart_items ci JOIN products p ON p.id = ci.product_id
        WHERE ci.cart_id = ?
        """,
        (cart_id,),
    ).fetchall()
    if not items:
        return True, "Cart is empty."
    lines = []
    total = 0.0
    for i in items:
        lines.append(
            f"{i['brand']} {i['model']} x{float(i['qty']):.2f}"
            f" @ {float(i['unit_price']):.2f} = {float(i['total']):.2f}"
        )
        total += float(i["total"])
    lines.append(f"Total: {total:.2f}")
    return True, "\n".join(lines)


def cart_finish_by_id(client_id: int) -> tuple[bool, str, dict, list]:
    with _connect() as conn:
        cart_row = conn.execute(
            "SELECT id, warehouse_code FROM carts WHERE client_id = ? AND status = 'OPEN'"
            " ORDER BY id DESC LIMIT 1",
            (client_id,),
        ).fetchone()
        if not cart_row:
            return False, "no_open_cart", {}, []
        return _finish_cart(conn, cart_row["id"], cart_row["warehouse_code"])


def cart_finish_by_cart_id_shop1416(cart_id: int) -> tuple[bool, str, dict, list]:
    with _connect() as conn:
        cart_row = conn.execute(
            "SELECT id, warehouse_code FROM carts WHERE id = ? AND status = 'OPEN'",
            (cart_id,),
        ).fetchone()
        if not cart_row:
            return False, "no_open_cart", {}, []
        return _finish_cart(conn, cart_id, cart_row["warehouse_code"])


def _finish_cart(
    conn: sqlite3.Connection, cart_id: int, warehouse_code: str
) -> tuple[bool, str, dict, list]:
    try:
        items = conn.execute(
            """
            SELECT ci.id, ci.product_id, ci.qty, ci.price_mode, ci.unit_price, ci.total,
                   p.brand, p.model, p.name
            FROM cart_items ci
            JOIN products p ON p.id = ci.product_id
            WHERE ci.cart_id = ?
            """,
            (cart_id,),
        ).fetchall()
        if not items:
            return False, "cart_empty", {}, []

        for item in items:
            qty_row = conn.execute(
                "SELECT qty FROM stock WHERE warehouse_code = ? AND product_id = ?",
                (warehouse_code, item["product_id"]),
            ).fetchone()
            available = float(qty_row["qty"]) if qty_row else 0.0
            if available < float(item["qty"]):
                return (
                    False,
                    f"insufficient_stock:{item['brand']} {item['model']}",
                    {},
                    [],
                )

        for item in items:
            conn.execute(
                "UPDATE stock SET qty = qty - ?"
                " WHERE warehouse_code = ? AND product_id = ?",
                (item["qty"], warehouse_code, item["product_id"]),
            )
            conn.execute(
                "INSERT INTO stock_ops"
                " (op_type, source, warehouse_code, product_id, qty)"
                " VALUES ('SALE', 'SHOP', ?, ?, ?)",
                (warehouse_code, item["product_id"], item["qty"]),
            )

        total = sum(float(i["total"]) for i in items)
        conn.execute("UPDATE carts SET status = 'CLOSED' WHERE id = ?", (cart_id,))
        next_num = conn.execute(
            "SELECT COALESCE(MAX(number), 0) + 1 AS n FROM invoices"
        ).fetchone()["n"]
        conn.execute(
            "INSERT INTO invoices (cart_id, number, total) VALUES (?, ?, ?)",
            (cart_id, next_num, total),
        )
        conn.commit()

        inv_row = conn.execute(
            """
            SELECT i.*, c.client_id, cl.name AS client, c.warehouse_code
            FROM invoices i
            JOIN carts c ON c.id = i.cart_id
            JOIN clients cl ON cl.id = c.client_id
            WHERE i.cart_id = ?
            """,
            (cart_id,),
        ).fetchone()
        invoice = _row_to_dict(inv_row)
        items_list = [_row_to_dict(i) for i in items]
        return True, "", invoice, items_list
    except Exception as exc:
        return False, str(exc), {}, []


def get_cart_items_list(cart_id: int) -> tuple[Optional[dict], list[dict]]:
    with _connect() as conn:
        cart_row = conn.execute(
            """
            SELECT c.id AS cart_id, c.client_id, c.warehouse_code, c.status,
                   cl.name AS client_name
            FROM carts c
            JOIN clients cl ON cl.id = c.client_id
            WHERE c.id = ?
            """,
            (cart_id,),
        ).fetchone()
        if not cart_row:
            return None, []
        items = conn.execute(
            """
            SELECT ci.id, ci.product_id, ci.qty, ci.price_mode, ci.unit_price, ci.total,
                   p.brand, p.model, p.name, p.wh_price
            FROM cart_items ci
            JOIN products p ON p.id = ci.product_id
            WHERE ci.cart_id = ?
            ORDER BY ci.id
            """,
            (cart_id,),
        ).fetchall()
        return _row_to_dict(cart_row), [_row_to_dict(i) for i in items]


def cancel_cart(cart_id: int) -> tuple[bool, str]:
    try:
        with _connect() as conn:
            conn.execute(
                "UPDATE carts SET status = 'CANCELLED'"
                " WHERE id = ? AND status = 'OPEN'",
                (cart_id,),
            )
            conn.commit()
        return True, ""
    except Exception as exc:
        return False, str(exc)


def update_cart_item(
    item_id: int, qty: float, unit_price: float
) -> tuple[bool, str]:
    try:
        with _connect() as conn:
            total = round(qty * unit_price, 4)
            conn.execute(
                "UPDATE cart_items SET qty = ?, unit_price = ?, total = ? WHERE id = ?",
                (qty, unit_price, total, item_id),
            )
            conn.commit()
        return True, ""
    except Exception as exc:
        return False, str(exc)


def delete_cart_item(item_id: int) -> tuple[bool, str]:
    try:
        with _connect() as conn:
            conn.execute("DELETE FROM cart_items WHERE id = ?", (item_id,))
            conn.commit()
        return True, ""
    except Exception as exc:
        return False, str(exc)


def cart_set_client(cart_id: int, client_id: int) -> tuple[bool, str]:
    try:
        with _connect() as conn:
            conn.execute(
                "UPDATE carts SET client_id = ? WHERE id = ?",
                (client_id, cart_id),
            )
            conn.commit()
        return True, ""
    except Exception as exc:
        return False, str(exc)


# ── Sale invoices ─────────────────────────────────────────────────────────────


def get_invoice_by_number(number: int) -> Optional[dict[str, Any]]:
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT i.*, cl.name AS client, c.warehouse_code
            FROM invoices i
            JOIN carts c ON c.id = i.cart_id
            JOIN clients cl ON cl.id = c.client_id
            WHERE i.number = ?
            """,
            (number,),
        ).fetchone()
        return _row_to_dict(row) if row else None


def get_invoice_items_by_number(number: int) -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT ci.id, ci.qty, ci.price_mode, ci.unit_price, ci.total,
                   p.brand, p.model, p.name, p.id AS product_id
            FROM invoices i
            JOIN carts c ON c.id = i.cart_id
            JOIN cart_items ci ON ci.cart_id = c.id
            JOIN products p ON p.id = ci.product_id
            WHERE i.number = ?
            ORDER BY ci.id
            """,
            (number,),
        ).fetchall()
        return [_row_to_dict(r) for r in rows]


def list_sale_invoices_done() -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT i.id, i.number, i.created_at, i.total, i.currency,
                   cl.name AS client, c.warehouse_code
            FROM invoices i
            JOIN carts c ON c.id = i.cart_id
            JOIN clients cl ON cl.id = c.client_id
            ORDER BY i.number DESC
            """
        ).fetchall()
        return [_row_to_dict(r) for r in rows]


def get_sale_invoice_full(number: int) -> Optional[dict[str, Any]]:
    return get_invoice_by_number(number)


def get_sale_invoice_items_full(number: int) -> list[dict[str, Any]]:
    return get_invoice_items_by_number(number)


def get_last_sale_price(
    product_id: int, client_id: Optional[int] = None
) -> Optional[float]:
    with _connect() as conn:
        if client_id:
            row = conn.execute(
                """
                SELECT ci.unit_price
                FROM cart_items ci
                JOIN carts c ON c.id = ci.cart_id
                WHERE ci.product_id = ? AND c.client_id = ? AND c.status = 'CLOSED'
                ORDER BY c.id DESC LIMIT 1
                """,
                (product_id, client_id),
            ).fetchone()
        else:
            row = conn.execute(
                """
                SELECT ci.unit_price
                FROM cart_items ci
                JOIN carts c ON c.id = ci.cart_id
                WHERE ci.product_id = ? AND c.status = 'CLOSED'
                ORDER BY c.id DESC LIMIT 1
                """,
                (product_id,),
            ).fetchone()
        return float(row["unit_price"]) if row else None


def update_sale_invoice(
    number: int,
    client_id: int,
    warehouse_code: str,
    new_items: list[dict[str, Any]],
) -> tuple[bool, str]:
    try:
        with _connect() as conn:
            inv_row = conn.execute(
                "SELECT i.id, i.cart_id FROM invoices i WHERE i.number = ?",
                (number,),
            ).fetchone()
            if not inv_row:
                return False, "invoice_not_found"
            cart_id = inv_row["cart_id"]

            old_items = conn.execute(
                "SELECT product_id, qty FROM cart_items WHERE cart_id = ?",
                (cart_id,),
            ).fetchall()
            cart_wh = conn.execute(
                "SELECT warehouse_code FROM carts WHERE id = ?", (cart_id,)
            ).fetchone()["warehouse_code"]
            for oi in old_items:
                conn.execute(
                    "UPDATE stock SET qty = qty + ?"
                    " WHERE warehouse_code = ? AND product_id = ?",
                    (oi["qty"], cart_wh, oi["product_id"]),
                )

            conn.execute(
                "UPDATE carts SET client_id = ?, warehouse_code = ? WHERE id = ?",
                (client_id, warehouse_code, cart_id),
            )
            conn.execute("DELETE FROM cart_items WHERE cart_id = ?", (cart_id,))

            total = 0.0
            for item in new_items:
                pid = item["product_id"]
                qty = float(item["qty"])
                unit_price = float(item["unit_price"])
                item_total = round(qty * unit_price, 4)
                total += item_total
                conn.execute(
                    "INSERT INTO cart_items"
                    " (cart_id, product_id, qty, price_mode, unit_price, total)"
                    " VALUES (?, ?, ?, 'custom', ?, ?)",
                    (cart_id, pid, qty, unit_price, item_total),
                )
                conn.execute(
                    "UPDATE stock SET qty = qty - ?"
                    " WHERE warehouse_code = ? AND product_id = ?",
                    (qty, warehouse_code, pid),
                )

            conn.execute(
                "UPDATE invoices SET total = ? WHERE cart_id = ?", (total, cart_id)
            )
            conn.commit()
        return True, ""
    except Exception as exc:
        return False, str(exc)


# ── Receive invoices ──────────────────────────────────────────────────────────


def receive_invoice_get_open() -> Optional[dict[str, Any]]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM receive_invoices WHERE status = 'OPEN'"
            " ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return _row_to_dict(row) if row else None


def receive_invoice_start(
    supplier: str, destination_warehouse: str, note: str = ""
) -> tuple[bool, str, int]:
    try:
        with _connect() as conn:
            existing = conn.execute(
                "SELECT id FROM receive_invoices WHERE status = 'OPEN' LIMIT 1"
            ).fetchone()
            if existing:
                return False, "already_open", existing["id"]
            next_num = conn.execute(
                "SELECT COALESCE(MAX(number), 0) + 1 AS n FROM receive_invoices"
            ).fetchone()["n"]
            conn.execute(
                "INSERT INTO receive_invoices"
                " (number, supplier, destination_warehouse, note)"
                " VALUES (?, ?, ?, ?)",
                (next_num, supplier.strip(), destination_warehouse.strip(), note.strip()),
            )
            conn.commit()
            inv_id = conn.execute(
                "SELECT last_insert_rowid() AS id"
            ).fetchone()["id"]
            return True, "", inv_id
    except Exception as exc:
        return False, str(exc), 0


def receive_invoice_cancel(invoice_id: int) -> tuple[bool, str]:
    try:
        with _connect() as conn:
            conn.execute(
                "UPDATE receive_invoices SET status = 'CANCELLED'"
                " WHERE id = ? AND status = 'OPEN'",
                (invoice_id,),
            )
            conn.commit()
        return True, ""
    except Exception as exc:
        return False, str(exc)


def receive_item_add(
    invoice_id: int, product_id: int, qty: float, purchase_price: float
) -> tuple[bool, str]:
    try:
        with _connect() as conn:
            total = round(qty * purchase_price, 4)
            conn.execute(
                "INSERT INTO receive_items"
                " (invoice_id, product_id, qty, purchase_price, total)"
                " VALUES (?, ?, ?, ?, ?)",
                (invoice_id, product_id, qty, purchase_price, total),
            )
            # Always update the product's base incoming price to keep it current
            if purchase_price > 0:
                conn.execute(
                    "UPDATE products SET wh_price = ? WHERE id = ?",
                    (purchase_price, product_id),
                )
            conn.commit()
        return True, ""
    except Exception as exc:
        return False, str(exc)


def receive_item_update(
    item_id: int, qty: float, purchase_price: float
) -> tuple[bool, str]:
    try:
        with _connect() as conn:
            total = round(qty * purchase_price, 4)
            conn.execute(
                "UPDATE receive_items"
                " SET qty = ?, purchase_price = ?, total = ? WHERE id = ?",
                (qty, purchase_price, total, item_id),
            )
            conn.commit()
        return True, ""
    except Exception as exc:
        return False, str(exc)


def receive_item_delete(item_id: int) -> tuple[bool, str]:
    try:
        with _connect() as conn:
            conn.execute("DELETE FROM receive_items WHERE id = ?", (item_id,))
            conn.commit()
        return True, ""
    except Exception as exc:
        return False, str(exc)


def receive_invoice_finish(invoice_id: int) -> tuple[bool, str]:
    try:
        with _connect() as conn:
            inv = conn.execute(
                "SELECT * FROM receive_invoices WHERE id = ? AND status = 'OPEN'",
                (invoice_id,),
            ).fetchone()
            if not inv:
                return False, "invoice_not_found_or_not_open"
            items = conn.execute(
                "SELECT * FROM receive_items WHERE invoice_id = ?", (invoice_id,)
            ).fetchall()
            if not items:
                return False, "no_items"
            warehouse = inv["destination_warehouse"]
            for item in items:
                conn.execute(
                    """
                    INSERT INTO stock (warehouse_code, product_id, qty)
                    VALUES (?, ?, ?)
                    ON CONFLICT(warehouse_code, product_id)
                    DO UPDATE SET qty = qty + excluded.qty
                    """,
                    (warehouse, item["product_id"], item["qty"]),
                )
                conn.execute(
                    "INSERT INTO stock_ops"
                    " (op_type, source, warehouse_code, product_id, qty)"
                    " VALUES ('RECEIVE', ?, ?, ?, ?)",
                    (inv["supplier"] or "RECEIVE", warehouse, item["product_id"], item["qty"]),
                )
            conn.execute(
                """
                UPDATE receive_invoices
                SET status = 'DONE',
                    total = COALESCE(
                        (SELECT SUM(ri.total) FROM receive_items ri WHERE ri.invoice_id = ?),
                        0
                    )
                WHERE id = ?
                """,
                (invoice_id, invoice_id),
            )
            conn.commit()
        return True, ""
    except Exception as exc:
        return False, str(exc)


def receive_invoice_get(invoice_id: int) -> Optional[dict[str, Any]]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM receive_invoices WHERE id = ?", (invoice_id,)
        ).fetchone()
        return _row_to_dict(row) if row else None


def receive_invoice_get_items(invoice_id: int) -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT ri.id, ri.qty, ri.purchase_price, ri.total,
                   p.brand, p.model, p.name, p.id AS product_id
            FROM receive_items ri
            JOIN products p ON p.id = ri.product_id
            WHERE ri.invoice_id = ?
            ORDER BY ri.id
            """,
            (invoice_id,),
        ).fetchall()
        return [_row_to_dict(r) for r in rows]


def list_receive_invoices_done() -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM receive_invoices WHERE status = 'DONE'"
            " ORDER BY number DESC"
        ).fetchall()
        return [_row_to_dict(r) for r in rows]


def list_receive_suppliers() -> list[str]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT supplier FROM receive_invoices"
            " WHERE supplier != '' ORDER BY supplier"
        ).fetchall()
        return [r["supplier"] for r in rows]


def get_receive_invoice_items_for_edit(invoice_id: int) -> list[dict[str, Any]]:
    return receive_invoice_get_items(invoice_id)


def update_receive_invoice(
    invoice_id: int,
    supplier: str,
    destination_warehouse: str,
    new_items: list[dict[str, Any]],
) -> tuple[bool, str]:
    try:
        with _connect() as conn:
            inv = conn.execute(
                "SELECT * FROM receive_invoices WHERE id = ?", (invoice_id,)
            ).fetchone()
            if not inv:
                return False, "invoice_not_found"

            if inv["status"] == "DONE":
                old_items = conn.execute(
                    "SELECT product_id, qty FROM receive_items WHERE invoice_id = ?",
                    (invoice_id,),
                ).fetchall()
                for oi in old_items:
                    conn.execute(
                        "UPDATE stock SET qty = qty - ?"
                        " WHERE warehouse_code = ? AND product_id = ?",
                        (oi["qty"], inv["destination_warehouse"], oi["product_id"]),
                    )

            conn.execute(
                "UPDATE receive_invoices SET supplier = ?, destination_warehouse = ?"
                " WHERE id = ?",
                (supplier.strip(), destination_warehouse.strip(), invoice_id),
            )
            conn.execute("DELETE FROM receive_items WHERE invoice_id = ?", (invoice_id,))

            for item in new_items:
                qty = float(item["qty"])
                pp = float(item["purchase_price"])
                total = round(qty * pp, 4)
                conn.execute(
                    "INSERT INTO receive_items"
                    " (invoice_id, product_id, qty, purchase_price, total)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (invoice_id, item["product_id"], qty, pp, total),
                )
                if inv["status"] == "DONE":
                    conn.execute(
                        """
                        INSERT INTO stock (warehouse_code, product_id, qty)
                        VALUES (?, ?, ?)
                        ON CONFLICT(warehouse_code, product_id)
                        DO UPDATE SET qty = qty + excluded.qty
                        """,
                        (destination_warehouse, item["product_id"], qty),
                    )

            conn.commit()
        return True, ""
    except Exception as exc:
        return False, str(exc)


# ── Return invoices ───────────────────────────────────────────────────────────


def return_invoice_get_open() -> Optional[dict[str, Any]]:
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT ri.*, cl.name AS client_name
            FROM return_invoices ri
            JOIN clients cl ON cl.id = ri.client_id
            WHERE ri.status = 'OPEN' ORDER BY ri.id DESC LIMIT 1
            """
        ).fetchone()
        return _row_to_dict(row) if row else None


def return_invoice_start(
    client_id: int, warehouse_code: str, note: str = ""
) -> tuple[bool, str, int]:
    try:
        with _connect() as conn:
            existing = conn.execute(
                "SELECT id FROM return_invoices WHERE status = 'OPEN' LIMIT 1"
            ).fetchone()
            if existing:
                return False, "already_open", existing["id"]
            next_num = conn.execute(
                "SELECT COALESCE(MAX(number), 0) + 1 AS n FROM return_invoices"
            ).fetchone()["n"]
            conn.execute(
                "INSERT INTO return_invoices (number, client_id, warehouse_code, note)"
                " VALUES (?, ?, ?, ?)",
                (next_num, client_id, warehouse_code.strip(), note.strip()),
            )
            conn.commit()
            inv_id = conn.execute(
                "SELECT last_insert_rowid() AS id"
            ).fetchone()["id"]
            return True, "", inv_id
    except Exception as exc:
        return False, str(exc), 0


def return_invoice_cancel(invoice_id: int) -> tuple[bool, str]:
    try:
        with _connect() as conn:
            conn.execute(
                "UPDATE return_invoices SET status = 'CANCELLED'"
                " WHERE id = ? AND status = 'OPEN'",
                (invoice_id,),
            )
            conn.commit()
        return True, ""
    except Exception as exc:
        return False, str(exc)


def return_item_add(
    invoice_id: int, product_id: int, qty: float, unit_price: float
) -> tuple[bool, str]:
    try:
        with _connect() as conn:
            total = round(qty * unit_price, 4)
            conn.execute(
                "INSERT INTO return_items"
                " (invoice_id, product_id, qty, unit_price, total)"
                " VALUES (?, ?, ?, ?, ?)",
                (invoice_id, product_id, qty, unit_price, total),
            )
            conn.execute(
                "UPDATE return_invoices SET total = total + ? WHERE id = ?",
                (total, invoice_id),
            )
            conn.commit()
        return True, ""
    except Exception as exc:
        return False, str(exc)


def return_item_update(
    item_id: int, qty: float, unit_price: float
) -> tuple[bool, str]:
    try:
        with _connect() as conn:
            old = conn.execute(
                "SELECT total, invoice_id FROM return_items WHERE id = ?", (item_id,)
            ).fetchone()
            new_total = round(qty * unit_price, 4)
            if old:
                diff = new_total - float(old["total"])
                conn.execute(
                    "UPDATE return_invoices SET total = total + ? WHERE id = ?",
                    (diff, old["invoice_id"]),
                )
            conn.execute(
                "UPDATE return_items SET qty = ?, unit_price = ?, total = ? WHERE id = ?",
                (qty, unit_price, new_total, item_id),
            )
            conn.commit()
        return True, ""
    except Exception as exc:
        return False, str(exc)


def return_item_delete(item_id: int) -> tuple[bool, str]:
    try:
        with _connect() as conn:
            old = conn.execute(
                "SELECT total, invoice_id FROM return_items WHERE id = ?", (item_id,)
            ).fetchone()
            if old:
                conn.execute(
                    "UPDATE return_invoices SET total = total - ? WHERE id = ?",
                    (old["total"], old["invoice_id"]),
                )
            conn.execute("DELETE FROM return_items WHERE id = ?", (item_id,))
            conn.commit()
        return True, ""
    except Exception as exc:
        return False, str(exc)


def return_invoice_finish(invoice_id: int) -> tuple[bool, str]:
    try:
        with _connect() as conn:
            inv = conn.execute(
                "SELECT * FROM return_invoices WHERE id = ? AND status = 'OPEN'",
                (invoice_id,),
            ).fetchone()
            if not inv:
                return False, "invoice_not_found_or_not_open"
            items = conn.execute(
                "SELECT * FROM return_items WHERE invoice_id = ?", (invoice_id,)
            ).fetchall()
            if not items:
                return False, "no_items"
            warehouse = inv["warehouse_code"]
            for item in items:
                conn.execute(
                    """
                    INSERT INTO stock (warehouse_code, product_id, qty)
                    VALUES (?, ?, ?)
                    ON CONFLICT(warehouse_code, product_id)
                    DO UPDATE SET qty = qty + excluded.qty
                    """,
                    (warehouse, item["product_id"], item["qty"]),
                )
                conn.execute(
                    "INSERT INTO stock_ops"
                    " (op_type, source, warehouse_code, product_id, qty)"
                    " VALUES ('RETURN', 'CLIENT', ?, ?, ?)",
                    (warehouse, item["product_id"], item["qty"]),
                )
            conn.execute(
                "UPDATE return_invoices SET status = 'DONE' WHERE id = ?", (invoice_id,)
            )
            conn.commit()
        return True, ""
    except Exception as exc:
        return False, str(exc)


def return_invoice_get(invoice_id: int) -> Optional[dict[str, Any]]:
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT ri.*, cl.name AS client_name
            FROM return_invoices ri
            JOIN clients cl ON cl.id = ri.client_id
            WHERE ri.id = ?
            """,
            (invoice_id,),
        ).fetchone()
        return _row_to_dict(row) if row else None


def return_invoice_get_items(invoice_id: int) -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT ri.id, ri.qty, ri.unit_price, ri.total,
                   p.brand, p.model, p.name, p.id AS product_id
            FROM return_items ri
            JOIN products p ON p.id = ri.product_id
            WHERE ri.invoice_id = ?
            ORDER BY ri.id
            """,
            (invoice_id,),
        ).fetchall()
        return [_row_to_dict(r) for r in rows]


def list_return_invoices_done() -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT ri.*, cl.name AS client_name
            FROM return_invoices ri
            JOIN clients cl ON cl.id = ri.client_id
            WHERE ri.status = 'DONE'
            ORDER BY ri.number DESC
            """
        ).fetchall()
        return [_row_to_dict(r) for r in rows]


def get_return_invoice_items_for_edit(invoice_id: int) -> list[dict[str, Any]]:
    return return_invoice_get_items(invoice_id)


def update_return_invoice(
    invoice_id: int,
    client_id: int,
    warehouse_code: str,
    new_items: list[dict[str, Any]],
) -> tuple[bool, str]:
    try:
        with _connect() as conn:
            inv = conn.execute(
                "SELECT * FROM return_invoices WHERE id = ?", (invoice_id,)
            ).fetchone()
            if not inv:
                return False, "invoice_not_found"

            if inv["status"] == "DONE":
                old_items = conn.execute(
                    "SELECT product_id, qty FROM return_items WHERE invoice_id = ?",
                    (invoice_id,),
                ).fetchall()
                for oi in old_items:
                    conn.execute(
                        "UPDATE stock SET qty = qty - ?"
                        " WHERE warehouse_code = ? AND product_id = ?",
                        (oi["qty"], inv["warehouse_code"], oi["product_id"]),
                    )

            total = 0.0
            conn.execute(
                "UPDATE return_invoices"
                " SET client_id = ?, warehouse_code = ?, total = 0 WHERE id = ?",
                (client_id, warehouse_code.strip(), invoice_id),
            )
            conn.execute("DELETE FROM return_items WHERE invoice_id = ?", (invoice_id,))

            for item in new_items:
                qty = float(item["qty"])
                up = float(item["unit_price"])
                item_total = round(qty * up, 4)
                total += item_total
                conn.execute(
                    "INSERT INTO return_items"
                    " (invoice_id, product_id, qty, unit_price, total)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (invoice_id, item["product_id"], qty, up, item_total),
                )
                if inv["status"] == "DONE":
                    conn.execute(
                        """
                        INSERT INTO stock (warehouse_code, product_id, qty)
                        VALUES (?, ?, ?)
                        ON CONFLICT(warehouse_code, product_id)
                        DO UPDATE SET qty = qty + excluded.qty
                        """,
                        (warehouse_code, item["product_id"], qty),
                    )

            conn.execute(
                "UPDATE return_invoices SET total = ? WHERE id = ?", (total, invoice_id)
            )
            conn.commit()
        return True, ""
    except Exception as exc:
        return False, str(exc)


# ── History ───────────────────────────────────────────────────────────────────


def _build_history_event(
    d: dict[str, Any],
    ev_type: str,
    counterparty_key: str,
    warehouse_key: str,
    view_url: str,
    download_url: str,
) -> dict[str, Any]:
    return {
        "dt": d.get("created_at", ""),
        "type": ev_type,
        "ref": str(d.get("number", "")),
        "counterparty": d.get(counterparty_key) or "",
        "warehouse": d.get(warehouse_key) or "",
        "brand": d.get("brand", ""),
        "model": d.get("model", ""),
        "name": d.get("name", ""),
        "product_id": d.get("product_id"),
        "qty": float(d.get("qty") or 0),
        "unit_price": float(d.get("unit_price") or 0),
        "total": float(d.get("total") or 0),
        "view_url": view_url,
        "download_url": download_url,
    }


def _build_history_event_sale(d: dict[str, Any]) -> dict[str, Any]:
    number = d["number"]
    return _build_history_event(
        d, "SALE", "client_name", "warehouse_code",
        f"/invoices/sale/{number}/edit", f"/sale/xlsx?n={number}",
    )


def _build_history_event_receive(d: dict[str, Any]) -> dict[str, Any]:
    invoice_id, number = d["invoice_id"], d["number"]
    return _build_history_event(
        d, "RECEIVE", "supplier", "destination_warehouse",
        f"/invoices/receive/{invoice_id}/edit", f"/receive/xlsx?n={number}",
    )


def _build_history_event_return(d: dict[str, Any]) -> dict[str, Any]:
    invoice_id, number = d["invoice_id"], d["number"]
    return _build_history_event(
        d, "RETURN", "client_name", "warehouse_code",
        f"/invoices/return/{invoice_id}/edit", f"/return/xlsx?n={invoice_id}",
    )


def list_history(q: str = "", limit: int = 500) -> list[dict[str, Any]]:
    like = f"%{q}%" if q else None
    with _connect() as conn:
        events: list[dict[str, Any]] = []

        # ── SALE events ──────────────────────────────────────────────────────
        sale_clauses: list[str] = []
        sale_params: list[Any] = []
        if like:
            sale_clauses.append(
                "(p.brand LIKE ? OR p.model LIKE ? OR p.name LIKE ?"
                " OR cl.name LIKE ? OR c.warehouse_code LIKE ?)"
            )
            sale_params = [like, like, like, like, like]
        sale_where = ("WHERE " + " AND ".join(sale_clauses)) if sale_clauses else ""
        for r in conn.execute(
            f"""
            SELECT i.number, i.created_at, ci.qty, ci.unit_price, ci.total,
                   p.brand, p.model, p.name, p.id AS product_id,
                   cl.name AS client_name, c.warehouse_code
            FROM cart_items ci
            JOIN carts c ON c.id = ci.cart_id
            JOIN invoices i ON i.cart_id = c.id
            JOIN products p ON p.id = ci.product_id
            LEFT JOIN clients cl ON cl.id = c.client_id
            {sale_where}
            ORDER BY i.created_at DESC
            LIMIT ?
            """,
            sale_params + [limit],
        ).fetchall():
            events.append(_build_history_event_sale(_row_to_dict(r)))

        # ── RECEIVE events ───────────────────────────────────────────────────
        recv_clauses: list[str] = ["ri_inv.status = 'DONE'"]
        recv_params: list[Any] = []
        if like:
            recv_clauses.append(
                "(p.brand LIKE ? OR p.model LIKE ? OR p.name LIKE ?"
                " OR ri_inv.supplier LIKE ? OR ri_inv.destination_warehouse LIKE ?)"
            )
            recv_params = [like, like, like, like, like]
        recv_where = "WHERE " + " AND ".join(recv_clauses)
        for r in conn.execute(
            f"""
            SELECT ri_inv.id AS invoice_id, ri_inv.number, ri_inv.created_at,
                   ri_inv.supplier, ri_inv.destination_warehouse,
                   ri.qty, ri.purchase_price AS unit_price, ri.total,
                   p.brand, p.model, p.name, p.id AS product_id
            FROM receive_items ri
            JOIN receive_invoices ri_inv ON ri_inv.id = ri.invoice_id
            JOIN products p ON p.id = ri.product_id
            {recv_where}
            ORDER BY ri_inv.created_at DESC
            LIMIT ?
            """,
            recv_params + [limit],
        ).fetchall():
            events.append(_build_history_event_receive(_row_to_dict(r)))

        # ── RETURN events ────────────────────────────────────────────────────
        ret_clauses: list[str] = ["ret_inv.status = 'DONE'"]
        ret_params: list[Any] = []
        if like:
            ret_clauses.append(
                "(p.brand LIKE ? OR p.model LIKE ? OR p.name LIKE ?"
                " OR cl.name LIKE ? OR ret_inv.warehouse_code LIKE ?)"
            )
            ret_params = [like, like, like, like, like]
        ret_where = "WHERE " + " AND ".join(ret_clauses)
        for r in conn.execute(
            f"""
            SELECT ret_inv.id AS invoice_id, ret_inv.number, ret_inv.created_at,
                   ret_inv.warehouse_code, cl.name AS client_name,
                   ret_it.qty, ret_it.unit_price, ret_it.total,
                   p.brand, p.model, p.name, p.id AS product_id
            FROM return_items ret_it
            JOIN return_invoices ret_inv ON ret_inv.id = ret_it.invoice_id
            JOIN products p ON p.id = ret_it.product_id
            LEFT JOIN clients cl ON cl.id = ret_inv.client_id
            {ret_where}
            ORDER BY ret_inv.created_at DESC
            LIMIT ?
            """,
            ret_params + [limit],
        ).fetchall():
            events.append(_build_history_event_return(_row_to_dict(r)))

    events.sort(key=lambda x: x.get("dt", ""), reverse=True)
    return events[:limit]


def list_history_by_product(product_id: int) -> list[dict[str, Any]]:
    with _connect() as conn:
        events: list[dict[str, Any]] = []

        # SALE
        for r in conn.execute(
            """
            SELECT i.number, i.created_at, ci.qty, ci.unit_price, ci.total,
                   p.brand, p.model, p.name, p.id AS product_id,
                   cl.name AS client_name, c.warehouse_code
            FROM cart_items ci
            JOIN carts c ON c.id = ci.cart_id
            JOIN invoices i ON i.cart_id = c.id
            JOIN products p ON p.id = ci.product_id
            LEFT JOIN clients cl ON cl.id = c.client_id
            WHERE ci.product_id = ?
            ORDER BY i.created_at DESC
            """,
            (product_id,),
        ).fetchall():
            events.append(_build_history_event_sale(_row_to_dict(r)))

        # RECEIVE
        for r in conn.execute(
            """
            SELECT ri_inv.id AS invoice_id, ri_inv.number, ri_inv.created_at,
                   ri_inv.supplier, ri_inv.destination_warehouse,
                   ri.qty, ri.purchase_price AS unit_price, ri.total,
                   p.brand, p.model, p.name, p.id AS product_id
            FROM receive_items ri
            JOIN receive_invoices ri_inv ON ri_inv.id = ri.invoice_id
            JOIN products p ON p.id = ri.product_id
            WHERE ri.product_id = ? AND ri_inv.status = 'DONE'
            ORDER BY ri_inv.created_at DESC
            """,
            (product_id,),
        ).fetchall():
            events.append(_build_history_event_receive(_row_to_dict(r)))

        # RETURN
        for r in conn.execute(
            """
            SELECT ret_inv.id AS invoice_id, ret_inv.number, ret_inv.created_at,
                   ret_inv.warehouse_code, cl.name AS client_name,
                   ret_it.qty, ret_it.unit_price, ret_it.total,
                   p.brand, p.model, p.name, p.id AS product_id
            FROM return_items ret_it
            JOIN return_invoices ret_inv ON ret_inv.id = ret_it.invoice_id
            JOIN products p ON p.id = ret_it.product_id
            LEFT JOIN clients cl ON cl.id = ret_inv.client_id
            WHERE ret_it.product_id = ? AND ret_inv.status = 'DONE'
            ORDER BY ret_inv.created_at DESC
            """,
            (product_id,),
        ).fetchall():
            events.append(_build_history_event_return(_row_to_dict(r)))

    events.sort(key=lambda x: x.get("dt", ""), reverse=True)
    return events


# ── Settings ──────────────────────────────────────────────────────────────────


def get_setting(key: str, default: str = "") -> str:
    with _connect() as conn:
        row = conn.execute(
            "SELECT value FROM site_settings WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO site_settings (key, value) VALUES (?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        conn.commit()


# ── Sessions ──────────────────────────────────────────────────────────────────


def create_session(token: str, expires_at: str) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO site_sessions (token, expires_at) VALUES (?, ?)",
            (token, expires_at),
        )
        conn.commit()


def is_valid_session(token: str) -> bool:
    with _connect() as conn:
        row = conn.execute(
            "SELECT token FROM site_sessions WHERE token = ?"
            "  AND expires_at > datetime('now')",
            (token,),
        ).fetchone()
        return row is not None


def delete_session(token: str) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM site_sessions WHERE token = ?", (token,))
        conn.commit()


def delete_all_sessions() -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM site_sessions")
        conn.commit()


def purge_expired_sessions() -> None:
    with _connect() as conn:
        conn.execute(
            "DELETE FROM site_sessions WHERE expires_at <= datetime('now')"
        )
        conn.commit()


# ── Price tokens ──────────────────────────────────────────────────────────────


def _hash_token(plain: str) -> str:
    return hashlib.sha256(plain.encode()).hexdigest()


def create_price_token(
    label: str = "", mode: str = "SIMPLE"
) -> tuple[str, dict[str, Any]]:
    """Create a new price token. Returns (plain_token, row_dict)."""
    plain = secrets.token_urlsafe(32)
    token_hash = _hash_token(plain)
    with _connect() as conn:
        _ensure_price_tokens_table(conn)
        _ensure_price_tokens_mode_column(conn)
        _ensure_price_tokens_plain_token_column(conn)
        conn.execute(
            "INSERT INTO price_tokens (label, token_hash, mode, plain_token) VALUES (?, ?, ?, ?)",
            (label.strip(), token_hash, (mode or "SIMPLE").upper(), plain),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM price_tokens WHERE token_hash = ?", (token_hash,)
        ).fetchone()
        return plain, _row_to_dict(row)


def list_price_tokens() -> list[dict[str, Any]]:
    with _connect() as conn:
        _ensure_price_tokens_table(conn)
        _ensure_price_tokens_mode_column(conn)
        rows = conn.execute(
            "SELECT * FROM price_tokens ORDER BY id DESC"
        ).fetchall()
        return [_row_to_dict(r) for r in rows]


def validate_price_token(plain: str) -> Optional[dict[str, Any]]:
    token_hash = _hash_token(plain)
    with _connect() as conn:
        _ensure_price_tokens_table(conn)
        row = conn.execute(
            "SELECT * FROM price_tokens WHERE token_hash = ? AND revoked = 0",
            (token_hash,),
        ).fetchone()
        return _row_to_dict(row) if row else None


def touch_price_token(token_id: int) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE price_tokens"
            " SET last_used_at = datetime('now','localtime') WHERE id = ?",
            (token_id,),
        )
        conn.commit()


def bind_price_token_device(token_id: int, device_id: str) -> None:
    """Bind a price token to a device UUID (only if not already bound)."""
    with _connect() as conn:
        conn.execute(
            "UPDATE price_tokens SET device_id = ? WHERE id = ? AND device_id IS NULL",
            (device_id, token_id),
        )
        conn.commit()


def set_price_token_mode(token_id: int, mode: str) -> tuple[bool, str]:
    """Set the display mode (SIMPLE or FULL) for a price token."""
    try:
        with _connect() as conn:
            _ensure_price_tokens_mode_column(conn)
            conn.execute(
                "UPDATE price_tokens SET mode = ? WHERE id = ?",
                ((mode or "SIMPLE").upper(), token_id),
            )
            conn.commit()
        return True, ""
    except Exception as exc:
        return False, str(exc)


def revoke_price_token(token_id: int) -> tuple[bool, str]:
    try:
        with _connect() as conn:
            conn.execute(
                "UPDATE price_tokens SET revoked = 1, device_id = NULL WHERE id = ?",
                (token_id,),
            )
            conn.commit()
        return True, ""
    except Exception as exc:
        return False, str(exc)


def delete_price_token(token_id: int) -> tuple[bool, str]:
    try:
        with _connect() as conn:
            conn.execute("DELETE FROM price_tokens WHERE id = ?", (token_id,))
            conn.commit()
        return True, ""
    except Exception as exc:
        return False, str(exc)


def search_products_for_price(
    q: str, limit: int = 30, mode: str = "SIMPLE"
) -> list[dict[str, Any]]:
    """Search non-archived products for Pocket Price by brand/model/name/barcode.

    Args:
        q: Search query string.
        limit: Maximum number of results (default 30).
        mode: ``"SIMPLE"`` returns only the retail +25% price tier;
              ``"FULL"`` returns all price tiers including the wholesale price.

    Returns:
        List of product dicts with computed price fields filtered by *mode*.
    """
    with _connect() as conn:
        like = f"%{q}%"
        rows = conn.execute(
            """
            SELECT id, brand, model, name, wh_price, barcode, note
            FROM products
            WHERE archived = 0
              AND (brand LIKE ? OR model LIKE ? OR name LIKE ? OR barcode LIKE ?)
            ORDER BY brand, model
            LIMIT ?
            """,
            (like, like, like, like, limit),
        ).fetchall()
        return [_apply_price_mode(dict(r), mode) for r in rows]

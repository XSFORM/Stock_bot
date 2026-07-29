from __future__ import annotations

import hashlib
import os
import secrets
import sqlite3
from decimal import Decimal, ROUND_HALF_EVEN
from pathlib import Path
from typing import Any, Optional

from app.utils.money import calc_document_total, calc_line_total

# ── DB path ───────────────────────────────────────────────────────────────────

_ROOT = Path(__file__).resolve().parents[3]  # Stock_bot root
DB_PATH = Path(os.getenv("DB_PATH", str(_ROOT / "data" / "stock.db")))

_SCHEMA_SQL = Path(__file__).resolve().parent / "schema.sql"
_CENT = Decimal("0.01")


def _normalize_unit_price(value: float) -> float:
    return float(Decimal(str(value)).quantize(_CENT, rounding=ROUND_HALF_EVEN))


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
    # Unique index on non-empty barcodes (partial index supported in SQLite >= 3.8.9)
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_products_barcode_unique"
        " ON products(barcode) WHERE barcode != ''"
    )


def _ensure_clients_archived(conn: sqlite3.Connection) -> None:
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(clients)")}
    if "archived" not in cols:
        conn.execute("ALTER TABLE clients ADD COLUMN archived INTEGER NOT NULL DEFAULT 0")


def _ensure_clients_client_type_column(conn: sqlite3.Connection) -> None:
    """Add client_type column: 'wholesale' (default) or 'retail'."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(clients)")}
    if "client_type" not in cols:
        conn.execute(
            "ALTER TABLE clients ADD COLUMN client_type TEXT NOT NULL DEFAULT 'wholesale'"
        )


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


def _ensure_price_tokens_show_qty_column(conn: sqlite3.Connection) -> None:
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(price_tokens)")}
    if "show_qty" not in cols:
        conn.execute(
            "ALTER TABLE price_tokens ADD COLUMN show_qty INTEGER NOT NULL DEFAULT 0"
        )


def _ensure_price_tokens_show_buy_price_column(conn: sqlite3.Connection) -> None:
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(price_tokens)")}
    if "show_buy_price" not in cols:
        conn.execute(
            "ALTER TABLE price_tokens ADD COLUMN show_buy_price INTEGER NOT NULL DEFAULT 0"
        )


def _ensure_catalog_tokens_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS catalog_tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            label TEXT NOT NULL DEFAULT '',
            token_hash TEXT NOT NULL UNIQUE,
            plain_token TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            last_used_at TEXT,
            revoked INTEGER NOT NULL DEFAULT 0,
            device_id TEXT
        )
    """)


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


def _ensure_suppliers_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS suppliers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            phone TEXT NOT NULL DEFAULT '',
            note TEXT NOT NULL DEFAULT '',
            archived INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        )
        """
    )


def _ensure_supplier_ledger_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS supplier_ledger (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            supplier_id INTEGER NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            amount REAL NOT NULL,
            note TEXT NOT NULL DEFAULT '',
            FOREIGN KEY (supplier_id) REFERENCES suppliers(id) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_supplier_ledger_supplier ON supplier_ledger(supplier_id)"
    )


def _ensure_receive_invoices_supplier_id_column(conn: sqlite3.Connection) -> None:
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(receive_invoices)")}
    if "supplier_id" not in cols:
        conn.execute("ALTER TABLE receive_invoices ADD COLUMN supplier_id INTEGER")


def _ensure_return_items_free_columns(conn: sqlite3.Connection) -> None:
    """Migrate return_items to support free/manual line items (product_id nullable)."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(return_items)")}
    if "free_line" in cols:
        return  # already migrated

    conn.execute("""
        CREATE TABLE IF NOT EXISTS return_items_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            invoice_id INTEGER NOT NULL,
            product_id INTEGER,
            free_line INTEGER NOT NULL DEFAULT 0,
            free_name TEXT NOT NULL DEFAULT '',
            qty REAL NOT NULL,
            unit_price REAL NOT NULL,
            total REAL NOT NULL,
            FOREIGN KEY (invoice_id) REFERENCES return_invoices(id) ON DELETE CASCADE,
            FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE SET NULL
        )
    """)
    conn.execute("""
        INSERT INTO return_items_new (id, invoice_id, product_id,
                                       free_line, free_name,
                                       qty, unit_price, total)
        SELECT id, invoice_id, product_id, 0, '', qty, unit_price, total
        FROM return_items
    """)
    conn.execute("DROP TABLE return_items")
    conn.execute("ALTER TABLE return_items_new RENAME TO return_items")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_return_items_invoice ON return_items(invoice_id)")


def _ensure_stock_ops_note_column(conn: sqlite3.Connection) -> None:
    """Phase 4 — add optional note to stock_ops for inventory adjustments."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(stock_ops)")}
    if "note" not in cols:
        conn.execute("ALTER TABLE stock_ops ADD COLUMN note TEXT NOT NULL DEFAULT ''")


# ── Phase 5 — Expenses ─────────────────────────────────────────────────────

# Fixed set — used to validate the kind column and to filter reports.
EXPENSE_KINDS = ("business", "personal")


def _ensure_expense_categories_table(conn: sqlite3.Connection) -> None:
    """
    Phase 5 — categories dictionary for expenses.

    Each category is tagged as 'business' or 'personal' so the finance
    report can show business net profit separately from wallet net.
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS expense_categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            kind TEXT NOT NULL DEFAULT 'business',
            archived INTEGER NOT NULL DEFAULT 0
        )
        """
    )


def _ensure_expenses_table(conn: sqlite3.Connection) -> None:
    """
    Phase 5 — actual expense entries.

    Store money in USD (single-currency by user request). date is the day
    the expense happened; created_at is when the record was entered.
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS expenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            category_id INTEGER NOT NULL,
            amount_usd REAL NOT NULL,
            note TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            FOREIGN KEY (category_id) REFERENCES expense_categories(id) ON DELETE RESTRICT
        )
        """
    )
    # Helpful index for period filters.
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_expenses_date ON expenses(date)"
    )


def _seed_default_expense_categories(conn: sqlite3.Connection) -> None:
    """
    First-run seed. Only inserts categories that don't exist by name.
    Runs on every init_db but is a no-op after the first time.
    """
    defaults = [
        # Business
        ("Аренда",              "business"),
        ("Зарплата",            "business"),
        ("Налоги/Госторг",      "business"),
        ("Коммуналка",          "business"),
        ("Транспорт",           "business"),
        ("Связь/Интернет",      "business"),
        ("Реклама",             "business"),
        ("Прочие бизнес",       "business"),
        # Personal
        ("Личные покупки",      "personal"),
        ("Семья",               "personal"),
        ("Прочие личные",       "personal"),
    ]
    for name, kind in defaults:
        conn.execute(
            "INSERT OR IGNORE INTO expense_categories (name, kind) VALUES (?, ?)",
            (name, kind),
        )


def _ensure_items_cost_price_column(conn: sqlite3.Connection) -> None:
    """
    Phase 1 — snapshot of cost price at the moment of sale/return.

    We keep the historical purchase cost (products.wh_price at the time of
    the operation) on each cart_items / return_items row. This lets us
    compute historically accurate gross profit later, without being
    affected by future changes to products.wh_price (which is overwritten
    on every receive / Pocket Catalog update).

    IMPORTANT: default value 0 means "no data" (row created before this
    migration, or free-line item without a product). Reports must EXCLUDE
    such rows from profit calculations, NEVER treat 0 as real cost.
    """
    for table in ("cart_items", "return_items"):
        cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        if "cost_price" not in cols:
            conn.execute(
                f"ALTER TABLE {table} ADD COLUMN cost_price REAL NOT NULL DEFAULT 0"
            )


def _ensure_cart_items_free_columns(conn: sqlite3.Connection) -> None:
    """Migrate cart_items to support free/manual line items (product_id nullable)."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(cart_items)")}
    if "free_line" in cols:
        return  # already migrated

    # Recreate table with nullable product_id + free_line + free_name
    conn.execute("""
        CREATE TABLE IF NOT EXISTS cart_items_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cart_id INTEGER NOT NULL,
            product_id INTEGER,
            free_line INTEGER NOT NULL DEFAULT 0,
            free_name TEXT NOT NULL DEFAULT '',
            qty REAL NOT NULL,
            price_mode TEXT NOT NULL DEFAULT 'custom',
            unit_price REAL NOT NULL,
            total REAL NOT NULL,
            FOREIGN KEY (cart_id) REFERENCES carts(id) ON DELETE CASCADE,
            FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE SET NULL
        )
    """)
    conn.execute("""
        INSERT INTO cart_items_new (id, cart_id, product_id, free_line, free_name,
                                    qty, price_mode, unit_price, total)
        SELECT id, cart_id, product_id, 0, '', qty, price_mode, unit_price, total
        FROM cart_items
    """)
    conn.execute("DROP TABLE cart_items")
    conn.execute("ALTER TABLE cart_items_new RENAME TO cart_items")


def _backfill_suppliers_from_receive_invoices(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO suppliers (name)
        SELECT DISTINCT TRIM(supplier)
        FROM receive_invoices
        WHERE TRIM(COALESCE(supplier, '')) != ''
        """
    )
    conn.execute(
        """
        UPDATE receive_invoices
        SET supplier_id = (
            SELECT s.id
            FROM suppliers s
            WHERE s.name = TRIM(receive_invoices.supplier)
            LIMIT 1
        )
        WHERE supplier_id IS NULL
          AND TRIM(COALESCE(supplier, '')) != ''
          AND EXISTS (
              SELECT 1
              FROM suppliers s
              WHERE s.name = TRIM(receive_invoices.supplier)
          )
        """
    )


def init_db() -> None:
    with _connect() as conn:
        conn.executescript(_SCHEMA_SQL.read_text())
        _ensure_products_extra_cols(conn)
        _ensure_clients_archived(conn)
        _ensure_clients_client_type_column(conn)
        _ensure_price_tokens_table(conn)
        _ensure_price_tokens_last_used_column(conn)
        _ensure_price_tokens_mode_column(conn)
        _ensure_price_tokens_plain_token_column(conn)
        _ensure_price_tokens_device_id_column(conn)
        _ensure_price_tokens_show_qty_column(conn)
        _ensure_price_tokens_show_buy_price_column(conn)
        _ensure_catalog_tokens_table(conn)
        _ensure_receive_invoices_total(conn)
        _ensure_suppliers_table(conn)
        _ensure_supplier_ledger_table(conn)
        _ensure_receive_invoices_supplier_id_column(conn)
        _backfill_suppliers_from_receive_invoices(conn)
        _ensure_cart_items_free_columns(conn)
        _ensure_return_items_free_columns(conn)
        _ensure_items_cost_price_column(conn)
        _ensure_stock_ops_note_column(conn)
        _ensure_expense_categories_table(conn)
        _ensure_expenses_table(conn)
        _seed_default_expense_categories(conn)
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
    """Add computed price fields and filter by mode.

    SIMPLE mode: returns only the safe minimal subset for public display
    (id, brand, model, name, barcode, price_wh25).  Internal/extended fields
    such as price_wh10 and note are stripped.

    FULL mode: returns all of the above plus price_wh10 and note.
    NOTE: ``price_wh10`` / ``price_wh25`` are legacy Pocket Price API field names;
    they now carry primary/secondary active markup prices (not fixed 10%/25%).

    In both modes the raw wh_price is always removed.
    """
    wh = float(product.get("wh_price", 0) or 0)
    presets = get_sale_markup_presets()
    if not presets:
        presets = list(_FALLBACK_MARKUP_PRESETS)
    # Pocket Price shows at most two compact sell prices, so we use the
    # lowest and highest active markups.
    pocket_markups = [presets[0]]
    if len(presets) > 1 and presets[-1] != presets[0]:
        pocket_markups.append(presets[-1])
    is_full = (mode or "SIMPLE").upper() == "FULL"
    primary_markup = pocket_markups[0]
    highest_markup = pocket_markups[-1]

    product["price_wh10"] = round(wh * (1 + primary_markup / 100.0), 4)
    if is_full:
        if len(pocket_markups) > 1:
            product["price_wh25"] = round(wh * (1 + highest_markup / 100.0), 4)
    else:
        # SIMPLE should always expose one sell price via legacy key `price_wh25`.
        product["price_wh25"] = round(wh * (1 + highest_markup / 100.0), 4)
    product.pop("wh_price", None)
    if not is_full:
        product.pop("price_wh10", None)
        product.pop("note", None)
    return product


# ── Products ──────────────────────────────────────────────────────────────────


def list_products(
    include_archived: bool = False, search: Optional[str] = None
) -> list[dict[str, Any]]:
    with _connect() as conn:
        conditions: list[str] = []
        params: list[Any] = []
        if not include_archived:
            conditions.append("archived = 0")
        tokens = [t.strip() for t in (search or "").split() if t.strip()]
        for token in tokens:
            like = f"%{token}%"
            conditions.append(
                "(brand LIKE ? OR model LIKE ? OR name LIKE ? OR barcode LIKE ?)"
            )
            params.extend([like, like, like, like])
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        rows = conn.execute(
            f"SELECT * FROM products {where} ORDER BY brand, model", params
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
    brand: str = "",
) -> tuple[bool, str]:
    try:
        with _connect() as conn:
            if brand.strip():
                row = conn.execute(
                    "SELECT name FROM brands WHERE name = ?", (brand.strip(),)
                ).fetchone()
                if not row:
                    return False, "brand_not_found"
                conn.execute(
                    "UPDATE products SET barcode = ?, wh_price = ?, model = ?, name = ?, brand = ? WHERE id = ?",
                    (barcode.strip(), wh_price, model.strip(), name.strip(), brand.strip(), product_id),
                )
            else:
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
    """
    Product autocomplete for /sale, /return, /receive, /invoice edit forms.

    Same filtering rules as search_products_for_price (Pocket Price):
    - barcode is matched *prefix-only* and only for numeric queries of
      length >= 6, otherwise short number searches (like "80" or "8081")
      would match anything with those digits somewhere inside an EAN-13;
    - CASE-based priority in ORDER BY makes sure exact/prefix model
      matches survive the LIMIT cut.
    """
    with _connect() as conn:
        q_stripped = (q or "").strip()
        like_all    = f"%{q_stripped}%"
        like_prefix = f"{q_stripped}%"
        include_barcode = q_stripped.isdigit() and len(q_stripped) >= 6

        if include_barcode:
            sql = (
                "SELECT *,"
                "       CASE"
                "         WHEN LOWER(model) = LOWER(?)    THEN 1"
                "         WHEN LOWER(model) LIKE LOWER(?) THEN 2"
                "         WHEN LOWER(model) LIKE LOWER(?) THEN 3"
                "         WHEN LOWER(brand) LIKE LOWER(?) THEN 4"
                "         WHEN LOWER(name)  LIKE LOWER(?) THEN 4"
                "         WHEN barcode LIKE ?             THEN 5"
                "         ELSE 9"
                "       END AS _prio"
                " FROM products"
                " WHERE archived = 0"
                "   AND (   LOWER(brand) LIKE LOWER(?)"
                "        OR LOWER(model) LIKE LOWER(?)"
                "        OR LOWER(name)  LIKE LOWER(?)"
                "        OR barcode LIKE ?)"
                " ORDER BY _prio, brand, model"
                " LIMIT ?"
            )
            params = (
                q_stripped, like_prefix, like_all,
                like_all, like_all, like_prefix,
                like_all, like_all, like_all, like_prefix,
                limit,
            )
        else:
            sql = (
                "SELECT *,"
                "       CASE"
                "         WHEN LOWER(model) = LOWER(?)    THEN 1"
                "         WHEN LOWER(model) LIKE LOWER(?) THEN 2"
                "         WHEN LOWER(model) LIKE LOWER(?) THEN 3"
                "         WHEN LOWER(brand) LIKE LOWER(?) THEN 4"
                "         WHEN LOWER(name)  LIKE LOWER(?) THEN 4"
                "         ELSE 9"
                "       END AS _prio"
                " FROM products"
                " WHERE archived = 0"
                "   AND (   LOWER(brand) LIKE LOWER(?)"
                "        OR LOWER(model) LIKE LOWER(?)"
                "        OR LOWER(name)  LIKE LOWER(?))"
                " ORDER BY _prio, brand, model"
                " LIMIT ?"
            )
            params = (
                q_stripped, like_prefix, like_all,
                like_all, like_all,
                like_all, like_all, like_all,
                limit,
            )
        rows = conn.execute(sql, params).fetchall()
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


def get_product_by_barcode_for_scan(barcode: str) -> Optional[dict[str, Any]]:
    """Return a product dict suitable for the mobile scan API (id, brand, model, name, purchase_price, barcode)."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT id, brand, model, name, wh_price, barcode"
            " FROM products WHERE barcode = ? AND archived = 0 LIMIT 1",
            (barcode.strip(),),
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["purchase_price"] = float(d.pop("wh_price", 0) or 0)
        return d


def create_product_with_barcode(
    brand: str,
    model: str,
    name: str,
    purchase_price: float,
    barcode: str,
) -> tuple[bool, str, int]:
    """Create a catalog-only product (no stock movement). Returns (ok, error_msg, product_id)."""
    barcode = barcode.strip()
    if not barcode:
        return False, "barcode_empty", 0
    # Check uniqueness first to give a clear error before hitting the DB constraint
    existing = get_product_by_barcode_for_scan(barcode)
    if existing:
        return False, "barcode_exists", existing["id"]
    try:
        with _connect() as conn:
            conn.execute(
                "INSERT INTO products (brand, model, name, barcode, note, wh_price)"
                " VALUES (?, ?, ?, ?, '', ?)",
                (brand.strip(), model.strip(), name.strip(), barcode, purchase_price),
            )
            conn.commit()
            row = conn.execute(
                "SELECT id FROM products WHERE barcode = ?",
                (barcode,),
            ).fetchone()
            if not row:
                return False, "product_not_found_after_insert", 0
            return True, "", row["id"]
    except Exception as exc:
        return False, str(exc), 0


def update_product_purchase_price(product_id: int, purchase_price: float) -> tuple[bool, str]:
    """Update purchase (wh) price for a product by id."""
    try:
        with _connect() as conn:
            conn.execute(
                "UPDATE products SET wh_price = ? WHERE id = ?",
                (purchase_price, product_id),
            )
            conn.commit()
        return True, ""
    except Exception as exc:
        return False, str(exc)


# ── Stock ─────────────────────────────────────────────────────────────────────


def get_stock(
    warehouse: Optional[str] = None,
    q: Optional[str] = None,
    include_archived: bool = False,
    sort_by: str = "qty_asc",
) -> list[dict[str, Any]]:
    _SORT_ORDERS = {
        "qty_asc": "s.qty ASC, s.warehouse_code, p.brand, p.model",
        "qty_desc": "s.qty DESC, s.warehouse_code, p.brand, p.model",
        "model": "s.warehouse_code, p.brand, p.model",
    }
    order_clause = _SORT_ORDERS.get(sort_by, _SORT_ORDERS["qty_asc"])
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
            ORDER BY {order_clause}
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
    name: str, phone: str = "", note: str = "", client_type: str = "wholesale"
) -> tuple[bool, str]:
    """Add a client. client_type: 'wholesale' (default) or 'retail'."""
    ctype = (client_type or "wholesale").strip().lower()
    if ctype not in ("wholesale", "retail"):
        ctype = "wholesale"
    try:
        with _connect() as conn:
            conn.execute(
                "INSERT INTO clients (name, phone, note, client_type) VALUES (?, ?, ?, ?)",
                (name.strip(), phone.strip(), note.strip(), ctype),
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
    client_id: int, name: str, phone: str, note: str,
    client_type: str | None = None,
) -> tuple[bool, str]:
    """Update client. If client_type is None - keep current value."""
    try:
        with _connect() as conn:
            if client_type is not None:
                ctype = (client_type or "wholesale").strip().lower()
                if ctype not in ("wholesale", "retail"):
                    ctype = "wholesale"
                conn.execute(
                    "UPDATE clients SET name = ?, phone = ?, note = ?, client_type = ? WHERE id = ?",
                    (name.strip(), phone.strip(), note.strip(), ctype, client_id),
                )
            else:
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


def delete_client_ledger_entry(entry_id: int, client_id: int) -> tuple[bool, str]:
    """
    Delete a single row from client_ledger by its id.

    Verifies the entry belongs to the given client_id (security: prevents
    deleting another client's row by guessing the id). Only ledger
    adjustments are deletable - invoice/return events come from other tables
    and are not affected by this function.
    """
    try:
        with _connect() as conn:
            row = conn.execute(
                "SELECT client_id FROM client_ledger WHERE id = ?", (entry_id,)
            ).fetchone()
            if row is None:
                return False, "not_found"
            if int(row["client_id"]) != int(client_id):
                return False, "wrong_client"
            conn.execute("DELETE FROM client_ledger WHERE id = ?", (entry_id,))
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
        inv_items = conn.execute(
            """
            SELECT ci.unit_price, ci.qty
            FROM invoices i
            JOIN carts c ON c.id = i.cart_id
            JOIN cart_items ci ON ci.cart_id = c.id
            WHERE c.client_id = ? AND c.status = 'CLOSED'
            """,
            (client_id,),
        ).fetchall()
        inv_total = calc_document_total(list(inv_items), "unit_price")

        ledger_row = conn.execute(
            "SELECT COALESCE(SUM(amount), 0) AS paid FROM client_ledger WHERE client_id = ?",
            (client_id,),
        ).fetchone()
        paid = float(ledger_row["paid"]) if ledger_row else 0.0

        return round(inv_total - paid, 2)


# ── Bot helpers (Phase 6 — Telegram redesign) ───────────────────────────────

def get_recent_active_clients(days: int = 7, limit: int = 8) -> list[dict[str, Any]]:
    """
    Clients touched by a sale or a ledger entry in the last `days` days,
    ordered by most-recent activity first. Used to fill the bot's main
    menu with quick-access buttons.

    Each returned dict has: id, name, phone, balance, last_activity_at.
    Archived clients are excluded.
    """
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT c.id, c.name, c.phone,
                   MAX(activity_at) AS last_activity_at
            FROM clients c
            JOIN (
                SELECT client_id, MAX(created_at) AS activity_at
                FROM client_ledger
                WHERE created_at >= datetime('now','localtime', ?)
                GROUP BY client_id

                UNION ALL

                SELECT ca.client_id, MAX(ca.created_at) AS activity_at
                FROM carts ca
                WHERE ca.status = 'CLOSED'
                  AND ca.created_at >= datetime('now','localtime', ?)
                GROUP BY ca.client_id
            ) act ON act.client_id = c.id
            WHERE COALESCE(c.archived, 0) = 0
            GROUP BY c.id, c.name, c.phone
            ORDER BY last_activity_at DESC
            LIMIT ?
            """,
            (f"-{int(days)} days", f"-{int(days)} days", int(limit)),
        ).fetchall()
    result = []
    for r in rows:
        d = _row_to_dict(r)
        d["balance"] = get_client_balance(d["id"])
        result.append(d)
    return result


def get_top_debtors(limit: int = 10) -> list[dict[str, Any]]:
    """
    Non-archived clients with balance > 0, ordered by balance desc.
    Adds days_since_last (from get_client_payment_stats) so the bot can
    highlight silent debtors.
    """
    clients = list_clients_with_balance(include_archived=False)
    debtors = [c for c in clients if float(c.get("balance", 0)) > 0]
    debtors.sort(key=lambda c: float(c["balance"]), reverse=True)
    debtors = debtors[: int(limit)]
    for c in debtors:
        stats = get_client_payment_stats(int(c["id"]))
        c["days_since_last"] = stats.get("days_since_last")
        c["last_payment_at"] = stats.get("last_payment_at")
    return debtors


def find_clients_by_name(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """Case-insensitive substring search over non-archived clients."""
    q = (query or "").strip()
    if not q:
        return []
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT id, name, phone
            FROM clients
            WHERE COALESCE(archived, 0) = 0
              AND LOWER(name) LIKE LOWER(?)
            ORDER BY name
            LIMIT ?
            """,
            (f"%{q}%", int(limit)),
        ).fetchall()
    result = []
    for r in rows:
        d = _row_to_dict(r)
        d["balance"] = get_client_balance(d["id"])
        result.append(d)
    return result


# ── Phase 2: Profit report ──────────────────────────────────────────────────

def get_profit_report(
    date_from: str,
    date_to: str,
    warehouse_codes: Optional[list[str]] = None,
) -> dict[str, Any]:
    """
    Gross profit report for sales in [date_from, date_to] (inclusive dates).

    date_from / date_to are 'YYYY-MM-DD' strings.
    warehouse_codes filter (list of codes) — None or empty = all warehouses.

    Rules (per Fable5 spec, Phase 2):
      - Line with cost_price = 0 counts as "unknown cost" and is EXCLUDED
        from profit calculations. Its revenue is reported separately so it
        does not inflate margin.
      - Returns are subtracted from profit using their own cost_price.
      - Free-line items (product_id NULL) also have cost_price = 0 → unknown.

    Returns a dict with:
      - totals: {revenue, cost, profit, margin_pct, ret_revenue, ret_cost}
      - unknown: {lines, revenue}  # excluded rows
      - by_brand: list of {brand, qty, revenue, cost, profit}
      - by_client: list of {client_id, client, revenue, cost, profit}
      - top_products: list of {product_id, brand, model, name, qty, revenue, cost, profit}
        sorted by profit desc, limit 20
    """
    # Normalise inclusive date range to timestamp bounds.
    date_from = (date_from or "").strip()
    date_to = (date_to or "").strip()

    wh_filter_sql_sale = ""
    wh_filter_sql_ret  = ""
    params_common: list[Any] = [date_from, date_to]
    if warehouse_codes:
        placeholders = ",".join("?" * len(warehouse_codes))
        wh_filter_sql_sale = f" AND c.warehouse_code IN ({placeholders})"
        wh_filter_sql_ret  = f" AND ri_inv.warehouse_code IN ({placeholders})"
        params_common.extend(warehouse_codes)

    with _connect() as conn:
        # ─── SALES ─────────────────────────────────────────────────────────
        # Rows with cost_price > 0 → profitable; rows with cost_price = 0 → unknown.
        sale_rows = conn.execute(
            f"""
            SELECT ci.qty, ci.unit_price, ci.cost_price, ci.product_id, ci.free_line, ci.free_name,
                   p.brand, p.model, p.name,
                   c.client_id, cl.name AS client_name,
                   date(i.created_at) AS day
            FROM invoices i
            JOIN carts c ON c.id = i.cart_id
            JOIN cart_items ci ON ci.cart_id = c.id
            LEFT JOIN products p ON p.id = ci.product_id
            LEFT JOIN clients  cl ON cl.id = c.client_id
            WHERE date(i.created_at) BETWEEN ? AND ?
              AND c.status = 'CLOSED'
              {wh_filter_sql_sale}
            """,
            params_common,
        ).fetchall()

        # ─── RETURNS ───────────────────────────────────────────────────────
        return_rows = conn.execute(
            f"""
            SELECT ri.qty, ri.unit_price, ri.cost_price, ri.product_id, ri.free_line,
                   p.brand, p.model, p.name,
                   ri_inv.client_id, cl.name AS client_name
            FROM return_items ri
            JOIN return_invoices ri_inv ON ri_inv.id = ri.invoice_id
            LEFT JOIN products p  ON p.id = ri.product_id
            LEFT JOIN clients  cl ON cl.id = ri_inv.client_id
            WHERE date(ri_inv.created_at) BETWEEN ? AND ?
              AND ri_inv.status = 'DONE'
              {wh_filter_sql_ret}
            """,
            params_common,
        ).fetchall()

    # ─── Aggregate ─────────────────────────────────────────────────────────
    revenue = cost = 0.0
    unknown_lines = 0
    unknown_revenue = 0.0

    by_brand: dict[str, dict[str, Any]] = {}
    by_client: dict[int, dict[str, Any]] = {}
    by_product: dict[int, dict[str, Any]] = {}

    def _brand_bucket(name):
        b = by_brand.setdefault(name, {"brand": name, "qty": 0.0, "revenue": 0.0, "cost": 0.0, "profit": 0.0})
        return b

    def _client_bucket(cid, cname):
        b = by_client.setdefault(cid or 0, {"client_id": cid, "client": cname or "—",
                                            "revenue": 0.0, "cost": 0.0, "profit": 0.0})
        return b

    def _prod_bucket(pid, brand, model, name):
        b = by_product.setdefault(pid, {"product_id": pid, "brand": brand, "model": model, "name": name,
                                        "qty": 0.0, "revenue": 0.0, "cost": 0.0, "profit": 0.0})
        return b

    for r in sale_rows:
        qty  = float(r["qty"] or 0)
        up   = float(r["unit_price"] or 0)
        cp   = float(r["cost_price"] or 0)
        line_rev = up * qty
        line_cost = cp * qty
        line_profit = line_rev - line_cost

        if cp <= 0:
            unknown_lines += 1
            unknown_revenue += line_rev
            continue  # excluded from profit

        revenue += line_rev
        cost    += line_cost

        brand_label = r["brand"] or (r["free_name"] or "—")
        b = _brand_bucket(brand_label)
        b["qty"]     += qty
        b["revenue"] += line_rev
        b["cost"]    += line_cost
        b["profit"]  += line_profit

        cl = _client_bucket(r["client_id"], r["client_name"])
        cl["revenue"] += line_rev
        cl["cost"]    += line_cost
        cl["profit"]  += line_profit

        if r["product_id"]:
            pb = _prod_bucket(r["product_id"], r["brand"] or "", r["model"] or "", r["name"] or "")
            pb["qty"]     += qty
            pb["revenue"] += line_rev
            pb["cost"]    += line_cost
            pb["profit"]  += line_profit

    ret_revenue = ret_cost = 0.0
    for r in return_rows:
        qty  = float(r["qty"] or 0)
        up   = float(r["unit_price"] or 0)
        cp   = float(r["cost_price"] or 0)
        line_rev  = up * qty
        line_cost = cp * qty

        # Returns with cost=0 also excluded from profit adjustment.
        if cp <= 0:
            unknown_lines += 1
            unknown_revenue -= line_rev  # negative revenue for excluded returns
            continue

        ret_revenue += line_rev
        ret_cost    += line_cost

        brand_label = r["brand"] or "—"
        b = _brand_bucket(brand_label)
        b["revenue"] -= line_rev
        b["cost"]    -= line_cost
        b["profit"]  -= (line_rev - line_cost)

        cl = _client_bucket(r["client_id"], r["client_name"])
        cl["revenue"] -= line_rev
        cl["cost"]    -= line_cost
        cl["profit"]  -= (line_rev - line_cost)

        if r["product_id"]:
            pb = _prod_bucket(r["product_id"], r["brand"] or "", r["model"] or "", r["name"] or "")
            pb["revenue"] -= line_rev
            pb["cost"]    -= line_cost
            pb["profit"]  -= (line_rev - line_cost)

    # Net values
    net_revenue = revenue - ret_revenue
    net_cost    = cost    - ret_cost
    profit      = net_revenue - net_cost
    margin_pct  = (profit / net_revenue * 100.0) if net_revenue > 0 else 0.0

    def _round_bucket(d, keys):
        for k in keys: d[k] = round(d[k], 2)
        return d

    brands_list = sorted(by_brand.values(), key=lambda x: x["profit"], reverse=True)
    for b in brands_list: _round_bucket(b, ("qty", "revenue", "cost", "profit"))

    clients_list = sorted(by_client.values(), key=lambda x: x["profit"], reverse=True)
    for c in clients_list: _round_bucket(c, ("revenue", "cost", "profit"))

    products_list = sorted(by_product.values(), key=lambda x: x["profit"], reverse=True)[:20]
    for pr in products_list: _round_bucket(pr, ("qty", "revenue", "cost", "profit"))

    return {
        "totals": {
            "revenue":       round(revenue, 2),
            "cost":          round(cost, 2),
            "ret_revenue":   round(ret_revenue, 2),
            "ret_cost":      round(ret_cost, 2),
            "net_revenue":   round(net_revenue, 2),
            "net_cost":      round(net_cost, 2),
            "profit":        round(profit, 2),
            "margin_pct":    round(margin_pct, 2),
        },
        "unknown": {
            "lines":   unknown_lines,
            "revenue": round(unknown_revenue, 2),
        },
        "by_brand":     brands_list,
        "by_client":    clients_list,
        "top_products": products_list,
    }


# ── Phase 4: Inventory (physical count vs system) ────────────────────────────

def list_stock_for_inventory(warehouse_code: str) -> list[dict[str, Any]]:
    """
    Full stock listing for a given warehouse: every non-archived product with
    its current system quantity. Products with qty=0 are included so the user
    can also spot goods that "should" be there but aren't.
    """
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT p.id AS product_id, p.brand, p.model, p.name, p.barcode,
                   COALESCE(s.qty, 0) AS qty
            FROM products p
            LEFT JOIN stock s ON s.product_id = p.id AND s.warehouse_code = ?
            WHERE p.archived = 0
            ORDER BY p.brand, p.model
            """,
            (warehouse_code,),
        ).fetchall()
        return [_row_to_dict(r) for r in rows]


def apply_inventory_adjustments(
    warehouse_code: str,
    adjustments: list[dict[str, Any]],
    note: str = "",
) -> tuple[bool, str, int]:
    """
    Apply a batch of ADJUST operations produced by a physical count.

    Each item in `adjustments` must be a dict:
        {"product_id": int, "delta": float}
    where delta is (actual_qty - system_qty). Positive = surplus (add to stock),
    negative = shortage (subtract from stock).

    Items with delta == 0 are skipped (no discrepancy → no journal entry).

    Returns (ok, error, n_applied). All in one transaction — if anything
    fails, nothing is committed.
    """
    note = (note or "").strip()
    if not note:
        return False, "note_required", 0

    # Filter out zero-deltas
    real_adjustments = [
        a for a in adjustments
        if float(a.get("delta") or 0) != 0
    ]
    if not real_adjustments:
        return False, "no_changes", 0

    try:
        with _connect() as conn:
            n = 0
            for a in real_adjustments:
                pid = int(a["product_id"])
                delta = float(a["delta"])
                # Update stock
                conn.execute(
                    """
                    INSERT INTO stock (warehouse_code, product_id, qty)
                    VALUES (?, ?, ?)
                    ON CONFLICT(warehouse_code, product_id)
                    DO UPDATE SET qty = qty + excluded.qty
                    """,
                    (warehouse_code, pid, delta),
                )
                # Journal it
                # Use localtime for created_at so the "today" filter in
                # /reports/inventory picks it up regardless of server TZ.
                conn.execute(
                    "INSERT INTO stock_ops"
                    " (created_at, op_type, source, warehouse_code, product_id, qty, note)"
                    " VALUES (datetime('now','localtime'), 'ADJUST', 'INVENTORY', ?, ?, ?, ?)",
                    (warehouse_code, pid, delta, note),
                )
                n += 1
            conn.commit()
        return True, "", n
    except Exception as exc:
        return False, str(exc), 0


def get_inventory_discrepancies(
    date_from: str,
    date_to: str,
    warehouse_codes: Optional[list[str]] = None,
) -> dict[str, Any]:
    """
    Aggregated report of ADJUST operations for period [date_from, date_to].

    Splits into "surplus" (delta > 0, оприходовано) and "shortage" (delta < 0,
    списано). Groups by warehouse and by product for the two big tables.
    """
    params: list[Any] = [date_from, date_to]
    wh_sql = ""
    if warehouse_codes:
        placeholders = ",".join("?" * len(warehouse_codes))
        wh_sql = f" AND op.warehouse_code IN ({placeholders})"
        params.extend(warehouse_codes)

    with _connect() as conn:
        rows = conn.execute(
            f"""
            SELECT op.id, op.created_at, op.warehouse_code, op.product_id, op.qty, op.note,
                   p.brand, p.model, p.name
            FROM stock_ops op
            LEFT JOIN products p ON p.id = op.product_id
            WHERE op.op_type = 'ADJUST'
              AND date(op.created_at) BETWEEN ? AND ?
              {wh_sql}
            ORDER BY op.created_at DESC
            """,
            params,
        ).fetchall()

    surplus_total = 0.0    # sum of positive deltas
    shortage_total = 0.0   # sum of |negative deltas|
    ops = []

    by_wh: dict[str, dict[str, float]] = {}
    by_product: dict[int, dict[str, Any]] = {}

    for r in rows:
        d = _row_to_dict(r)
        qty = float(d["qty"])
        ops.append(d)

        wh = d["warehouse_code"]
        bucket = by_wh.setdefault(wh, {"warehouse_code": wh, "surplus": 0.0, "shortage": 0.0, "n_ops": 0})
        bucket["n_ops"] += 1

        pid = d["product_id"]
        pb = by_product.setdefault(pid, {
            "product_id": pid, "brand": d["brand"], "model": d["model"], "name": d["name"],
            "surplus": 0.0, "shortage": 0.0, "n_ops": 0,
        })
        pb["n_ops"] += 1

        if qty > 0:
            surplus_total += qty
            bucket["surplus"] += qty
            pb["surplus"] += qty
        else:
            v = abs(qty)
            shortage_total += v
            bucket["shortage"] += v
            pb["shortage"] += v

    for b in by_wh.values():
        b["surplus"]  = round(b["surplus"], 2)
        b["shortage"] = round(b["shortage"], 2)
    for pb in by_product.values():
        pb["surplus"]  = round(pb["surplus"], 2)
        pb["shortage"] = round(pb["shortage"], 2)
        pb["net"] = round(pb["surplus"] - pb["shortage"], 2)

    products_list = sorted(
        by_product.values(),
        key=lambda x: (x["shortage"] + x["surplus"]),
        reverse=True,
    )

    return {
        "totals": {
            "surplus":  round(surplus_total, 2),
            "shortage": round(shortage_total, 2),
            "net":      round(surplus_total - shortage_total, 2),
            "n_ops":    len(rows),
        },
        "by_warehouse": sorted(by_wh.values(), key=lambda x: x["warehouse_code"]),
        "by_product":   products_list,
        "ops":          ops[:200],
    }


# ── Phase 5 — Expenses CRUD ─────────────────────────────────────────────────

def list_expense_categories(include_archived: bool = False) -> list[dict[str, Any]]:
    with _connect() as conn:
        if include_archived:
            rows = conn.execute(
                "SELECT * FROM expense_categories ORDER BY kind, name"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM expense_categories WHERE archived = 0 ORDER BY kind, name"
            ).fetchall()
        return [_row_to_dict(r) for r in rows]


def add_expense_category(name: str, kind: str = "business") -> tuple[bool, str]:
    name = (name or "").strip()
    if not name:
        return False, "name_required"
    if kind not in EXPENSE_KINDS:
        return False, "bad_kind"
    try:
        with _connect() as conn:
            conn.execute(
                "INSERT INTO expense_categories (name, kind) VALUES (?, ?)",
                (name, kind),
            )
            conn.commit()
        return True, ""
    except sqlite3.IntegrityError:
        return False, "duplicate_name"
    except Exception as exc:
        return False, str(exc)


def update_expense_category(
    category_id: int, name: str, kind: str
) -> tuple[bool, str]:
    name = (name or "").strip()
    if not name:
        return False, "name_required"
    if kind not in EXPENSE_KINDS:
        return False, "bad_kind"
    try:
        with _connect() as conn:
            conn.execute(
                "UPDATE expense_categories SET name = ?, kind = ? WHERE id = ?",
                (name, kind, category_id),
            )
            conn.commit()
        return True, ""
    except sqlite3.IntegrityError:
        return False, "duplicate_name"
    except Exception as exc:
        return False, str(exc)


def set_expense_category_archived(
    category_id: int, archived: bool
) -> tuple[bool, str]:
    try:
        with _connect() as conn:
            conn.execute(
                "UPDATE expense_categories SET archived = ? WHERE id = ?",
                (1 if archived else 0, category_id),
            )
            conn.commit()
        return True, ""
    except Exception as exc:
        return False, str(exc)


def list_expenses(
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    category_id: Optional[int] = None,
    kind: Optional[str] = None,
    search: str = "",
) -> list[dict[str, Any]]:
    """
    Return expenses joined with category info, newest date first.

    All filters are optional; date_from/date_to are inclusive 'YYYY-MM-DD'.
    kind, if given, must be 'business' or 'personal'.
    """
    sql = (
        "SELECT e.id, e.date, e.category_id, e.amount_usd, e.note, e.created_at,"
        "       c.name AS category_name, c.kind AS category_kind,"
        "       c.archived AS category_archived"
        " FROM expenses e"
        " JOIN expense_categories c ON c.id = e.category_id"
        " WHERE 1=1"
    )
    params: list[Any] = []
    if date_from:
        sql += " AND e.date >= ?"
        params.append(date_from)
    if date_to:
        sql += " AND e.date <= ?"
        params.append(date_to)
    if category_id:
        sql += " AND e.category_id = ?"
        params.append(int(category_id))
    if kind and kind in EXPENSE_KINDS:
        sql += " AND c.kind = ?"
        params.append(kind)
    if search:
        sql += " AND (e.note LIKE ? OR c.name LIKE ?)"
        s = f"%{search.strip()}%"
        params.extend([s, s])
    sql += " ORDER BY e.date DESC, e.id DESC"
    with _connect() as conn:
        rows = conn.execute(sql, params).fetchall()
        return [_row_to_dict(r) for r in rows]


def get_expense(expense_id: int) -> Optional[dict[str, Any]]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT e.*, c.name AS category_name, c.kind AS category_kind"
            " FROM expenses e"
            " JOIN expense_categories c ON c.id = e.category_id"
            " WHERE e.id = ?",
            (expense_id,),
        ).fetchone()
        return _row_to_dict(row) if row else None


def add_expense(
    date: str, category_id: int, amount_usd: float, note: str = ""
) -> tuple[bool, str]:
    date = (date or "").strip()
    if not date:
        return False, "date_required"
    if not category_id:
        return False, "category_required"
    try:
        amount_usd = float(amount_usd)
    except (TypeError, ValueError):
        return False, "amount_invalid"
    if amount_usd <= 0:
        return False, "amount_must_be_positive"
    try:
        with _connect() as conn:
            cat = conn.execute(
                "SELECT id FROM expense_categories WHERE id = ?", (category_id,)
            ).fetchone()
            if not cat:
                return False, "category_not_found"
            conn.execute(
                "INSERT INTO expenses (date, category_id, amount_usd, note)"
                " VALUES (?, ?, ?, ?)",
                (date, int(category_id), round(amount_usd, 2), (note or "").strip()),
            )
            conn.commit()
        return True, ""
    except Exception as exc:
        return False, str(exc)


def update_expense(
    expense_id: int,
    date: str,
    category_id: int,
    amount_usd: float,
    note: str = "",
) -> tuple[bool, str]:
    date = (date or "").strip()
    if not date:
        return False, "date_required"
    if not category_id:
        return False, "category_required"
    try:
        amount_usd = float(amount_usd)
    except (TypeError, ValueError):
        return False, "amount_invalid"
    if amount_usd <= 0:
        return False, "amount_must_be_positive"
    try:
        with _connect() as conn:
            cat = conn.execute(
                "SELECT id FROM expense_categories WHERE id = ?", (category_id,)
            ).fetchone()
            if not cat:
                return False, "category_not_found"
            row = conn.execute(
                "SELECT id FROM expenses WHERE id = ?", (expense_id,)
            ).fetchone()
            if not row:
                return False, "expense_not_found"
            conn.execute(
                "UPDATE expenses"
                "   SET date = ?, category_id = ?, amount_usd = ?, note = ?"
                " WHERE id = ?",
                (
                    date, int(category_id), round(amount_usd, 2),
                    (note or "").strip(), expense_id,
                ),
            )
            conn.commit()
        return True, ""
    except Exception as exc:
        return False, str(exc)


def delete_expense(expense_id: int) -> tuple[bool, str]:
    try:
        with _connect() as conn:
            row = conn.execute(
                "SELECT id FROM expenses WHERE id = ?", (expense_id,)
            ).fetchone()
            if not row:
                return False, "expense_not_found"
            conn.execute("DELETE FROM expenses WHERE id = ?", (expense_id,))
            conn.commit()
        return True, ""
    except Exception as exc:
        return False, str(exc)


def get_expenses_summary(
    date_from: str,
    date_to: str,
) -> dict[str, Any]:
    """
    Aggregate expenses per category for the period; also totals per kind.

    Returns:
        {
            "totals": {"business": float, "personal": float, "all": float},
            "by_category": [
                {"category_id", "category", "kind", "amount"},
                ...
            ],
        }
    """
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT c.id AS category_id, c.name AS category, c.kind AS kind,
                   COALESCE(SUM(e.amount_usd), 0) AS amount
            FROM expense_categories c
            LEFT JOIN expenses e
                   ON e.category_id = c.id
                  AND e.date BETWEEN ? AND ?
            GROUP BY c.id, c.name, c.kind
            HAVING amount > 0
            ORDER BY c.kind, amount DESC
            """,
            (date_from, date_to),
        ).fetchall()
    by_cat = [_row_to_dict(r) for r in rows]
    totals = {"business": 0.0, "personal": 0.0}
    for r in by_cat:
        r["amount"] = round(float(r["amount"]), 2)
        if r["kind"] in totals:
            totals[r["kind"]] += r["amount"]
    totals["business"] = round(totals["business"], 2)
    totals["personal"] = round(totals["personal"], 2)
    totals["all"] = round(totals["business"] + totals["personal"], 2)
    return {"totals": totals, "by_category": by_cat}


# ── Phase 3: Payment discipline stats ────────────────────────────────────────

def get_client_payment_stats(client_id: int) -> dict[str, Any]:
    """
    Payment discipline stats for a single client.

    Only rows with amount > 0 count as "payments" — those reduce debt.
    Rows with amount < 0 (manual debt additions) are NOT payments.

    Returns:
        {
            "last_payment_at": ISO datetime or None,
            "days_since_last": int or None,
            "sum_last_30d":  float,
            "sum_last_90d":  float,
            "avg_payment_90d": float (0 if no payments in 90 days),
            "count_last_90d": int,
        }
    """
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT
              MAX(CASE WHEN amount > 0 THEN created_at END) AS last_payment_at,
              COALESCE(SUM(CASE WHEN amount > 0 AND created_at >= datetime('now','localtime','-30 days') THEN amount END), 0) AS sum_30d,
              COALESCE(SUM(CASE WHEN amount > 0 AND created_at >= datetime('now','localtime','-90 days') THEN amount END), 0) AS sum_90d,
              COALESCE(COUNT(CASE WHEN amount > 0 AND created_at >= datetime('now','localtime','-90 days') THEN 1 END), 0) AS count_90d
            FROM client_ledger
            WHERE client_id = ?
            """,
            (client_id,),
        ).fetchone()
    return _payment_stats_from_row(row)


def get_all_clients_payment_stats() -> dict[int, dict[str, Any]]:
    """Batch version: {client_id: stats-dict} for every client that has at least one ledger row."""
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT client_id,
              MAX(CASE WHEN amount > 0 THEN created_at END) AS last_payment_at,
              COALESCE(SUM(CASE WHEN amount > 0 AND created_at >= datetime('now','localtime','-30 days') THEN amount END), 0) AS sum_30d,
              COALESCE(SUM(CASE WHEN amount > 0 AND created_at >= datetime('now','localtime','-90 days') THEN amount END), 0) AS sum_90d,
              COALESCE(COUNT(CASE WHEN amount > 0 AND created_at >= datetime('now','localtime','-90 days') THEN 1 END), 0) AS count_90d
            FROM client_ledger
            GROUP BY client_id
            """,
        ).fetchall()
    return {r["client_id"]: _payment_stats_from_row(r) for r in rows}


def _payment_stats_from_row(row) -> dict[str, Any]:
    """Turn a SQL row (or None) into the payment-stats dict."""
    if row is None:
        last_at = None
        sum30 = sum90 = 0.0
        cnt90 = 0
    else:
        last_at = row["last_payment_at"]
        sum30 = float(row["sum_30d"] or 0)
        sum90 = float(row["sum_90d"] or 0)
        cnt90 = int(row["count_90d"] or 0)

    days_since = None
    if last_at:
        try:
            from datetime import datetime
            # created_at is 'YYYY-MM-DD HH:MM:SS' localtime
            dt = datetime.strptime(str(last_at)[:19], "%Y-%m-%d %H:%M:%S")
            days_since = (datetime.now() - dt).days
        except (ValueError, TypeError):
            days_since = None

    avg90 = round(sum90 / cnt90, 2) if cnt90 else 0.0
    return {
        "last_payment_at": last_at,
        "days_since_last": days_since,
        "sum_last_30d": round(sum30, 2),
        "sum_last_90d": round(sum90, 2),
        "avg_payment_90d": avg90,
        "count_last_90d": cnt90,
    }



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
            SELECT i.number, i.created_at, i.currency, c.id AS cart_id
            FROM invoices i
            JOIN carts c ON c.id = i.cart_id
            WHERE c.client_id = ? AND c.status = 'CLOSED'
            ORDER BY i.created_at DESC
            """,
            (client_id,),
        ).fetchall()
        for r in rows:
            row = _row_to_dict(r)
            items = conn.execute(
                "SELECT unit_price, qty FROM cart_items WHERE cart_id = ?",
                (r["cart_id"],),
            ).fetchall()
            events.append({
                "kind": "INVOICE",
                "dt": row.get("created_at", ""),
                "created_at": row.get("created_at", ""),
                "ref": str(row.get("number", "")),
                "amount": calc_document_total(list(items), "unit_price"),
                "note": "",
                "view_url": f"/sale/xlsx/view?n={row.get('number')}",
                "download_url": f"/sale/xlsx?n={row.get('number')}",
            })

        rows_return = conn.execute(
            """
            SELECT id, number, created_at, total, note
            FROM return_invoices
            WHERE client_id = ? AND status = 'DONE'
            ORDER BY created_at DESC
            """,
            (client_id,),
        ).fetchall()
        for r in rows_return:
            row = _row_to_dict(r)
            events.append({
                "kind": "RETURN",
                "dt": row.get("created_at", ""),
                "created_at": row.get("created_at", ""),
                "ref": str(row.get("number", "")),
                "amount": float(row.get("total") or 0),
                "note": row.get("note", ""),
                "view_url": f"/return/xlsx/view?n={row.get('id')}",
                "download_url": f"/return/xlsx?n={row.get('id')}",
            })

        # Skip auto-generated ledger rows for returns — they are the side
        # effect of return_invoice_finish and are already shown to the user
        # as a "RETURN" event above (from return_invoices). Displaying the
        # ledger twin would double-count the debt reduction in balance_after
        # and confuse the user with two lines for one action.
        # The note pattern is stable: "RETURN #NNNNNN" (6-digit zero-padded).
        rows2 = conn.execute(
            "SELECT id, created_at, amount, note"
            " FROM client_ledger WHERE client_id = ?"
            "   AND (note IS NULL OR note NOT LIKE 'RETURN #%')"
            " ORDER BY created_at DESC",
            (client_id,),
        ).fetchall()
        for r in rows2:
            row = _row_to_dict(r)
            events.append({
                "kind": "LEDGER",
                "dt": row.get("created_at", ""),
                "created_at": row.get("created_at", ""),
                "ref": str(row.get("id", "")),
                "amount": float(row.get("amount") or 0),
                "note": row.get("note", ""),
            })
        events.sort(key=lambda x: x.get("created_at", ""))
        running_balance = 0.0
        for ev in events:
            amount = float(ev.get("amount") or 0)
            kind = str(ev.get("kind") or "")
            if kind == "INVOICE":
                running_balance += amount
            elif kind in {"RETURN", "LEDGER"}:
                running_balance -= amount
            ev["balance_after"] = round(running_balance, 2)

        events.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        return events


def get_total_clients_debt() -> float:
    with _connect() as conn:
        inv_items = conn.execute(
            """
            SELECT ci.unit_price, ci.qty
            FROM invoices i
            JOIN carts c ON c.id = i.cart_id
            JOIN cart_items ci ON ci.cart_id = c.id
            WHERE c.status = 'CLOSED'
            """
        ).fetchall()
        inv_total = calc_document_total(list(inv_items), "unit_price")

        ledger_row = conn.execute(
            "SELECT COALESCE(SUM(amount), 0) AS paid FROM client_ledger"
        ).fetchone()
        paid = float(ledger_row["paid"]) if ledger_row else 0.0

        return round(max(0.0, inv_total - paid), 2)


def list_suppliers(include_archived: bool = False) -> list[dict[str, Any]]:
    with _connect() as conn:
        if include_archived:
            rows = conn.execute("SELECT * FROM suppliers ORDER BY name").fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM suppliers WHERE archived = 0 ORDER BY name"
            ).fetchall()
        return [_row_to_dict(r) for r in rows]


def add_supplier(name: str, phone: str = "", note: str = "") -> tuple[bool, str]:
    try:
        with _connect() as conn:
            conn.execute(
                "INSERT INTO suppliers (name, phone, note) VALUES (?, ?, ?)",
                (name.strip(), phone.strip(), note.strip()),
            )
            conn.commit()
        return True, ""
    except Exception as exc:
        return False, str(exc)


def get_supplier(supplier_id: int) -> Optional[dict[str, Any]]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM suppliers WHERE id = ?", (supplier_id,)
        ).fetchone()
        return _row_to_dict(row) if row else None


def update_supplier(
    supplier_id: int, name: str, phone: str, note: str
) -> tuple[bool, str]:
    try:
        with _connect() as conn:
            conn.execute(
                "UPDATE suppliers SET name = ?, phone = ?, note = ? WHERE id = ?",
                (name.strip(), phone.strip(), note.strip(), supplier_id),
            )
            conn.commit()
        return True, ""
    except Exception as exc:
        return False, str(exc)


def set_supplier_archived(supplier_id: int, archived: int) -> tuple[bool, str]:
    try:
        with _connect() as conn:
            conn.execute(
                "UPDATE suppliers SET archived = ? WHERE id = ?",
                (archived, supplier_id),
            )
            conn.commit()
        return True, ""
    except Exception as exc:
        return False, str(exc)


def add_supplier_adjustment(
    supplier_id: int, amount: float, note: str = ""
) -> tuple[bool, str]:
    """Record a payment/adjustment that reduces supplier debt (positive amount)."""
    try:
        with _connect() as conn:
            conn.execute(
                "INSERT INTO supplier_ledger (supplier_id, amount, note) VALUES (?, ?, ?)",
                (supplier_id, abs(amount), note.strip()),
            )
            conn.commit()
        return True, ""
    except Exception as exc:
        return False, str(exc)


def add_supplier_debt(
    supplier_id: int, amount: float, note: str = ""
) -> tuple[bool, str]:
    """Record an explicit additional debt (stored as negative in ledger)."""
    try:
        with _connect() as conn:
            conn.execute(
                "INSERT INTO supplier_ledger (supplier_id, amount, note) VALUES (?, ?, ?)",
                (supplier_id, -abs(amount), note.strip()),
            )
            conn.commit()
        return True, ""
    except Exception as exc:
        return False, str(exc)



def delete_supplier_ledger_entry(entry_id: int, supplier_id: int) -> tuple[bool, str]:
    """Delete a single supplier_ledger row by id. Verifies it belongs to supplier_id."""
    try:
        with _connect() as conn:
            row = conn.execute(
                "SELECT supplier_id FROM supplier_ledger WHERE id = ?", (entry_id,)
            ).fetchone()
            if row is None:
                return False, "not_found"
            if int(row["supplier_id"]) != int(supplier_id):
                return False, "wrong_supplier"
            conn.execute("DELETE FROM supplier_ledger WHERE id = ?", (entry_id,))
            conn.commit()
        return True, ""
    except Exception as exc:
        return False, str(exc)

def get_supplier_balance(supplier_id: int) -> float:
    """
    Supplier debt = DONE receive invoices total - sum(supplier_ledger.amount).
    Positive value means we owe supplier.
    """
    with _connect() as conn:
        inv_row = conn.execute(
            """
            SELECT COALESCE(SUM(total), 0) AS inv_total
            FROM receive_invoices
            WHERE status = 'DONE' AND supplier_id = ?
            """,
            (supplier_id,),
        ).fetchone()
        inv_total = float(inv_row["inv_total"]) if inv_row else 0.0

        ledger_row = conn.execute(
            "SELECT COALESCE(SUM(amount), 0) AS paid FROM supplier_ledger WHERE supplier_id = ?",
            (supplier_id,),
        ).fetchone()
        paid = float(ledger_row["paid"]) if ledger_row else 0.0

        return round(inv_total - paid, 2)


def list_suppliers_with_balance(include_archived: bool = False) -> list[dict[str, Any]]:
    suppliers = list_suppliers(include_archived=include_archived)
    for supplier in suppliers:
        supplier["balance"] = get_supplier_balance(supplier["id"])
    return suppliers


def get_supplier_history(supplier_id: int) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    with _connect() as conn:
        inv_rows = conn.execute(
            """
            SELECT ri.id, ri.number, ri.created_at, ri.total, ri.note
            FROM receive_invoices ri
            WHERE ri.status = 'DONE' AND ri.supplier_id = ?
            ORDER BY ri.created_at ASC, ri.id ASC
            """,
            (supplier_id,),
        ).fetchall()
        for r in inv_rows:
            row = _row_to_dict(r)
            events.append(
                {
                    "dt": row.get("created_at", ""),
                    "created_at": row.get("created_at", ""),
                    "kind": "RECEIVE",
                    "ref": str(row.get("number", "")),
                    "amount": float(row.get("total") or 0),
                    "note": row.get("note", ""),
                    "view_url": f"/receive/xlsx/view?n={row.get('id')}",
                    "download_url": f"/receive/xlsx?n={row.get('id')}",
                    "_delta": float(row.get("total") or 0),
                }
            )

        ledger_rows = conn.execute(
            """
            SELECT id, created_at, amount, note
            FROM supplier_ledger
            WHERE supplier_id = ?
            ORDER BY created_at ASC, id ASC
            """,
            (supplier_id,),
        ).fetchall()
        for r in ledger_rows:
            row = _row_to_dict(r)
            amt = float(row.get("amount") or 0)
            events.append(
                {
                    "dt": row.get("created_at", ""),
                    "created_at": row.get("created_at", ""),
                    "kind": "LEDGER",
                    "ref": str(row.get("id", "")),
                    "amount": amt,
                    "note": row.get("note", ""),
                    "_delta": -amt,
                }
            )

    events.sort(key=lambda x: (x.get("created_at", ""), x.get("kind", ""), x.get("ref", "")))
    running = 0.0
    for ev in events:
        running = round(running + float(ev.get("_delta", 0) or 0), 2)
        ev["balance_after"] = running
        ev.pop("_delta", None)
    return events


def get_total_suppliers_debt() -> float:
    with _connect() as conn:
        inv_row = conn.execute(
            "SELECT COALESCE(SUM(total), 0) AS inv_total FROM receive_invoices WHERE status = 'DONE' AND supplier_id IS NOT NULL"
        ).fetchone()
        inv_total = float(inv_row["inv_total"]) if inv_row else 0.0
        ledger_row = conn.execute(
            "SELECT COALESCE(SUM(amount), 0) AS paid FROM supplier_ledger"
        ).fetchone()
        paid = float(ledger_row["paid"]) if ledger_row else 0.0
        return round(max(0.0, inv_total - paid), 2)


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


def get_reports_snapshot(
    date_from: str,
    date_to: str,
    top_limit: int = 10,
    low_stock_threshold: float = 2,
    warehouse_codes: Optional[list[str]] = None,
) -> dict[str, Any]:
    selected_warehouses = [code.strip() for code in (warehouse_codes or []) if code and code.strip()]
    warehouse_filter_sql = ""
    if selected_warehouses:
        placeholders = ",".join("?" for _ in selected_warehouses)
        warehouse_filter_sql = f" AND s.warehouse_code IN ({placeholders})"

    with _connect() as conn:
        sales_row = conn.execute(
            """
            SELECT COALESCE(SUM(i.total), 0) AS revenue,
                   COUNT(*) AS sales_count
            FROM invoices i
            JOIN carts c ON c.id = i.cart_id
            WHERE c.status = 'CLOSED'
              AND date(i.created_at) BETWEEN ? AND ?
            """,
            (date_from, date_to),
        ).fetchone()
        revenue = float(sales_row["revenue"]) if sales_row else 0.0
        sales_count = int(sales_row["sales_count"]) if sales_row else 0

        sold_row = conn.execute(
            """
            SELECT COALESCE(SUM(ci.qty), 0) AS sold_qty
            FROM cart_items ci
            JOIN carts c ON c.id = ci.cart_id
            JOIN invoices i ON i.cart_id = c.id
            WHERE c.status = 'CLOSED'
              AND date(i.created_at) BETWEEN ? AND ?
            """,
            (date_from, date_to),
        ).fetchone()
        sold_qty = float(sold_row["sold_qty"]) if sold_row else 0.0

        returns_row = conn.execute(
            """
            SELECT COALESCE(SUM(total), 0) AS returns_total
            FROM return_invoices
            WHERE status = 'DONE'
              AND date(created_at) BETWEEN ? AND ?
            """,
            (date_from, date_to),
        ).fetchone()
        returns_total = float(returns_row["returns_total"]) if returns_row else 0.0

        stock_row = conn.execute(
            f"""
            SELECT COALESCE(SUM(s.qty), 0) AS stock_qty_total
            FROM stock s
            JOIN products p ON p.id = s.product_id
            WHERE p.archived = 0
            {warehouse_filter_sql}
            """,
            selected_warehouses,
        ).fetchone()
        stock_qty_total = float(stock_row["stock_qty_total"]) if stock_row else 0.0

        positions_row = conn.execute(
            f"""
            SELECT COUNT(*) AS positions_count
            FROM (
                SELECT s.product_id
                FROM stock s
                JOIN products p ON p.id = s.product_id
                WHERE p.archived = 0
                {warehouse_filter_sql}
                GROUP BY s.product_id
                HAVING SUM(s.qty) > 0
            )
            """,
            selected_warehouses,
        ).fetchone()
        stock_positions_count = int(positions_row["positions_count"]) if positions_row else 0

        top_rows = conn.execute(
            """
            SELECT
                COALESCE(p.brand, '') AS brand,
                COALESCE(p.model, ci.free_name, '') AS model,
                COALESCE(p.name, ci.free_name, '') AS name,
                COALESCE(SUM(ci.qty), 0) AS sold_qty,
                COALESCE(SUM(ci.total), 0) AS sales_total
            FROM cart_items ci
            JOIN carts c ON c.id = ci.cart_id
            JOIN invoices i ON i.cart_id = c.id
            LEFT JOIN products p ON p.id = ci.product_id
            WHERE c.status = 'CLOSED'
              AND date(i.created_at) BETWEEN ? AND ?
            GROUP BY ci.product_id, ci.free_name, p.brand, p.model, p.name
            ORDER BY sold_qty DESC, sales_total DESC
            LIMIT ?
            """,
            (date_from, date_to, int(top_limit)),
        ).fetchall()

        low_stock_rows = conn.execute(
            f"""
            SELECT s.warehouse_code,
                   COALESCE(p.brand, '') AS brand,
                   COALESCE(p.model, '') AS model,
                   COALESCE(p.name, '') AS name,
                   s.qty
            FROM stock s
            JOIN products p ON p.id = s.product_id
            WHERE p.archived = 0
              AND s.qty <= ?
              {warehouse_filter_sql}
            ORDER BY s.qty ASC, s.warehouse_code ASC, p.brand ASC, p.model ASC, p.name ASC
            LIMIT 20
            """,
            [float(low_stock_threshold), *selected_warehouses],
        ).fetchall()

        daily_rows = conn.execute(
            """
            SELECT date(i.created_at) AS day,
                   COALESCE(SUM(i.total), 0) AS revenue,
                   COUNT(*) AS sales_count
            FROM invoices i
            JOIN carts c ON c.id = i.cart_id
            WHERE c.status = 'CLOSED'
              AND date(i.created_at) BETWEEN ? AND ?
            GROUP BY date(i.created_at)
            ORDER BY day ASC
            """,
            (date_from, date_to),
        ).fetchall()

    avg_check = (revenue / sales_count) if sales_count > 0 else 0.0

    return {
        "revenue": round(revenue, 2),
        "returns_total": round(returns_total, 2),
        "net_revenue": round(revenue - returns_total, 2),
        "sales_count": sales_count,
        "sold_qty": round(sold_qty, 2),
        "avg_check": round(avg_check, 2),
        "stock_qty_total": round(stock_qty_total, 2),
        "stock_positions_count": stock_positions_count,
        "top_products": [_row_to_dict(r) for r in top_rows],
        "low_stock": [_row_to_dict(r) for r in low_stock_rows],
        "daily_sales": [_row_to_dict(r) for r in daily_rows],
    }


def get_earliest_operation_date() -> Optional[date]:
    """Return the earliest date of any closed sale or completed return.

    Returns None if the database has no operations yet.
    """
    from datetime import date as _date

    with _connect() as conn:
        row = conn.execute(
            """
            SELECT MIN(earliest) AS earliest
            FROM (
                SELECT MIN(date(i.created_at)) AS earliest
                FROM invoices i
                JOIN carts c ON c.id = i.cart_id
                WHERE c.status = 'CLOSED'
                UNION ALL
                SELECT MIN(date(ri.created_at)) AS earliest
                FROM return_invoices ri
                WHERE ri.status = 'DONE'
            )
            """
        ).fetchone()
        if row and row["earliest"]:
            try:
                return _date.fromisoformat(row["earliest"])
            except ValueError:
                pass
    return None


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
    elif price_mode == "custom" and custom_price is not None:
        return custom_price
    elif price_mode.startswith("wh") and price_mode[2:].isdigit():
        pct = int(price_mode[2:])
        return round(wh_price * (1 + pct / 100), 4)
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
            total = calc_line_total(unit_price, qty)

            existing = conn.execute(
                "SELECT id, qty, unit_price FROM cart_items WHERE cart_id = ? AND product_id = ?",
                (cart_id, prod_row["id"]),
            ).fetchone()
            if existing:
                new_qty = float(existing["qty"]) + float(qty)
                new_total = calc_line_total(existing["unit_price"], new_qty)
                conn.execute(
                    "UPDATE cart_items SET qty = ?, total = ? WHERE id = ?",
                    (new_qty, new_total, existing["id"]),
                )
            else:
                # Phase 1: snapshot current wh_price as historical cost.
                conn.execute(
                    "INSERT INTO cart_items"
                    " (cart_id, product_id, qty, price_mode, unit_price, total, cost_price)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (cart_id, prod_row["id"], qty, price_mode, unit_price, total, wh_price),
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

            total = calc_document_total(list(items), "unit_price")
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


def cart_add_free_item(
    cart_id: int,
    free_name: str,
    qty: float,
    unit_price: float,
) -> tuple[bool, str]:
    """Add a free/manual line item (not linked to any stock product)."""
    try:
        free_name = free_name.strip()
        if not free_name:
            return False, "free_name_required"
        if qty <= 0:
            return False, "qty_must_be_positive"
        if unit_price < 0:
            return False, "price_must_be_non_negative"
        unit_price = _normalize_unit_price(unit_price)
        total = calc_line_total(unit_price, qty)
        with _connect() as conn:
            # Phase 1: free line has no product → cost_price = 0 (excluded from profit reports).
            conn.execute(
                "INSERT INTO cart_items"
                " (cart_id, product_id, free_line, free_name, qty, price_mode, unit_price, total, cost_price)"
                " VALUES (?, NULL, 1, ?, ?, 'custom', ?, ?, 0)",
                (cart_id, free_name, qty, unit_price, total),
            )
            conn.commit()
        return True, ""
    except Exception as exc:
        return False, str(exc)


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
        unit_price = _normalize_unit_price(_compute_unit_price(wh_price, price_mode, custom_price))
        total = calc_line_total(unit_price, qty)
        # Phase 1: snapshot current wh_price as historical cost.
        conn.execute(
            "INSERT INTO cart_items"
            " (cart_id, product_id, qty, price_mode, unit_price, total, cost_price)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (cart_id, prod_row["id"], qty, price_mode, unit_price, total, wh_price),
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
        SELECT ci.free_line, ci.free_name,
               COALESCE(p.brand, '') AS brand, COALESCE(p.model, '') AS model,
               ci.qty, ci.unit_price, ci.total
        FROM cart_items ci
        LEFT JOIN products p ON p.id = ci.product_id
        WHERE ci.cart_id = ?
        """,
        (cart_id,),
    ).fetchall()
    if not items:
        return True, "Cart is empty."
    lines = []
    total = 0.0
    for i in items:
        if i["free_line"]:
            label = i["free_name"]
        else:
            label = f"{i['brand']} {i['model']}"
        lines.append(
            f"{label} x{float(i['qty']):.2f}"
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
            SELECT ci.id, ci.product_id, ci.free_line, ci.free_name,
                   ci.qty, ci.price_mode, ci.unit_price, ci.total,
                   COALESCE(p.brand, '') AS brand,
                   COALESCE(p.model, '') AS model,
                   COALESCE(p.name, ci.free_name) AS name,
                   COALESCE(p.barcode, '') AS barcode
            FROM cart_items ci
            LEFT JOIN products p ON p.id = ci.product_id
            WHERE ci.cart_id = ?
            """,
            (cart_id,),
        ).fetchall()
        if not items:
            return False, "cart_empty", {}, []

        for item in items:
            if item["free_line"]:
                continue  # free items have no stock to check
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
            if item["free_line"]:
                continue  # free items don't affect stock
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

        total = calc_document_total(list(items), "unit_price")
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
            SELECT ci.id, ci.product_id, ci.free_line, ci.free_name,
                   ci.qty, ci.price_mode, ci.unit_price, ci.total,
                   COALESCE(p.brand, '') AS brand,
                   COALESCE(p.model, '') AS model,
                   COALESCE(p.name, ci.free_name) AS name,
                   COALESCE(p.wh_price, 0) AS wh_price
            FROM cart_items ci
            LEFT JOIN products p ON p.id = ci.product_id
            WHERE ci.cart_id = ?
            ORDER BY ci.id
            """,
            (cart_id,),
        ).fetchall()
        items_list = []
        for row in items:
            item = _row_to_dict(row)
            item["total"] = calc_line_total(item.get("unit_price"), item.get("qty"))
            items_list.append(item)
        return _row_to_dict(cart_row), items_list


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
            unit_price = _normalize_unit_price(unit_price)
            total = calc_line_total(unit_price, qty)
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
            SELECT i.*, cl.name AS client, c.client_id, c.warehouse_code
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
                   ci.free_line, ci.free_name,
                   COALESCE(p.brand, '') AS brand,
                   COALESCE(p.model, '') AS model,
                   COALESCE(p.name, ci.free_name) AS name,
                   COALESCE(p.barcode, '') AS barcode,
                   p.id AS product_id
            FROM invoices i
            JOIN carts c ON c.id = i.cart_id
            JOIN cart_items ci ON ci.cart_id = c.id
            LEFT JOIN products p ON p.id = ci.product_id
            WHERE i.number = ?
            ORDER BY ci.id
            """,
            (number,),
        ).fetchall()
        items = []
        for row in rows:
            item = _row_to_dict(row)
            item["total"] = calc_line_total(item.get("unit_price"), item.get("qty"))
            items.append(item)
        return items


def list_sale_invoices_done(q: str = "") -> list[dict[str, Any]]:
    q = q.strip()
    with _connect() as conn:
        if q:
            like = f"%{q}%"
            rows = conn.execute(
                """
                SELECT i.id, i.number, i.created_at, i.total, i.currency,
                       cl.name AS client, c.warehouse_code
                FROM invoices i
                JOIN carts c ON c.id = i.cart_id
                JOIN clients cl ON cl.id = c.client_id
                WHERE CAST(i.number AS TEXT) LIKE ?
                   OR i.created_at LIKE ?
                   OR cl.name LIKE ?
                ORDER BY i.number DESC
                """,
                (like, like, like),
            ).fetchall()
        else:
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

            # Phase 1 — historical cost preservation.
            # Read existing cost_price BEFORE we DELETE, so re-inserted rows for
            # products already in this invoice keep their original snapshot.
            # Otherwise we would overwrite historical purchase cost with today's
            # products.wh_price and every future profit report would be wrong.
            old_items = conn.execute(
                "SELECT product_id, free_line, qty, cost_price FROM cart_items WHERE cart_id = ?",
                (cart_id,),
            ).fetchall()
            old_cost_by_pid = {
                oi["product_id"]: float(oi["cost_price"] or 0)
                for oi in old_items
                if oi["product_id"] and not oi["free_line"]
            }
            cart_wh = conn.execute(
                "SELECT warehouse_code FROM carts WHERE id = ?", (cart_id,)
            ).fetchone()["warehouse_code"]
            for oi in old_items:
                if oi["free_line"] or not oi["product_id"]:
                    continue  # free items don't affect stock
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

            for item in new_items:
                pid = item.get("product_id") or None
                free_line = 1 if not pid else 0
                free_name = item.get("free_name", "") if free_line else ""
                qty = float(item["qty"])
                unit_price = _normalize_unit_price(float(item["unit_price"]))
                item_total = calc_line_total(unit_price, qty)
                # Phase 1 cost_price resolution:
                # - free line → 0 (no cost data)
                # - product was already in this invoice → keep old snapshot
                # - product is newly added → snapshot current wh_price
                if free_line or not pid:
                    cost_price = 0.0
                elif pid in old_cost_by_pid:
                    cost_price = old_cost_by_pid[pid]
                else:
                    row = conn.execute(
                        "SELECT wh_price FROM products WHERE id = ?", (pid,)
                    ).fetchone()
                    cost_price = float(row["wh_price"]) if row else 0.0
                conn.execute(
                    "INSERT INTO cart_items"
                    " (cart_id, product_id, free_line, free_name, qty, price_mode, unit_price, total, cost_price)"
                    " VALUES (?, ?, ?, ?, ?, 'custom', ?, ?, ?)",
                    (cart_id, pid, free_line, free_name, qty, unit_price, item_total, cost_price),
                )
                if pid and not free_line:
                    conn.execute(
                        "UPDATE stock SET qty = qty - ?"
                        " WHERE warehouse_code = ? AND product_id = ?",
                        (qty, warehouse_code, pid),
                    )

            total = calc_document_total(new_items, "unit_price")
            conn.execute(
                "UPDATE invoices SET total = ? WHERE cart_id = ?", (total, cart_id)
            )
            conn.commit()
        return True, ""
    except Exception as exc:
        return False, str(exc)


def delete_sale_invoice(number: int) -> tuple[bool, str]:
    """Delete a completed sale invoice: restore stock, remove items, delete invoice."""
    try:
        with _connect() as conn:
            inv_row = conn.execute(
                "SELECT i.id, i.cart_id FROM invoices i WHERE i.number = ?",
                (number,),
            ).fetchone()
            if not inv_row:
                return False, "invoice_not_found"
            inv_id = inv_row["id"]
            cart_id = inv_row["cart_id"]

            cart_row = conn.execute(
                "SELECT warehouse_code FROM carts WHERE id = ?", (cart_id,)
            ).fetchone()
            if not cart_row:
                return False, "cart_not_found"
            warehouse_code = cart_row["warehouse_code"]

            items = conn.execute(
                "SELECT product_id, free_line, qty FROM cart_items WHERE cart_id = ?",
                (cart_id,),
            ).fetchall()

            for item in items:
                if item["free_line"] or not item["product_id"]:
                    continue  # free items don't affect stock
                conn.execute(
                    "UPDATE stock SET qty = qty + ?"
                    " WHERE warehouse_code = ? AND product_id = ?",
                    (item["qty"], warehouse_code, item["product_id"]),
                )
                conn.execute(
                    "INSERT INTO stock_ops"
                    " (op_type, source, warehouse_code, product_id, qty)"
                    " VALUES ('SALE_CANCEL', 'ADMIN', ?, ?, ?)",
                    (warehouse_code, item["product_id"], item["qty"]),
                )

            conn.execute("DELETE FROM cart_items WHERE cart_id = ?", (cart_id,))
            conn.execute("DELETE FROM invoices WHERE id = ?", (inv_id,))
            conn.execute(
                "UPDATE carts SET status = 'CANCELLED' WHERE id = ?", (cart_id,)
            )
            conn.commit()
        return True, ""
    except Exception as exc:
        return False, str(exc)


# ── Receive invoices ──────────────────────────────────────────────────────────


def receive_invoice_get_open() -> Optional[dict[str, Any]]:
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT ri.*, COALESCE(s.name, ri.supplier, '') AS supplier
            FROM receive_invoices ri
            LEFT JOIN suppliers s ON s.id = ri.supplier_id
            WHERE ri.status = 'OPEN'
            ORDER BY ri.id DESC
            LIMIT 1
            """
        ).fetchone()
        return _row_to_dict(row) if row else None


def receive_invoice_start(
    supplier: str,
    destination_warehouse: str,
    note: str = "",
    supplier_id: Optional[int] = None,
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
            supplier_name = supplier.strip()
            supplier_fk: Optional[int] = int(supplier_id) if supplier_id else None
            if supplier_fk:
                sup_row = conn.execute(
                    "SELECT name FROM suppliers WHERE id = ?",
                    (supplier_fk,),
                ).fetchone()
                if not sup_row:
                    return False, "supplier_not_found", 0
                supplier_name = str(sup_row["name"] or "").strip()
            conn.execute(
                "INSERT INTO receive_invoices"
                " (number, supplier, supplier_id, destination_warehouse, note)"
                " VALUES (?, ?, ?, ?, ?)",
                (next_num, supplier_name, supplier_fk, destination_warehouse.strip(), note.strip()),
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
            purchase_price = _normalize_unit_price(purchase_price)
            total = calc_line_total(purchase_price, qty)
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
            purchase_price = _normalize_unit_price(purchase_price)
            total = calc_line_total(purchase_price, qty)
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
            invoice_total = calc_document_total(list(items), "purchase_price")
            conn.execute(
                """
                UPDATE receive_invoices
                SET status = 'DONE',
                    total = ?
                WHERE id = ?
                """,
                (invoice_total, invoice_id),
            )
            conn.commit()
        return True, ""
    except Exception as exc:
        return False, str(exc)


def receive_invoice_get(invoice_id: int) -> Optional[dict[str, Any]]:
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT ri.*, COALESCE(s.name, ri.supplier, '') AS supplier
            FROM receive_invoices ri
            LEFT JOIN suppliers s ON s.id = ri.supplier_id
            WHERE ri.id = ?
            """,
            (invoice_id,),
        ).fetchone()
        return _row_to_dict(row) if row else None


def receive_invoice_get_items(invoice_id: int) -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT ri.id, ri.qty, ri.purchase_price, ri.total,
                   p.brand, p.model, p.name, p.barcode, p.id AS product_id
            FROM receive_items ri
            JOIN products p ON p.id = ri.product_id
            WHERE ri.invoice_id = ?
            ORDER BY ri.id
            """,
            (invoice_id,),
        ).fetchall()
        items = []
        for row in rows:
            item = _row_to_dict(row)
            item["total"] = calc_line_total(item.get("purchase_price"), item.get("qty"))
            items.append(item)
        return items


def list_receive_invoices_done(q: str = "") -> list[dict[str, Any]]:
    q = q.strip()
    with _connect() as conn:
        if q:
            like = f"%{q}%"
            rows = conn.execute(
                """
                SELECT ri.*, COALESCE(s.name, ri.supplier, '') AS supplier
                FROM receive_invoices ri
                LEFT JOIN suppliers s ON s.id = ri.supplier_id
                WHERE ri.status = 'DONE'
                  AND (
                      CAST(ri.number AS TEXT) LIKE ?
                      OR ri.created_at LIKE ?
                      OR COALESCE(s.name, ri.supplier, '') LIKE ?
                      OR ri.destination_warehouse LIKE ?
                  )
                ORDER BY ri.number DESC
                """,
                (like, like, like, like),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT ri.*, COALESCE(s.name, ri.supplier, '') AS supplier
                FROM receive_invoices ri
                LEFT JOIN suppliers s ON s.id = ri.supplier_id
                WHERE ri.status = 'DONE'
                ORDER BY ri.number DESC
                """
            ).fetchall()
        return [_row_to_dict(r) for r in rows]


def list_receive_suppliers() -> list[str]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT name FROM suppliers WHERE archived = 0 ORDER BY name"
        ).fetchall()
        if rows:
            return [r["name"] for r in rows]
        legacy_rows = conn.execute(
            "SELECT DISTINCT supplier FROM receive_invoices"
            " WHERE supplier != '' ORDER BY supplier"
        ).fetchall()
        return [r["supplier"] for r in legacy_rows]


def get_receive_invoice_items_for_edit(invoice_id: int) -> list[dict[str, Any]]:
    return receive_invoice_get_items(invoice_id)


def update_receive_invoice(
    invoice_id: int,
    supplier: str,
    destination_warehouse: str,
    new_items: list[dict[str, Any]],
    supplier_id: Optional[int] = None,
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

            supplier_name = supplier.strip()
            supplier_fk: Optional[int] = int(supplier_id) if supplier_id else None
            if supplier_fk:
                sup_row = conn.execute(
                    "SELECT name FROM suppliers WHERE id = ?",
                    (supplier_fk,),
                ).fetchone()
                if not sup_row:
                    return False, "supplier_not_found"
                supplier_name = str(sup_row["name"] or "").strip()
            elif not supplier_name:
                supplier_name = str(inv["supplier"] or "").strip()

            conn.execute(
                "UPDATE receive_invoices SET supplier = ?, supplier_id = ?, destination_warehouse = ?"
                " WHERE id = ?",
                (supplier_name, supplier_fk, destination_warehouse.strip(), invoice_id),
            )
            conn.execute("DELETE FROM receive_items WHERE invoice_id = ?", (invoice_id,))

            for item in new_items:
                qty = float(item["qty"])
                pp = _normalize_unit_price(float(item["purchase_price"]))
                total = calc_line_total(pp, qty)
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

            invoice_total = calc_document_total(new_items, "purchase_price")
            conn.execute(
                "UPDATE receive_invoices SET total = ? WHERE id = ?",
                (invoice_total, invoice_id),
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
            unit_price = _normalize_unit_price(unit_price)
            total = calc_line_total(unit_price, qty)
            # Phase 1: snapshot current wh_price as cost at the moment of return.
            # This is an approximation — we don't look up the original sale.
            wh_row = conn.execute(
                "SELECT wh_price FROM products WHERE id = ?", (product_id,)
            ).fetchone()
            cost_price = float(wh_row["wh_price"]) if wh_row else 0.0
            conn.execute(
                "INSERT INTO return_items"
                " (invoice_id, product_id, qty, unit_price, total, cost_price)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (invoice_id, product_id, qty, unit_price, total, cost_price),
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
            unit_price = _normalize_unit_price(unit_price)
            old = conn.execute(
                "SELECT total, invoice_id FROM return_items WHERE id = ?", (item_id,)
            ).fetchone()
            new_total = calc_line_total(unit_price, qty)
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
                # Free-line items have no product_id and don't live in stock - skip.
                if item["product_id"] is None or item["free_line"]:
                    continue
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
            return_total = round(float(inv["total"] or 0), 2)
            if return_total > 0:
                number = int(inv["number"]) if inv["number"] is not None else 0
                conn.execute(
                    "INSERT INTO client_ledger (client_id, amount, note)"
                    " VALUES (?, ?, ?)",
                    (
                        inv["client_id"],
                        return_total,
                        f"RETURN #{number:06d}",
                    ),
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
                   ri.free_line, ri.free_name,
                   p.brand, p.model, p.name, p.barcode, p.id AS product_id
            FROM return_items ri
            LEFT JOIN products p ON p.id = ri.product_id
            WHERE ri.invoice_id = ?
            ORDER BY ri.id
            """,
            (invoice_id,),
        ).fetchall()
        items = []
        for row in rows:
            item = _row_to_dict(row)
            item["free_line"] = bool(item.get("free_line"))
            item["free_name"] = item.get("free_name") or ""
            item["total"] = calc_line_total(item.get("unit_price"), item.get("qty"))
            items.append(item)
        return items


def return_invoice_add_free_item(
    invoice_id: int,
    free_name: str,
    qty: float,
    unit_price: float,
) -> tuple[bool, str]:
    """Add a free/manual line item to a return invoice (no stock product)."""
    try:
        free_name = free_name.strip()
        if not free_name:
            return False, "free_name_required"
        if qty <= 0:
            return False, "qty_must_be_positive"
        if unit_price < 0:
            return False, "price_must_be_non_negative"
        unit_price = _normalize_unit_price(unit_price)
        total = calc_line_total(unit_price, qty)
        with _connect() as conn:
            inv = conn.execute(
                "SELECT id FROM return_invoices WHERE id = ? AND status = 'OPEN'",
                (invoice_id,),
            ).fetchone()
            if not inv:
                return False, "invoice_not_open"
            # Phase 1: free-line return item has no product → cost_price = 0.
            conn.execute(
                "INSERT INTO return_items"
                " (invoice_id, product_id, free_line, free_name, qty, unit_price, total, cost_price)"
                " VALUES (?, NULL, 1, ?, ?, ?, ?, 0)",
                (invoice_id, free_name, qty, unit_price, total),
            )
            # Keep invoice total in sync
            conn.execute(
                "UPDATE return_invoices SET total = COALESCE((SELECT SUM(total) FROM return_items WHERE invoice_id = ?), 0) WHERE id = ?",
                (invoice_id, invoice_id),
            )
            conn.commit()
        return True, ""
    except Exception as exc:
        return False, str(exc)


def list_return_invoices_done(q: str = "") -> list[dict[str, Any]]:
    q = q.strip()
    with _connect() as conn:
        if q:
            like = f"%{q}%"
            rows = conn.execute(
                """
                SELECT ri.*, cl.name AS client_name
                FROM return_invoices ri
                JOIN clients cl ON cl.id = ri.client_id
                WHERE ri.status = 'DONE'
                  AND (
                      CAST(ri.number AS TEXT) LIKE ?
                      OR ri.created_at LIKE ?
                      OR cl.name LIKE ?
                      OR ri.warehouse_code LIKE ?
                  )
                ORDER BY ri.number DESC
                """,
                (like, like, like, like),
            ).fetchall()
        else:
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

            # Phase 1 — preserve historical cost_price across edits.
            old_return_items = conn.execute(
                "SELECT product_id, qty, cost_price FROM return_items WHERE invoice_id = ?",
                (invoice_id,),
            ).fetchall()
            old_ret_cost_by_pid = {
                r["product_id"]: float(r["cost_price"] or 0)
                for r in old_return_items
                if r["product_id"]
            }
            if inv["status"] == "DONE":
                for oi in old_return_items:
                    if not oi["product_id"]:
                        continue
                    conn.execute(
                        "UPDATE stock SET qty = qty - ?"
                        " WHERE warehouse_code = ? AND product_id = ?",
                        (oi["qty"], inv["warehouse_code"], oi["product_id"]),
                    )

            conn.execute(
                "UPDATE return_invoices"
                " SET client_id = ?, warehouse_code = ?, total = 0 WHERE id = ?",
                (client_id, warehouse_code.strip(), invoice_id),
            )
            conn.execute("DELETE FROM return_items WHERE invoice_id = ?", (invoice_id,))

            for item in new_items:
                qty = float(item["qty"])
                up = _normalize_unit_price(float(item["unit_price"]))
                item_total = calc_line_total(up, qty)
                pid = item.get("product_id")
                # Phase 1 cost_price for return: keep old snapshot if this
                # product was already in the return, else use current wh_price.
                if not pid:
                    cost_price = 0.0
                elif pid in old_ret_cost_by_pid:
                    cost_price = old_ret_cost_by_pid[pid]
                else:
                    wh_row = conn.execute(
                        "SELECT wh_price FROM products WHERE id = ?", (pid,)
                    ).fetchone()
                    cost_price = float(wh_row["wh_price"]) if wh_row else 0.0
                conn.execute(
                    "INSERT INTO return_items"
                    " (invoice_id, product_id, qty, unit_price, total, cost_price)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (invoice_id, pid, qty, up, item_total, cost_price),
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

            total = calc_document_total(new_items, "unit_price")
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
        sale_clauses: list[str] = ["c.status = 'CLOSED'"]
        sale_params: list[Any] = []
        if like:
            sale_clauses.append(
                "(COALESCE(p.brand, '') LIKE ? OR COALESCE(p.model, '') LIKE ?"
                " OR COALESCE(p.name, ci.free_name) LIKE ?"
                " OR ci.free_name LIKE ?"
                " OR cl.name LIKE ? OR c.warehouse_code LIKE ?)"
            )
            sale_params = [like, like, like, like, like, like]
        sale_where = "WHERE " + " AND ".join(sale_clauses)
        for r in conn.execute(
            f"""
            SELECT i.number, i.created_at, ci.qty, ci.unit_price, ci.total,
                   ci.free_line, ci.free_name,
                   COALESCE(p.brand, '') AS brand,
                   COALESCE(p.model, '') AS model,
                   COALESCE(p.name, ci.free_name) AS name,
                   p.id AS product_id,
                   cl.name AS client_name, c.warehouse_code
            FROM cart_items ci
            JOIN carts c ON c.id = ci.cart_id
            JOIN invoices i ON i.cart_id = c.id
            LEFT JOIN products p ON p.id = ci.product_id
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

_ALLOWED_MARKUPS: list[int] = [5, 10, 15, 20, 25, 30]
_FALLBACK_MARKUP_PRESETS: list[int] = [10, 15, 25]
_FALLBACK_DEFAULT_MARKUP: int = 15


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


def get_sale_markup_presets() -> list[int]:
    """Return the list of active sale markup percentages from settings.

    Falls back to [10, 15, 25] when the setting is missing or invalid.
    Always returns a non-empty sorted list of values from _ALLOWED_MARKUPS.
    """
    raw = get_setting("sale_markup_presets", "")
    if raw:
        try:
            parsed = [int(x.strip()) for x in raw.split(",") if x.strip().isdigit()]
            valid = sorted({p for p in parsed if p in _ALLOWED_MARKUPS})
            if valid:
                return valid
        except (ValueError, TypeError):
            pass
    return list(_FALLBACK_MARKUP_PRESETS)


def get_sale_default_markup() -> int:
    """Return the default sale markup percentage from settings.

    Falls back to 15 (or the first active preset) when the setting is missing
    or the stored value is not among the active presets.
    """
    presets = get_sale_markup_presets()
    raw = get_setting("sale_default_markup", "")
    if raw:
        try:
            val = int(raw)
            if val in presets:
                return val
        except (ValueError, TypeError):
            pass
    if _FALLBACK_DEFAULT_MARKUP in presets:
        return _FALLBACK_DEFAULT_MARKUP
    return presets[0]


def get_pocket_price_tmt_rate() -> float:
    """Return the USD → TMT exchange rate for Pocket Price display.

    Falls back to 19.50 when the setting is missing or invalid.
    """
    raw = get_setting("pocket_price_tmt_rate", "")
    if raw:
        try:
            val = float(raw)
            if val > 0:
                return val
        except (ValueError, TypeError):
            pass
    return 19.50


def get_pocket_price_show_tmt() -> bool:
    """Return whether TMT prices should be shown in Pocket Price.

    Falls back to False when the setting is missing.
    """
    return get_setting("pocket_price_show_tmt", "0") == "1"


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


def set_price_token_show_qty(token_id: int, show_qty: bool) -> tuple[bool, str]:
    """Enable or disable qty display for a price token."""
    try:
        with _connect() as conn:
            _ensure_price_tokens_show_qty_column(conn)
            conn.execute(
                "UPDATE price_tokens SET show_qty = ? WHERE id = ?",
                (1 if show_qty else 0, token_id),
            )
            conn.commit()
        return True, ""
    except Exception as exc:
        return False, str(exc)


def set_price_token_show_buy_price(token_id: int, show_buy_price: bool) -> tuple[bool, str]:
    """Enable or disable purchase price display for a price token."""
    try:
        with _connect() as conn:
            _ensure_price_tokens_show_buy_price_column(conn)
            conn.execute(
                "UPDATE price_tokens SET show_buy_price = ? WHERE id = ?",
                (1 if show_buy_price else 0, token_id),
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


def get_product_total_qty(product_id: int) -> float:
    """Return the total stock quantity for a product summed across all warehouses."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(qty), 0) AS total FROM stock WHERE product_id = ?",
            (product_id,),
        ).fetchone()
        return float(row["total"]) if row else 0.0


def search_products_for_price(
    q: str, limit: int = 30, mode: str = "SIMPLE", show_qty: bool = False,
    show_buy_price: bool = False,
) -> list[dict[str, Any]]:
    """Search non-archived products for Pocket Price by brand/model/name/barcode.

    Args:
        q: Search query string.
        limit: Maximum number of results (default 30).
        mode: ``"SIMPLE"`` returns only the safe minimal subset (id, brand,
              model, name, barcode, price_wh25); ``"FULL"`` additionally returns
              price_wh10, note, and optionally buy_price.
        show_qty: When ``True`` each product dict will include a ``qty_total``
                  field with the sum of stock qty across all warehouses.
        show_buy_price: When ``True`` **and** mode is ``"FULL"``, each product
                        dict will include a ``buy_price`` field with the
                        purchase (warehouse) price.  Ignored in SIMPLE mode.

    Returns:
        List of product dicts with computed price fields filtered by *mode*.
    """
    with _connect() as conn:
        q_stripped = (q or "").strip()
        like_all    = f"%{q_stripped}%"
        like_prefix = f"{q_stripped}%"

        # Barcode search rules:
        # - only for purely numeric queries of length >= 6 (a barcode fragment
        #   shorter than that gives huge amounts of false positives — EAN-13
        #   codes are 13 digits, so 2–3 random digits appear in most of them);
        # - only prefix match (LIKE 'q%'), never LIKE '%q%'. A shopper typing
        #   part of a barcode always types the beginning.
        include_barcode = q_stripped.isdigit() and len(q_stripped) >= 6

        # Priority-based ordering makes sure the most relevant matches
        # survive the LIMIT cut:
        #   1 = model equals the query exactly (rare, but perfect)
        #   2 = model starts with the query          (e.g. "80" → "8081")
        #   3 = model contains the query somewhere   (e.g. "80" → "sf-2080")
        #   4 = brand or name contains the query
        #   5 = barcode prefix match (only when include_barcode)
        if include_barcode:
            sql = """
                SELECT id, brand, model, name, wh_price, barcode, note,
                       CASE
                         WHEN LOWER(model) = LOWER(?)                THEN 1
                         WHEN LOWER(model) LIKE LOWER(?)             THEN 2
                         WHEN LOWER(model) LIKE LOWER(?)             THEN 3
                         WHEN LOWER(brand) LIKE LOWER(?)             THEN 4
                         WHEN LOWER(name)  LIKE LOWER(?)             THEN 4
                         WHEN barcode LIKE ?                         THEN 5
                         ELSE 9
                       END AS _prio
                FROM products
                WHERE archived = 0
                  AND (   LOWER(brand) LIKE LOWER(?)
                       OR LOWER(model) LIKE LOWER(?)
                       OR LOWER(name)  LIKE LOWER(?)
                       OR barcode LIKE ?)
                ORDER BY _prio, brand, model
                LIMIT ?
            """
            params = (
                q_stripped, like_prefix, like_all,     # priority 1/2/3
                like_all, like_all, like_prefix,       # priority 4/4/5
                like_all, like_all, like_all, like_prefix,  # WHERE
                limit,
            )
        else:
            sql = """
                SELECT id, brand, model, name, wh_price, barcode, note,
                       CASE
                         WHEN LOWER(model) = LOWER(?)    THEN 1
                         WHEN LOWER(model) LIKE LOWER(?) THEN 2
                         WHEN LOWER(model) LIKE LOWER(?) THEN 3
                         WHEN LOWER(brand) LIKE LOWER(?) THEN 4
                         WHEN LOWER(name)  LIKE LOWER(?) THEN 4
                         ELSE 9
                       END AS _prio
                FROM products
                WHERE archived = 0
                  AND (   LOWER(brand) LIKE LOWER(?)
                       OR LOWER(model) LIKE LOWER(?)
                       OR LOWER(name)  LIKE LOWER(?))
                ORDER BY _prio, brand, model
                LIMIT ?
            """
            params = (
                q_stripped, like_prefix, like_all,
                like_all, like_all,
                like_all, like_all, like_all,
                limit,
            )
        rows = conn.execute(sql, params).fetchall()
        result = []
        qty_map: dict[int, float] = {}
        if show_qty and rows:
            product_ids = [r["id"] for r in rows]
            placeholders = ",".join("?" * len(product_ids))
            qty_rows = conn.execute(
                f"SELECT product_id, COALESCE(SUM(qty), 0) AS total"
                f" FROM stock WHERE product_id IN ({placeholders}) GROUP BY product_id",
                product_ids,
            ).fetchall()
            qty_map = {r["product_id"]: float(r["total"]) for r in qty_rows}
        for r in rows:
            is_full = (mode or "SIMPLE").upper() == "FULL"
            wh = float(r["wh_price"] or 0) if (show_buy_price and is_full) else None
            product = _apply_price_mode(dict(r), mode)
            if show_qty:
                product["qty_total"] = qty_map.get(product["id"], 0.0)
            if show_buy_price and is_full:
                product["buy_price"] = wh
            result.append(product)
        return result


# ── Catalog tokens ──────────────────────────────────────────────────────────


def create_catalog_token(label: str = "") -> tuple[str, dict[str, Any]]:
    """Create a new catalog token. Returns (plain_token, row_dict)."""
    plain = secrets.token_urlsafe(32)
    token_hash = _hash_token(plain)
    with _connect() as conn:
        _ensure_catalog_tokens_table(conn)
        conn.execute(
            "INSERT INTO catalog_tokens (label, token_hash, plain_token) VALUES (?, ?, ?)",
            (label.strip(), token_hash, plain),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM catalog_tokens WHERE token_hash = ?", (token_hash,)
        ).fetchone()
        return plain, _row_to_dict(row)


def list_catalog_tokens() -> list[dict[str, Any]]:
    with _connect() as conn:
        _ensure_catalog_tokens_table(conn)
        rows = conn.execute(
            "SELECT * FROM catalog_tokens ORDER BY id DESC"
        ).fetchall()
        return [_row_to_dict(r) for r in rows]


def validate_catalog_token(plain: str) -> Optional[dict[str, Any]]:
    token_hash = _hash_token(plain)
    with _connect() as conn:
        _ensure_catalog_tokens_table(conn)
        row = conn.execute(
            "SELECT * FROM catalog_tokens WHERE token_hash = ? AND revoked = 0",
            (token_hash,),
        ).fetchone()
        return _row_to_dict(row) if row else None


def touch_catalog_token(token_id: int) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE catalog_tokens"
            " SET last_used_at = datetime('now','localtime') WHERE id = ?",
            (token_id,),
        )
        conn.commit()


def bind_catalog_token_device(token_id: int, device_id: str) -> None:
    """Bind a catalog token to a device UUID (only if not already bound)."""
    with _connect() as conn:
        conn.execute(
            "UPDATE catalog_tokens SET device_id = ? WHERE id = ? AND device_id IS NULL",
            (device_id, token_id),
        )
        conn.commit()


def revoke_catalog_token(token_id: int) -> tuple[bool, str]:
    try:
        with _connect() as conn:
            conn.execute(
                "UPDATE catalog_tokens SET revoked = 1, device_id = NULL WHERE id = ?",
                (token_id,),
            )
            conn.commit()
        return True, ""
    except Exception as exc:
        return False, str(exc)


def delete_catalog_token(token_id: int) -> tuple[bool, str]:
    try:
        with _connect() as conn:
            conn.execute("DELETE FROM catalog_tokens WHERE id = ?", (token_id,))
            conn.commit()
        return True, ""
    except Exception as exc:
        return False, str(exc)

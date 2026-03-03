from __future__ import annotations

import os
import re
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
        _ensure_client_ledger_table(conn)
        _ensure_product_extra_columns(conn)
        _migrate_localtime_defaults(conn)
        _ensure_return_tables(conn)
        for code, title in WAREHOUSES.items():
            conn.execute(
                "INSERT OR IGNORE INTO warehouses(code, title) VALUES(?, ?)",
                (code, title),
            )
        conn.commit()
        
        seed_brands_from_products()
        
    finally:
        conn.close()


# -------- warehouses --------

def list_warehouses() -> list[dict[str, Any]]:
    """Return all warehouses from DB as list of {code, title}."""
    conn = _connect()
    try:
        rows = conn.execute("SELECT code, title FROM warehouses ORDER BY code").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def add_warehouse(code: str, title: str) -> tuple[bool, str]:
    """Add a new warehouse. Returns (ok, err)."""
    code = (code or "").strip().upper()
    title = (title or "").strip()
    if not code:
        return False, "Warehouse code is required"
    if not title:
        return False, "Warehouse name is required"
    if not re.match(r'^[A-Z0-9_]+$', code):
        return False, "Warehouse code must contain only letters, digits, and underscores"
    conn = _connect()
    try:
        conn.execute("INSERT INTO warehouses(code, title) VALUES(?, ?)", (code, title))
        conn.commit()
        return True, ""
    except sqlite3.IntegrityError:
        return False, f"Warehouse with code '{code}' already exists"
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()


def _is_valid_warehouse_code(conn: sqlite3.Connection, code: str) -> bool:
    """Check if a warehouse code exists in the DB."""
    row = conn.execute("SELECT 1 FROM warehouses WHERE code=?", (code,)).fetchone()
    return row is not None


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


def list_clients(include_archived: bool = False) -> list[dict[str, Any]]:
    conn = _connect()
    try:
        if include_archived:
            rows = conn.execute(
                "SELECT id, name, phone, note, archived FROM clients ORDER BY name"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, name, phone, note, archived FROM clients WHERE archived=0 ORDER BY name"
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


def set_client_archived(client_id: int, archived: int) -> tuple[bool, str]:
    """Set archived=1 (archive) or archived=0 (unarchive) for a client."""
    conn = _connect()
    try:
        r = conn.execute("SELECT id FROM clients WHERE id=?", (int(client_id),)).fetchone()
        if not r:
            return False, "Client not found"
        conn.execute(
            "UPDATE clients SET archived=? WHERE id=?",
            (int(archived), int(client_id)),
        )
        conn.commit()
        return True, ""
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()


def add_client_adjustment(client_id: int, amount: float, note: str = "") -> tuple[bool, str]:
    """Add a ledger entry that reduces client debt by `amount`.
    Positive amount = payment/write-off (reduces debt).
    Amount is allowed to push the balance negative (advance).
    """
    amount = float(amount)
    if amount <= 0:
        return False, "amount must be > 0"
    note = (note or "").strip()
    conn = _connect()
    try:
        r = conn.execute("SELECT id FROM clients WHERE id=?", (int(client_id),)).fetchone()
        if not r:
            return False, "Client not found"
        conn.execute(
            "INSERT INTO client_ledger(client_id, amount, note) VALUES(?, ?, ?)",
            (int(client_id), amount, note),
        )
        conn.commit()
        return True, ""
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()


def get_client_balance(client_id: int) -> float:
    """Return client balance (positive = owes money, negative = advance).
    balance = sum of SALE invoice totals - sum of RETURN invoice totals - sum of ledger adjustments.
    """
    conn = _connect()
    try:
        debt_row = conn.execute(
            """
            SELECT COALESCE(SUM(inv.total), 0) AS total_debt
            FROM invoices inv
            JOIN carts c ON c.id = inv.cart_id
            WHERE c.client_id = ?
            """,
            (int(client_id),),
        ).fetchone()
        paid_row = conn.execute(
            "SELECT COALESCE(SUM(amount), 0) AS total_paid FROM client_ledger WHERE client_id=?",
            (int(client_id),),
        ).fetchone()
        return_row = conn.execute(
            """
            SELECT COALESCE(SUM(total), 0) AS total_returned
            FROM return_invoices
            WHERE client_id = ? AND status = 'DONE'
            """,
            (int(client_id),),
        ).fetchone()
        debt = float(debt_row["total_debt"]) if debt_row else 0.0
        paid = float(paid_row["total_paid"]) if paid_row else 0.0
        returned = float(return_row["total_returned"]) if return_row else 0.0
        return round(debt - paid - returned, 2)
    finally:
        conn.close()


def list_clients_with_balance(include_archived: bool = False) -> list[dict[str, Any]]:
    """Return clients list enriched with computed balance field."""
    conn = _connect()
    try:
        if include_archived:
            rows = conn.execute(
                "SELECT id, name, phone, note, archived FROM clients ORDER BY name"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, name, phone, note, archived FROM clients WHERE archived=0 ORDER BY name"
            ).fetchall()
        clients = [dict(r) for r in rows]
    finally:
        conn.close()

    for c in clients:
        c["balance"] = get_client_balance(c["id"])
    return clients


def get_client_history(client_id: int) -> list[dict[str, Any]]:
    """Return combined history of SALE invoices, RETURN invoices, and ledger adjustments for a client,
    sorted by date ascending. Each entry has keys:
    dt, kind ('INVOICE' | 'RETURN' | 'ADJUSTMENT'), ref, amount, note, view_url, download_url.
    Also includes a running balance_after field.
    """
    conn = _connect()
    try:
        invoices = conn.execute(
            """
            SELECT inv.created_at AS dt,
                   'INVOICE' AS kind,
                   inv.number AS ref,
                   inv.total AS amount,
                   '' AS note
            FROM invoices inv
            JOIN carts c ON c.id = inv.cart_id
            WHERE c.client_id = ?
            ORDER BY inv.created_at
            """,
            (int(client_id),),
        ).fetchall()

        returns = conn.execute(
            """
            SELECT created_at AS dt,
                   'RETURN' AS kind,
                   id AS ref,
                   total AS amount,
                   note
            FROM return_invoices
            WHERE client_id = ? AND status = 'DONE'
            ORDER BY created_at
            """,
            (int(client_id),),
        ).fetchall()

        adjustments = conn.execute(
            """
            SELECT created_at AS dt,
                   'ADJUSTMENT' AS kind,
                   id AS ref,
                   amount,
                   note
            FROM client_ledger
            WHERE client_id = ?
            ORDER BY created_at
            """,
            (int(client_id),),
        ).fetchall()
    finally:
        conn.close()

    events: list[dict[str, Any]] = []
    for r in invoices:
        events.append(dict(r))
    for r in returns:
        events.append(dict(r))
    for r in adjustments:
        events.append(dict(r))

    events.sort(key=lambda x: x["dt"])

    running = 0.0
    for ev in events:
        if ev["kind"] == "INVOICE":
            running = round(running + float(ev["amount"]), 2)
            ev["view_url"] = f"/sale/xlsx/view?n={ev['ref']}"
            ev["download_url"] = f"/sale/xlsx?n={ev['ref']}"
        elif ev["kind"] == "RETURN":
            running = round(running - float(ev["amount"]), 2)
            ev["view_url"] = f"/return/xlsx/view?n={ev['ref']}"
            ev["download_url"] = f"/return/xlsx?n={ev['ref']}"
        else:
            running = round(running - float(ev["amount"]), 2)
            ev["view_url"] = ""
            ev["download_url"] = ""
        ev["balance_after"] = running

    return events


def _ensure_clients_columns(conn: sqlite3.Connection) -> None:
    cols = conn.execute("PRAGMA table_info(clients);").fetchall()
    existing = {c["name"] for c in cols}

    if "phone" not in existing:
        conn.execute("ALTER TABLE clients ADD COLUMN phone TEXT NOT NULL DEFAULT '';")
    if "note" not in existing:
        conn.execute("ALTER TABLE clients ADD COLUMN note TEXT NOT NULL DEFAULT '';")
    if "archived" not in existing:
        conn.execute("ALTER TABLE clients ADD COLUMN archived INTEGER NOT NULL DEFAULT 0;")


def _ensure_client_ledger_table(conn: sqlite3.Connection) -> None:
    """Create client_ledger table if it does not exist (migration for existing DBs)."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS client_ledger (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          client_id INTEGER NOT NULL,
          created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
          amount REAL NOT NULL,
          note TEXT NOT NULL DEFAULT '',
          FOREIGN KEY (client_id) REFERENCES clients(id) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_client_ledger_client ON client_ledger(client_id)"
    )


def _migrate_localtime_defaults(conn: sqlite3.Connection) -> None:
    """Migrate carts and invoices to use datetime('now','localtime') DEFAULT for created_at.

    SQLite does not support ALTER COLUMN DEFAULT, so the migration recreates each
    table that still has the old datetime('now') default using the recommended
    rename-based approach (PRAGMA foreign_keys = OFF for the duration).
    Existing row data is copied verbatim; only future rows benefit from the new default.
    """
    migrations = [
        (
            "carts",
            """CREATE TABLE carts_new (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              client_id INTEGER NOT NULL,
              created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
              status TEXT NOT NULL DEFAULT 'OPEN',
              FOREIGN KEY (client_id) REFERENCES clients(id) ON DELETE CASCADE
            )""",
        ),
        (
            "invoices",
            """CREATE TABLE invoices_new (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              cart_id INTEGER NOT NULL UNIQUE,
              number INTEGER NOT NULL UNIQUE,
              created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
              currency TEXT NOT NULL DEFAULT 'USD',
              total REAL NOT NULL,
              FOREIGN KEY (cart_id) REFERENCES carts(id) ON DELETE CASCADE
            )""",
        ),
    ]
    _ALLOWED_TABLES = {"carts", "invoices"}
    for table, create_sql in migrations:
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if row is None:
            continue
        if "localtime" in (row["sql"] or ""):
            continue
        if table not in _ALLOWED_TABLES:
            continue
        # Temporarily disable FK enforcement for safe table recreation
        conn.execute("PRAGMA foreign_keys = OFF")
        try:
            conn.execute(create_sql)
            conn.execute(f"INSERT INTO {table}_new SELECT * FROM {table}")
            conn.execute(f"DROP TABLE {table}")
            conn.execute(f"ALTER TABLE {table}_new RENAME TO {table}")
        finally:
            conn.execute("PRAGMA foreign_keys = ON")


def _ensure_product_extra_columns(conn: sqlite3.Connection) -> None:
    """Add barcode, note, last_purchase_price, archived to products if missing (migration)."""
    cols = conn.execute("PRAGMA table_info(products);").fetchall()
    existing = {c["name"] for c in cols}
    if "barcode" not in existing:
        conn.execute("ALTER TABLE products ADD COLUMN barcode TEXT NOT NULL DEFAULT '';")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_products_barcode ON products(barcode);")
    if "note" not in existing:
        conn.execute("ALTER TABLE products ADD COLUMN note TEXT NOT NULL DEFAULT '';")
    if "last_purchase_price" not in existing:
        conn.execute("ALTER TABLE products ADD COLUMN last_purchase_price REAL;")
    if "archived" not in existing:
        conn.execute("ALTER TABLE products ADD COLUMN archived INTEGER NOT NULL DEFAULT 0;")
    conn.commit()


def _ensure_return_tables(conn: sqlite3.Connection) -> None:
    """Create return_invoices and return_items tables if they do not exist (migration)."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS return_invoices (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          number INTEGER NOT NULL UNIQUE,
          client_id INTEGER NOT NULL,
          warehouse_code TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'OPEN',
          created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
          currency TEXT NOT NULL DEFAULT 'USD',
          total REAL NOT NULL DEFAULT 0,
          note TEXT NOT NULL DEFAULT '',
          FOREIGN KEY (client_id) REFERENCES clients(id) ON DELETE CASCADE,
          FOREIGN KEY (warehouse_code) REFERENCES warehouses(code)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS return_items (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          invoice_id INTEGER NOT NULL,
          product_id INTEGER NOT NULL,
          qty REAL NOT NULL,
          unit_price REAL NOT NULL,
          total REAL NOT NULL,
          FOREIGN KEY (invoice_id) REFERENCES return_invoices(id) ON DELETE CASCADE,
          FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_return_items_invoice ON return_items(invoice_id)"
    )
    conn.commit()


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
        
def list_receive_suppliers(limit: int = 200) -> list[str]:
    """Return unique supplier names from receive_invoices for UI suggestions."""
    init_db()
    conn = _connect()
    try:
        rows = conn.execute(
            """
            SELECT DISTINCT TRIM(supplier) AS supplier
            FROM receive_invoices
            WHERE supplier IS NOT NULL AND TRIM(supplier) != ''
            ORDER BY supplier
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
        return [str(r["supplier"]) for r in rows if r and r["supplier"]]
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

def list_products(include_archived: bool = False) -> list[dict[str, Any]]:
    conn = _connect()
    try:
        if include_archived:
            rows = conn.execute(
                "SELECT id, brand, model, name, wh_price, archived FROM products ORDER BY brand, model"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, brand, model, name, wh_price, archived FROM products WHERE archived=0 ORDER BY brand, model"
            ).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            d["wh10_price"] = round(float(d["wh_price"]) * 1.10, 2)
            out.append(d)
        return out
    finally:
        conn.close()


def set_product_archived(product_id: int, archived: int) -> tuple[bool, str]:
    """Set archived=1 (archive) or archived=0 (unarchive) for a product."""
    conn = _connect()
    try:
        r = conn.execute("SELECT id FROM products WHERE id=?", (int(product_id),)).fetchone()
        if not r:
            return False, "Product not found"
        conn.execute(
            "UPDATE products SET archived=? WHERE id=?",
            (int(archived), int(product_id)),
        )
        conn.commit()
        return True, ""
    except Exception as e:
        return False, str(e)
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


def get_stock_qty(warehouse_code: str, product_id: int) -> float:
    """Return available qty for a product in a given warehouse (public API)."""
    conn = _connect()
    try:
        return _get_stock_qty(conn, warehouse_code.strip().upper(), int(product_id))
    finally:
        conn.close()


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

    product = find_product(brand, model)
    if not product:
        return False, "Товар не найден. Добавь через /product_add"

    conn = _connect()
    try:
        if not _is_valid_warehouse_code(conn, src) or not _is_valid_warehouse_code(conn, dst):
            return False, "Неизвестный склад"
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


def move_stock_by_product_id(src: str, dst: str, product_id: int, qty: float) -> Tuple[bool, str]:
    """Move stock between warehouses using product_id directly."""
    src = src.strip().upper()
    dst = dst.strip().upper()
    qty = float(qty)

    if qty <= 0:
        return False, "QTY должно быть > 0"
    if src == dst:
        return False, "FROM и TO одинаковые"

    conn = _connect()
    try:
        if not _is_valid_warehouse_code(conn, src) or not _is_valid_warehouse_code(conn, dst):
            return False, "Неизвестный склад"
        pid = int(product_id)
        # Verify product exists
        row = conn.execute("SELECT id FROM products WHERE id=?", (pid,)).fetchone()
        if not row:
            return False, "Товар не найден"

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

    if src == dst:
        return False, "FROM и TO одинаковые", 0

    conn = _connect()
    try:
        if not _is_valid_warehouse_code(conn, src) or not _is_valid_warehouse_code(conn, dst):
            return False, "Неизвестный склад", 0
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

        where_parts.append("p.archived=0")
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


def search_products(q: str, limit: int = 30, include_archived: bool = False) -> list[dict[str, Any]]:
    """Search all products (catalog) by brand/model/name, case-insensitive.

    Returns list of dicts: product_id, brand, model, name, last_purchase_price.
    last_purchase_price is taken from products.last_purchase_price, and if NULL,
    falls back to the most recent receive_items.purchase_price for that product.
    """
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
              COALESCE(
                p.last_purchase_price,
                (
                  SELECT ri.purchase_price
                  FROM receive_items ri
                  WHERE ri.product_id = p.id
                  ORDER BY ri.id DESC
                  LIMIT 1
                )
              ) as last_purchase_price
            FROM products p
            WHERE (lower(p.brand) LIKE ? OR lower(p.model) LIKE ? OR lower(p.name) LIKE ?)
              AND (p.archived = 0 OR ? = 1)
            ORDER BY p.brand, p.model
            LIMIT ?
            """,
            (like, like, like, int(include_archived), int(limit)),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


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
              ROUND(p.wh_price * 1.10, 2) as wh10_price,
              ROUND(p.wh_price * 1.25, 2) as wh25_price
            FROM stock s
            JOIN products p ON p.id = s.product_id
            JOIN warehouses w ON w.code = s.warehouse_code
            WHERE w.code = ?
              AND s.qty > 0
              AND p.archived = 0
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
    if price_mode not in ("wh", "wh10", "wh25", "custom"):
        return False, "price_mode должен быть: wh / wh10 / wh25 / custom"

    product = find_product(brand, model)
    if not product:
        return False, "Товар не найден."

    wh_price = float(product["wh_price"])
    if price_mode == "wh":
        unit = round(wh_price, 2)
    elif price_mode == "wh10":
        unit = round(wh_price * 1.10, 2)
    elif price_mode == "wh25":
        unit = round(wh_price * 1.25, 2)
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

    conn = _connect()
    try:
        if not _is_valid_warehouse_code(conn, shop):
            return False, "Неизвестный склад магазина", {}, []
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

# ============================================================
# Receive (purchase) invoices
# ============================================================

def receive_invoice_get_open() -> Optional[dict[str, Any]]:
    """Return the current OPEN receive invoice or None."""
    init_db()
    conn = _connect()
    try:
        r = conn.execute(
            """
            SELECT id, number, supplier, destination_warehouse, status, created_at, note
            FROM receive_invoices
            WHERE status = 'OPEN'
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
        return dict(r) if r else None
    finally:
        conn.close()


def receive_invoice_start(
    supplier: str,
    destination_warehouse: str,
    note: str = "",
) -> tuple[bool, str, Optional[int]]:
    """Create a new OPEN receive invoice. Returns (ok, err, invoice_id)."""
    init_db()
    supplier = (supplier or "").strip()
    destination_warehouse = (destination_warehouse or "").strip().upper()
    note = (note or "").strip()

    if not destination_warehouse:
        return False, "destination_warehouse is required", None

    conn = _connect()
    try:
        existing = conn.execute(
            "SELECT id FROM receive_invoices WHERE status='OPEN' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if existing:
            return False, "There is already an open receive invoice. Finish or cancel it first.", None

        last = conn.execute(
            "SELECT COALESCE(MAX(number), 0) as n FROM receive_invoices"
        ).fetchone()
        num = int(last["n"]) + 1

        cur = conn.execute(
            """
            INSERT INTO receive_invoices(number, supplier, destination_warehouse, status, note)
            VALUES (?, ?, ?, 'OPEN', ?)
            """,
            (num, supplier, destination_warehouse, note),
        )
        conn.commit()
        return True, "", int(cur.lastrowid)
    except Exception as e:
        return False, str(e), None
    finally:
        conn.close()


def receive_invoice_cancel(invoice_id: int) -> tuple[bool, str]:
    """Cancel an OPEN receive invoice (no stock changes)."""
    init_db()
    conn = _connect()
    try:
        r = conn.execute(
            "SELECT id, status FROM receive_invoices WHERE id=?", (int(invoice_id),)
        ).fetchone()
        if not r:
            return False, "Invoice not found"
        if r["status"] != "OPEN":
            return False, "Invoice is not open"
        conn.execute("DELETE FROM receive_items WHERE invoice_id=?", (int(invoice_id),))
        conn.execute(
            "UPDATE receive_invoices SET status='CANCELLED' WHERE id=?", (int(invoice_id),)
        )
        conn.commit()
        return True, ""
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()


def receive_item_add(
    invoice_id: int,
    product_id: int,
    qty: float,
    purchase_price: float,
) -> tuple[bool, str]:
    """Add a line item to an OPEN receive invoice."""
    init_db()
    try:
        qty = float(qty)
        purchase_price = float(purchase_price)
    except Exception:
        return False, "qty and purchase_price must be numbers"
    if qty <= 0:
        return False, "qty must be > 0"
    if purchase_price < 0:
        return False, "purchase_price must be >= 0"

    total = round(qty * purchase_price, 2)
    conn = _connect()
    try:
        r = conn.execute(
            "SELECT id, status FROM receive_invoices WHERE id=?", (int(invoice_id),)
        ).fetchone()
        if not r:
            return False, "Invoice not found"
        if r["status"] != "OPEN":
            return False, "Invoice is not open"
        p = conn.execute(
            "SELECT id FROM products WHERE id=?", (int(product_id),)
        ).fetchone()
        if not p:
            return False, "Product not found"
        conn.execute(
            """
            INSERT INTO receive_items(invoice_id, product_id, qty, purchase_price, total)
            VALUES (?, ?, ?, ?, ?)
            """,
            (int(invoice_id), int(product_id), qty, purchase_price, total),
        )
        conn.commit()
        return True, ""
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()


def receive_item_update(item_id: int, qty: float, purchase_price: float) -> tuple[bool, str]:
    """Update qty and purchase_price for a receive item."""
    init_db()
    try:
        qty = float(qty)
        purchase_price = float(purchase_price)
    except Exception:
        return False, "qty and purchase_price must be numbers"
    if qty <= 0:
        return False, "qty must be > 0"
    if purchase_price < 0:
        return False, "purchase_price must be >= 0"
    total = round(qty * purchase_price, 2)
    conn = _connect()
    try:
        r = conn.execute(
            """
            SELECT ri.id, inv.status
            FROM receive_items ri
            JOIN receive_invoices inv ON inv.id = ri.invoice_id
            WHERE ri.id=?
            """,
            (int(item_id),),
        ).fetchone()
        if not r:
            return False, "Item not found"
        if r["status"] != "OPEN":
            return False, "Invoice is not open"
        conn.execute(
            "UPDATE receive_items SET qty=?, purchase_price=?, total=? WHERE id=?",
            (qty, purchase_price, total, int(item_id)),
        )
        conn.commit()
        return True, ""
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()


def receive_item_delete(item_id: int) -> tuple[bool, str]:
    """Remove a line item from an OPEN receive invoice."""
    init_db()
    conn = _connect()
    try:
        r = conn.execute(
            """
            SELECT ri.id, inv.status
            FROM receive_items ri
            JOIN receive_invoices inv ON inv.id = ri.invoice_id
            WHERE ri.id=?
            """,
            (int(item_id),),
        ).fetchone()
        if not r:
            return False, "Item not found"
        if r["status"] != "OPEN":
            return False, "Invoice is not open"
        conn.execute("DELETE FROM receive_items WHERE id=?", (int(item_id),))
        conn.commit()
        return True, ""
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()


def receive_invoice_finish(invoice_id: int) -> tuple[bool, str]:
    """
    Finish a receive invoice:
    - increase stock in destination_warehouse for each item
    - update last_purchase_price on each product
    - set invoice status to DONE
    """
    init_db()
    conn = _connect()
    try:
        inv = conn.execute(
            "SELECT id, status, destination_warehouse FROM receive_invoices WHERE id=?",
            (int(invoice_id),),
        ).fetchone()
        if not inv:
            return False, "Invoice not found"
        if inv["status"] != "OPEN":
            return False, "Invoice is not open"

        items = conn.execute(
            """
            SELECT ri.id, ri.product_id, ri.qty, ri.purchase_price
            FROM receive_items ri
            WHERE ri.invoice_id=?
            ORDER BY ri.id
            """,
            (int(invoice_id),),
        ).fetchall()
        if not items:
            return False, "No items in invoice"

        wh = inv["destination_warehouse"]
        for item in items:
            pid = int(item["product_id"])
            qty = float(item["qty"])
            pp = float(item["purchase_price"])

            # Upsert stock
            srow = conn.execute(
                "SELECT qty FROM stock WHERE warehouse_code=? AND product_id=?",
                (wh, pid),
            ).fetchone()
            if srow:
                conn.execute(
                    "UPDATE stock SET qty = qty + ? WHERE warehouse_code=? AND product_id=?",
                    (qty, wh, pid),
                )
            else:
                conn.execute(
                    "INSERT INTO stock(warehouse_code, product_id, qty) VALUES(?, ?, ?)",
                    (wh, pid, qty),
                )

            # Update last_purchase_price
            conn.execute(
                "UPDATE products SET last_purchase_price=? WHERE id=?",
                (pp, pid),
            )

        conn.execute(
            "UPDATE receive_invoices SET status='DONE' WHERE id=?",
            (int(invoice_id),),
        )
        conn.commit()
        return True, ""
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()


def receive_invoice_get(invoice_id: int) -> Optional[dict[str, Any]]:
    """Return receive invoice dict by id."""
    init_db()
    conn = _connect()
    try:
        r = conn.execute(
            """
            SELECT id, number, supplier, destination_warehouse, status, created_at, note
            FROM receive_invoices
            WHERE id=?
            """,
            (int(invoice_id),),
        ).fetchone()
        return dict(r) if r else None
    finally:
        conn.close()


def receive_invoice_get_items(invoice_id: int) -> list[dict[str, Any]]:
    """Return line items for a receive invoice."""
    init_db()
    conn = _connect()
    try:
        rows = conn.execute(
            """
            SELECT ri.id, p.brand, p.model, p.name, ri.qty, ri.purchase_price, ri.total
            FROM receive_items ri
            JOIN products p ON p.id = ri.product_id
            WHERE ri.invoice_id=?
            ORDER BY ri.id
            """,
            (int(invoice_id),),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def add_product_simple(
    brand: str,
    model: str,
    name: str,
    barcode: str = "",
    note: str = "",
) -> tuple[bool, str, Optional[int]]:
    """Add a new product (for receive screen). Returns (ok, err, product_id)."""
    init_db()
    brand = (brand or "").strip()
    model = (model or "").strip()
    name = (name or "").strip()
    barcode = (barcode or "").strip()
    note = (note or "").strip()

    if not brand:
        return False, "brand is required", None
    if not model:
        return False, "model is required", None
    if not name:
        return False, "name is required", None

    conn = _connect()
    try:
        existing = conn.execute(
            "SELECT id FROM products WHERE brand=? AND model=?", (brand, model)
        ).fetchone()
        if existing:
            return False, f"Product {brand} {model} already exists", None
        cur = conn.execute(
            """
            INSERT INTO products(brand, model, name, wh_price, barcode, note)
            VALUES (?, ?, ?, 0, ?, ?)
            """,
            # wh_price=0: selling price is not set at receive time; update via Products page later
            (brand, model, name, barcode, note),
        )
        conn.commit()
        # Also seed the brand
        conn.execute("INSERT OR IGNORE INTO brands(name) VALUES (?)", (brand,))
        conn.commit()
        return True, "", int(cur.lastrowid)
    except Exception as e:
        return False, str(e), None
    finally:
        conn.close()


def list_sale_invoices_done(limit: int = 200) -> list[dict[str, Any]]:
    """Return list of completed sale invoices (most recent first)."""
    init_db()
    conn = _connect()
    try:
        rows = conn.execute(
            """
            SELECT inv.id, inv.number, inv.created_at, inv.total, inv.currency,
                   cl.name as client
            FROM invoices inv
            JOIN carts c ON c.id = inv.cart_id
            JOIN clients cl ON cl.id = c.client_id
            ORDER BY inv.number DESC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def list_receive_invoices_done(limit: int = 200) -> list[dict[str, Any]]:
    """Return list of DONE receive invoices with totals (most recent first)."""
    init_db()
    conn = _connect()
    try:
        rows = conn.execute(
            """
            SELECT inv.id, inv.number, inv.supplier, inv.destination_warehouse,
                   inv.created_at,
                   COALESCE(SUM(ri.total), 0) as total
            FROM receive_invoices inv
            LEFT JOIN receive_items ri ON ri.invoice_id = inv.id
            WHERE inv.status = 'DONE'
            GROUP BY inv.id
            ORDER BY inv.number DESC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ============================================================
# Return invoices
# ============================================================

def return_invoice_get_open() -> Optional[dict[str, Any]]:
    """Return the current OPEN return invoice or None."""
    init_db()
    conn = _connect()
    try:
        r = conn.execute(
            """
            SELECT ri.id, ri.number, ri.client_id, ri.warehouse_code, ri.status,
                   ri.created_at, ri.note, cl.name as client_name
            FROM return_invoices ri
            JOIN clients cl ON cl.id = ri.client_id
            WHERE ri.status = 'OPEN'
            ORDER BY ri.id DESC
            LIMIT 1
            """
        ).fetchone()
        return dict(r) if r else None
    finally:
        conn.close()


def return_invoice_start(client_id: int, warehouse_code: str, note: str = "") -> Tuple[bool, str, Optional[int]]:
    """Create a new OPEN return invoice. Only one OPEN invoice allowed at a time."""
    init_db()
    conn = _connect()
    try:
        existing = conn.execute(
            "SELECT id FROM return_invoices WHERE status='OPEN' LIMIT 1"
        ).fetchone()
        if existing:
            return False, "Уже есть открытый возврат. Завершите или отмените его.", None

        last = conn.execute("SELECT COALESCE(MAX(number), 0) as n FROM return_invoices").fetchone()
        num = int(last["n"]) + 1

        cur = conn.execute(
            "INSERT INTO return_invoices(number, client_id, warehouse_code, note) VALUES(?, ?, ?, ?)",
            (num, int(client_id), warehouse_code.strip().upper(), (note or "").strip()),
        )
        conn.commit()
        return True, "", int(cur.lastrowid)
    except Exception as e:
        return False, str(e), None
    finally:
        conn.close()


def return_invoice_cancel(invoice_id: int) -> Tuple[bool, str]:
    """Cancel an OPEN return invoice."""
    init_db()
    conn = _connect()
    try:
        r = conn.execute(
            "SELECT status FROM return_invoices WHERE id=?", (int(invoice_id),)
        ).fetchone()
        if not r:
            return False, "Возврат не найден"
        if r["status"] != "OPEN":
            return False, f"Нельзя отменить возврат со статусом {r['status']}"
        conn.execute(
            "UPDATE return_invoices SET status='CANCELLED' WHERE id=?", (int(invoice_id),)
        )
        conn.commit()
        return True, ""
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()


def return_item_add(invoice_id: int, product_id: int, qty: float, unit_price: float) -> Tuple[bool, str]:
    """Add a line item to an OPEN return invoice."""
    init_db()
    qty = float(qty)
    unit_price = float(unit_price)
    if qty <= 0:
        return False, "Количество должно быть > 0"
    if unit_price < 0:
        return False, "Цена не может быть отрицательной"

    conn = _connect()
    try:
        r = conn.execute(
            "SELECT status FROM return_invoices WHERE id=?", (int(invoice_id),)
        ).fetchone()
        if not r:
            return False, "Возврат не найден"
        if r["status"] != "OPEN":
            return False, "Возврат уже закрыт"

        total = round(qty * unit_price, 2)
        conn.execute(
            "INSERT INTO return_items(invoice_id, product_id, qty, unit_price, total) VALUES(?, ?, ?, ?, ?)",
            (int(invoice_id), int(product_id), qty, unit_price, total),
        )
        conn.commit()
        return True, ""
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()


def return_item_update(item_id: int, qty: float, unit_price: float) -> Tuple[bool, str]:
    """Update qty and price of a return item."""
    init_db()
    qty = float(qty)
    unit_price = float(unit_price)
    if qty <= 0:
        return False, "Количество должно быть > 0"
    if unit_price < 0:
        return False, "Цена не может быть отрицательной"

    conn = _connect()
    try:
        total = round(qty * unit_price, 2)
        n = conn.execute(
            "UPDATE return_items SET qty=?, unit_price=?, total=? WHERE id=?",
            (qty, unit_price, total, int(item_id)),
        ).rowcount
        conn.commit()
        if n == 0:
            return False, "Позиция не найдена"
        return True, ""
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()


def return_item_delete(item_id: int) -> Tuple[bool, str]:
    """Delete a return item."""
    init_db()
    conn = _connect()
    try:
        n = conn.execute("DELETE FROM return_items WHERE id=?", (int(item_id),)).rowcount
        conn.commit()
        if n == 0:
            return False, "Позиция не найдена"
        return True, ""
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()


def return_invoice_finish(invoice_id: int) -> Tuple[bool, str]:
    """Finish a return invoice: add stock to warehouse and set status=DONE."""
    init_db()
    conn = _connect()
    try:
        inv = conn.execute(
            "SELECT id, client_id, warehouse_code, status FROM return_invoices WHERE id=?",
            (int(invoice_id),),
        ).fetchone()
        if not inv:
            return False, "Возврат не найден"
        if inv["status"] != "OPEN":
            return False, f"Возврат уже {inv['status']}"

        items = conn.execute(
            """
            SELECT ri.id, ri.product_id, ri.qty, ri.unit_price, ri.total
            FROM return_items ri
            WHERE ri.invoice_id = ?
            """,
            (int(invoice_id),),
        ).fetchall()

        if not items:
            return False, "Нет позиций в возврате"

        warehouse = inv["warehouse_code"]
        total_sum = round(sum(float(it["total"]) for it in items), 2)

        # Add stock back to warehouse
        for it in items:
            pid = int(it["product_id"])
            qty = float(it["qty"])
            current = _get_stock_qty(conn, warehouse, pid)
            _set_stock_qty(conn, warehouse, pid, current + qty)

        conn.execute(
            "UPDATE return_invoices SET status='DONE', total=? WHERE id=?",
            (total_sum, int(invoice_id)),
        )
        conn.commit()
        return True, ""
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()


def return_invoice_get(invoice_id: int) -> Optional[dict[str, Any]]:
    """Return a return invoice dict by id."""
    init_db()
    conn = _connect()
    try:
        r = conn.execute(
            """
            SELECT ri.id, ri.number, ri.created_at, ri.currency, ri.total,
                   ri.warehouse_code, ri.note,
                   cl.name as client
            FROM return_invoices ri
            JOIN clients cl ON cl.id = ri.client_id
            WHERE ri.id = ?
            """,
            (int(invoice_id),),
        ).fetchone()
        return dict(r) if r else None
    finally:
        conn.close()


def return_invoice_get_items(invoice_id: int) -> list[dict[str, Any]]:
    """Return line items for a return invoice."""
    init_db()
    conn = _connect()
    try:
        rows = conn.execute(
            """
            SELECT p.brand, p.model, p.name, ri.qty, ri.unit_price, ri.total, ri.id
            FROM return_items ri
            JOIN products p ON p.id = ri.product_id
            WHERE ri.invoice_id = ?
            ORDER BY ri.id
            """,
            (int(invoice_id),),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def list_return_invoices_done(limit: int = 200) -> list[dict[str, Any]]:
    """Return list of DONE return invoices (most recent first)."""
    init_db()
    conn = _connect()
    try:
        rows = conn.execute(
            """
            SELECT ri.id, ri.number, ri.created_at, ri.total, ri.currency,
                   ri.warehouse_code, cl.name as client
            FROM return_invoices ri
            JOIN clients cl ON cl.id = ri.client_id
            WHERE ri.status = 'DONE'
            ORDER BY ri.number DESC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_last_sale_price(product_id: int, client_id: Optional[int] = None) -> Optional[float]:
    """Return the last sale unit_price for a product, optionally filtered by client."""
    init_db()
    conn = _connect()
    try:
        if client_id:
            r = conn.execute(
                """
                SELECT ci.unit_price
                FROM cart_items ci
                JOIN carts c ON c.id = ci.cart_id
                JOIN invoices inv ON inv.cart_id = c.id
                WHERE ci.product_id = ? AND c.client_id = ?
                ORDER BY inv.created_at DESC
                LIMIT 1
                """,
                (int(product_id), int(client_id)),
            ).fetchone()
        else:
            r = None
        if not r:
            r = conn.execute(
                """
                SELECT ci.unit_price
                FROM cart_items ci
                JOIN carts c ON c.id = ci.cart_id
                JOIN invoices inv ON inv.cart_id = c.id
                WHERE ci.product_id = ?
                ORDER BY inv.created_at DESC
                LIMIT 1
                """,
                (int(product_id),),
            ).fetchone()
        return float(r["unit_price"]) if r else None
    finally:
        conn.close()


def list_history(q: str = "", limit: int = 500) -> list[dict[str, Any]]:
    """Return merged RECEIVE and SALE line items sorted by datetime desc.

    Each row has normalized keys:
      dt, type, ref, counterparty, warehouse, brand, model, name,
      qty, unit_price, total, view_url, download_url

    ``q`` is split on whitespace into tokens; every token must appear somewhere
    in the concatenated search blob (AND semantics, case-insensitive, partial
    match via LIKE).  For SALE the blob is: client name + brand + model + name
    + invoice number.  For RECEIVE: supplier + brand + model + name + invoice
    id + warehouse.
    """
    init_db()
    conn = _connect()
    try:
        tokens = q.strip().lower().split() if q.strip() else []

        # Build per-token LIKE conditions for each query.
        receive_blob = (
            "lower(coalesce(inv.supplier,'') || ' ' || coalesce(p.brand,'') || ' '"
            " || coalesce(p.model,'') || ' ' || coalesce(p.name,'') || ' '"
            " || coalesce(cast(inv.id as text),'') || ' '"
            " || coalesce(inv.destination_warehouse,''))"
        )
        sale_blob = (
            "lower(coalesce(cl.name,'') || ' ' || coalesce(p.brand,'') || ' '"
            " || coalesce(p.model,'') || ' ' || coalesce(p.name,'') || ' '"
            " || coalesce(cast(inv.number as text),''))"
        )

        # receive_blob / sale_blob are fixed SQL expressions (not user input);
        # token values are passed as parameterized `?` placeholders — no SQL
        # injection risk.
        receive_filter = "".join(
            f" AND {receive_blob} LIKE ?" for _ in tokens
        )
        sale_filter = "".join(
            f" AND {sale_blob} LIKE ?" for _ in tokens
        )
        token_params = [f"%{t}%" for t in tokens]

        # ---- RECEIVE events ----
        receive_rows = conn.execute(
            f"""
            SELECT
                inv.created_at            AS dt,
                'RECEIVE'                 AS type,
                inv.id                    AS ref,
                inv.supplier              AS counterparty,
                inv.destination_warehouse AS warehouse,
                p.brand                   AS brand,
                p.model                   AS model,
                p.name                    AS name,
                ri.qty                    AS qty,
                ri.purchase_price         AS unit_price,
                ri.total                  AS total
            FROM receive_invoices inv
            JOIN receive_items ri ON ri.invoice_id = inv.id
            JOIN products p ON p.id = ri.product_id
            WHERE inv.status = 'DONE'
            {receive_filter}
            """,
            token_params,
        ).fetchall()

        # ---- SALE events ----
        sale_rows = conn.execute(
            f"""
            SELECT
                inv.created_at  AS dt,
                'SALE'          AS type,
                inv.number      AS ref,
                cl.name         AS counterparty,
                NULL            AS warehouse,
                p.brand         AS brand,
                p.model         AS model,
                p.name          AS name,
                ci.qty          AS qty,
                ci.unit_price   AS unit_price,
                ci.total        AS total
            FROM invoices inv
            JOIN carts c ON c.id = inv.cart_id
            JOIN clients cl ON cl.id = c.client_id
            JOIN cart_items ci ON ci.cart_id = c.id
            JOIN products p ON p.id = ci.product_id
            WHERE 1=1  -- sale invoices have no status gate; dynamic filters append here
            {sale_filter}
            """,
            token_params,
        ).fetchall()

        events: list[dict[str, Any]] = []

        for r in receive_rows:
            row = dict(r)
            inv_id = row["ref"]
            row["view_url"] = f"/receive/xlsx/view?n={inv_id}"
            row["download_url"] = f"/receive/xlsx?n={inv_id}"
            events.append(row)

        for r in sale_rows:
            row = dict(r)
            inv_number = row["ref"]
            row["view_url"] = f"/sale/xlsx/view?n={inv_number}"
            row["download_url"] = f"/sale/xlsx?n={inv_number}"
            events.append(row)

        # ---- RETURN events ----
        return_blob = (
            "lower(coalesce(cl.name,'') || ' ' || coalesce(p.brand,'') || ' '"
            " || coalesce(p.model,'') || ' ' || coalesce(p.name,'') || ' '"
            " || coalesce(cast(inv.id as text),'') || ' '"
            " || coalesce(inv.warehouse_code,''))"
        )
        return_filter = "".join(
            f" AND {return_blob} LIKE ?" for _ in tokens
        )

        return_rows = conn.execute(
            f"""
            SELECT
                inv.created_at  AS dt,
                'RETURN'        AS type,
                inv.id          AS ref,
                cl.name         AS counterparty,
                inv.warehouse_code AS warehouse,
                p.brand         AS brand,
                p.model         AS model,
                p.name          AS name,
                ri.qty          AS qty,
                ri.unit_price   AS unit_price,
                ri.total        AS total
            FROM return_invoices inv
            JOIN return_items ri ON ri.invoice_id = inv.id
            JOIN products p ON p.id = ri.product_id
            JOIN clients cl ON cl.id = inv.client_id
            WHERE inv.status = 'DONE'
            {return_filter}
            """,
            token_params,
        ).fetchall()

        for r in return_rows:
            row = dict(r)
            inv_id = row["ref"]
            row["view_url"] = f"/return/xlsx/view?n={inv_id}"
            row["download_url"] = f"/return/xlsx?n={inv_id}"
            events.append(row)

        # Sort by dt descending (ISO datetime strings compare correctly).
        # Rows with a missing/None dt sort to the end (treated as "").
        events.sort(key=lambda x: x.get("dt") or "", reverse=True)

        return events[:limit]
    finally:
        conn.close()

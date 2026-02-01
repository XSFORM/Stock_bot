import os
import sqlite3
from pathlib import Path
from typing import Optional, List, Dict, Tuple

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DB_PATH = BASE_DIR / "stock.db"
SCHEMA_PATH = BASE_DIR / "schema.sql"


def _connect(db_path: str | Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_db(db_path: str | Path = DEFAULT_DB_PATH) -> None:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    schema = SCHEMA_PATH.read_text(encoding="utf-8")
    if not schema.strip():
        raise RuntimeError("schema.sql пустой — таблицы не будут созданы.")

    with _connect(db_path) as conn:
        conn.executescript(schema)
        conn.commit()


def add_client(name: str, db_path: str | Path = DEFAULT_DB_PATH) -> int:
    name = (name or "").strip()
    if not name:
        raise ValueError("Имя клиента пустое")

    with _connect(db_path) as conn:
        cur = conn.execute("INSERT INTO clients(name) VALUES(?)", (name,))
        conn.commit()
        return int(cur.lastrowid)


def list_clients(db_path: str | Path = DEFAULT_DB_PATH) -> List[str]:
    with _connect(db_path) as conn:
        rows = conn.execute("SELECT name FROM clients ORDER BY name").fetchall()
        return [r["name"] for r in rows]


def add_product(
    brand: str,
    model: str,
    name: str,
    wholesale_price: float,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> int:
    brand = (brand or "").strip().lower()
    model = (model or "").strip().lower()
    name = (name or "").strip()
    if not (brand and model and name):
        raise ValueError("brand/model/name обязательны")

    with _connect(db_path) as conn:
        cur = conn.execute(
            """
            INSERT INTO products(brand, model, name, wholesale_price)
            VALUES(?,?,?,?)
            ON CONFLICT(brand, model) DO UPDATE SET
              name=excluded.name,
              wholesale_price=excluded.wholesale_price
            """,
            (brand, model, name, float(wholesale_price)),
        )
        # если был конфликт — lastrowid может быть 0, поэтому найдём id
        row = conn.execute(
            "SELECT id FROM products WHERE brand=? AND model=?",
            (brand, model),
        ).fetchone()
        conn.commit()
        return int(row["id"])


def list_products(db_path: str | Path = DEFAULT_DB_PATH) -> List[Tuple[str, str, str, float]]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT brand, model, name, wholesale_price FROM products ORDER BY brand, model"
        ).fetchall()
        return [(r["brand"], r["model"], r["name"], float(r["wholesale_price"])) for r in rows]


def _get_product_id(brand: str, model: str, db_path: str | Path = DEFAULT_DB_PATH) -> int:
    brand = brand.strip().lower()
    model = model.strip().lower()
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT id FROM products WHERE brand=? AND model=?",
            (brand, model),
        ).fetchone()
        if not row:
            raise ValueError(f"Товар не найден: {brand} {model}")
        return int(row["id"])


def _ensure_stock_row(warehouse: str, product_id: int, db_path: str | Path = DEFAULT_DB_PATH) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO stock(warehouse, product_id, qty) VALUES(?,?,0)",
            (warehouse, product_id),
        )
        conn.commit()


def change_stock(
    warehouse: str,
    brand: str,
    model: str,
    qty_delta: int,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> None:
    pid = _get_product_id(brand, model, db_path)
    _ensure_stock_row(warehouse, pid, db_path)

    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT qty FROM stock WHERE warehouse=? AND product_id=?",
            (warehouse, pid),
        ).fetchone()
        current = int(row["qty"])
        new_qty = current + int(qty_delta)
        if new_qty < 0:
            raise ValueError(f"Недостаточно товара на складе {warehouse}. Сейчас: {current}, нужно: {-qty_delta}")
        conn.execute(
            "UPDATE stock SET qty=?, updated_at=datetime('now') WHERE warehouse=? AND product_id=?",
            (new_qty, warehouse, pid),
        )
        conn.commit()


def move_stock(
    from_wh: str,
    to_wh: str,
    brand: str,
    model: str,
    qty: int,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> None:
    qty = int(qty)
    if qty <= 0:
        raise ValueError("Количество должно быть > 0")

    pid = _get_product_id(brand, model, db_path)
    _ensure_stock_row(from_wh, pid, db_path)
    _ensure_stock_row(to_wh, pid, db_path)

    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT qty FROM stock WHERE warehouse=? AND product_id=?",
            (from_wh, pid),
        ).fetchone()
        cur_qty = int(row["qty"])
        if cur_qty < qty:
            raise ValueError(f"Недостаточно товара на {from_wh}. Сейчас {cur_qty}, нужно {qty}")

        conn.execute(
            "UPDATE stock SET qty=qty-?, updated_at=datetime('now') WHERE warehouse=? AND product_id=?",
            (qty, from_wh, pid),
        )
        conn.execute(
            "UPDATE stock SET qty=qty+?, updated_at=datetime('now') WHERE warehouse=? AND product_id=?",
            (qty, to_wh, pid),
        )
        conn.execute(
            "INSERT INTO movements(from_wh, to_wh, product_id, qty) VALUES(?,?,?,?)",
            (from_wh, to_wh, pid, qty),
        )
        conn.commit()


def get_stock(db_path: str | Path = DEFAULT_DB_PATH, warehouse: Optional[str] = None) -> List[Dict]:
    with _connect(db_path) as conn:
        if warehouse:
            rows = conn.execute(
                """
                SELECT s.warehouse, p.brand, p.model, p.name, s.qty
                FROM stock s
                JOIN products p ON p.id=s.product_id
                WHERE s.warehouse=?
                ORDER BY p.brand, p.model
                """,
                (warehouse,),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT s.warehouse, p.brand, p.model, p.name, s.qty
                FROM stock s
                JOIN products p ON p.id=s.product_id
                ORDER BY s.warehouse, p.brand, p.model
                """
            ).fetchall()
        return [dict(r) for r in rows]

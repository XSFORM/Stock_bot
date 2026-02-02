from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional, Tuple, Any

from app.constants import WAREHOUSES


BASE_DIR = Path(__file__).resolve().parents[1]  # .../app
DB_PATH = BASE_DIR / "db" / "stock.db"
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
        # Warehouses seed
        for code, title in WAREHOUSES.items():
            conn.execute(
                "INSERT OR IGNORE INTO warehouses(code, title) VALUES(?, ?)",
                (code, title),
            )
        conn.commit()
    finally:
        conn.close()


def ensure_admin() -> None:
    # просто гарантируем, что таблицы точно есть
    init_db()


# -------- clients --------

def add_client(name: str) -> None:
    name = name.strip()
    if not name:
        raise ValueError("empty name")

    conn = _connect()
    try:
        conn.execute("INSERT OR IGNORE INTO clients(name) VALUES(?)", (name,))
        conn.commit()
    finally:
        conn.close()


def list_clients() -> list[dict[str, Any]]:
    conn = _connect()
    try:
        rows = conn.execute("SELECT id, name FROM clients ORDER BY name").fetchall()
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


# -------- products --------

def add_product(brand: str, model: str, name: str, wh_price: float) -> None:
    brand = brand.strip().lower()
    model = model.strip().lower()
    name = name.strip()
    wh_price = float(wh_price)

    conn = _connect()
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO products(brand, model, name, wh_price)
            VALUES(?, ?, ?, ?)
            """,
            (brand, model, name, wh_price),
        )
        conn.commit()
    finally:
        conn.close()


def list_products() -> list[dict[str, Any]]:
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT id, brand, model, name, wh_price FROM products ORDER BY brand, model"
        ).fetchall()
        out = []
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
            "SELECT id, brand, model, name, wh_price FROM products WHERE brand=? AND model=?",
            (brand.strip().lower(), model.strip().lower()),
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
        (warehouse, product_id, qty),
    )


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


def get_stock_text(warehouse: Optional[str] = None) -> str:
    wh = warehouse.strip().upper() if warehouse else None
    conn = _connect()
    try:
        if wh:
            rows = conn.execute(
                """
                SELECT w.code as warehouse, p.brand, p.model, p.name, s.qty
                FROM stock s
                JOIN products p ON p.id=s.product_id
                JOIN warehouses w ON w.code=s.warehouse_code
                WHERE w.code=?
                ORDER BY p.brand, p.model
                """,
                (wh,),
            ).fetchall()
            title = f"<b>Остатки: {wh}</b>\n"
        else:
            rows = conn.execute(
                """
                SELECT w.code as warehouse, p.brand, p.model, p.name, s.qty
                FROM stock s
                JOIN products p ON p.id=s.product_id
                JOIN warehouses w ON w.code=s.warehouse_code
                ORDER BY w.code, p.brand, p.model
                """
            ).fetchall()
            title = "<b>Остатки (все склады)</b>\n"

        if not rows:
            return title + "Пока пусто."

        lines = []
        current = None
        for r in rows:
            d = dict(r)
            if not wh:
                if current != d["warehouse"]:
                    current = d["warehouse"]
                    lines.append(f"\n<b>{current}</b>")
            lines.append(f"• {d['brand']} {d['model']} — {d['qty']}")
        return title + "\n".join(lines).strip()
    finally:
        conn.close()


# -------- invoices & debts --------

def _next_invoice_no(conn: sqlite3.Connection) -> int:
    r = conn.execute("SELECT COALESCE(MAX(invoice_no), 0) AS mx FROM invoices").fetchone()
    return int(r["mx"]) + 1


def create_invoice_from_cart(cart) -> Tuple[int, float, str]:
    """
    cart: app.bot.states.Cart
    returns: (invoice_no, total, pdf_path)
    """
    from app.services.invoice_pdf import build_invoice_pdf  # локальный импорт чтобы не было циклов

    init_db()

    client = get_client_by_name(cart.client_name)
    if not client:
        raise ValueError("client not found")

    conn = _connect()
    try:
        invoice_no = _next_invoice_no(conn)
        total = 0.0

        # создаём инвойс
        cur = conn.execute(
            "INSERT INTO invoices(invoice_no, client_id, total, currency) VALUES(?, ?, ?, 'USD')",
            (invoice_no, int(client["id"]), 0.0),
        )
        invoice_id = int(cur.lastrowid)

        # items
        for it in cart.items:
            product = find_product(it.brand, it.model)
            if not product:
                raise ValueError(f"product not found: {it.brand} {it.model}")
            pid = int(product["id"])
            line_total = round(float(it.qty) * float(it.unit_price), 2)
            total = round(total + line_total, 2)

            conn.execute(
                """
                INSERT INTO invoice_items(invoice_id, product_id, qty, unit_price, price_mode, line_total)
                VALUES(?, ?, ?, ?, ?, ?)
                """,
                (invoice_id, pid, float(it.qty), float(it.unit_price), it.price_mode, line_total),
            )

        # обновляем total
        conn.execute("UPDATE invoices SET total=? WHERE id=?", (total, invoice_id))

        # генерим PDF
        pdf_path = build_invoice_pdf(
            invoice_no=invoice_no,
            client_name=cart.client_name,
            items=[
                {
                    "brand": it.brand,
                    "model": it.model,
                    "qty": float(it.qty),
                    "unit_price": float(it.unit_price),
                    "line_total": round(float(it.qty) * float(it.unit_price), 2),
                }
                for it in cart.items
            ],
            total=total,
            currency="USD",
        )

        conn.execute("UPDATE invoices SET pdf_path=? WHERE id=?", (pdf_path, invoice_id))

        conn.commit()
        return invoice_no, total, pdf_path
    finally:
        conn.close()


def get_debt_usd(client_name: str) -> float:
    client = get_client_by_name(client_name)
    if not client:
        return 0.0

    conn = _connect()
    try:
        inv = conn.execute(
            "SELECT COALESCE(SUM(total),0) AS s FROM invoices WHERE client_id=?",
            (int(client["id"]),),
        ).fetchone()["s"]
        pay = conn.execute(
            "SELECT COALESCE(SUM(amount),0) AS s FROM payments WHERE client_id=?",
            (int(client["id"]),),
        ).fetchone()["s"]
        return round(float(inv) - float(pay), 2)
    finally:
        conn.close()


def add_payment(client_name: str, amount: float, note: str = "") -> None:
    client = get_client_by_name(client_name)
    if not client:
        raise ValueError("client not found")

    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO payments(client_id, amount, note) VALUES(?, ?, ?)",
            (int(client["id"]), float(amount), note.strip()),
        )
        conn.commit()
    finally:
        conn.close()

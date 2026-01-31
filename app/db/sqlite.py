from __future__ import annotations

import os
import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from app.config import settings
from app.constants import WAREHOUSES
from app.services.invoice_pdf import generate_invoice_pdf

def _connect() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(settings.db_path), exist_ok=True)
    conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row
    return conn

def init_db() -> None:
    conn = _connect()
    try:
        with open(os.path.join(os.path.dirname(__file__), "schema.sql"), "r", encoding="utf-8") as f:
            conn.executescript(f.read())
        conn.commit()
    finally:
        conn.close()

def ensure_admin() -> None:
    # просто гарантируем, что БД создана
    init_db()

def add_client(name: str) -> None:
    conn = _connect()
    try:
        conn.execute("INSERT OR IGNORE INTO clients(name) VALUES(?)", (name,))
        conn.commit()
    finally:
        conn.close()

def list_clients() -> List[Dict[str, Any]]:
    conn = _connect()
    try:
        rows = conn.execute("SELECT id, name FROM clients ORDER BY name").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()

def get_client_by_name(name: str) -> Optional[Dict[str, Any]]:
    conn = _connect()
    try:
        row = conn.execute("SELECT id, name FROM clients WHERE name = ?", (name,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()

def add_product(brand: str, model: str, name: str, wh_price: float) -> None:
    wh10 = round(wh_price * 1.10, settings.decimals)
    conn = _connect()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO products(brand, model, name, wh_price, wh10_price) VALUES(?,?,?,?,?)",
            (brand, model, name, wh_price, wh10),
        )
        conn.commit()
    finally:
        conn.close()

def list_products() -> List[Dict[str, Any]]:
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT brand, model, name, wh_price, wh10_price FROM products ORDER BY brand, model"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()

def find_product(brand: str, model: str) -> Optional[Dict[str, Any]]:
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT id, brand, model, name, wh_price, wh10_price FROM products WHERE brand=? AND model=?",
            (brand, model),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()

def _get_stock_qty(conn: sqlite3.Connection, warehouse: str, product_id: int) -> float:
    row = conn.execute(
        "SELECT qty FROM stock WHERE warehouse=? AND product_id=?",
        (warehouse, product_id),
    ).fetchone()
    return float(row["qty"]) if row else 0.0

def _set_stock_qty(conn: sqlite3.Connection, warehouse: str, product_id: int, qty: float) -> None:
    conn.execute(
        "INSERT INTO stock(warehouse, product_id, qty) VALUES(?,?,?) "
        "ON CONFLICT(warehouse, product_id) DO UPDATE SET qty=excluded.qty",
        (warehouse, product_id, qty),
    )

def get_stock_text(warehouse: Optional[str] = None) -> str:
    conn = _connect()
    try:
        if warehouse:
            whs = [warehouse]
        else:
            whs = WAREHOUSES

        lines = []
        for wh in whs:
            rows = conn.execute(
                """
                SELECT s.warehouse, p.brand, p.model, p.name, s.qty
                FROM stock s
                JOIN products p ON p.id = s.product_id
                WHERE s.warehouse = ?
                ORDER BY p.brand, p.model
                """,
                (wh,),
            ).fetchall()

            lines.append(f"<b>{wh}</b>:")
            if not rows:
                lines.append("  (пусто)")
            else:
                for r in rows:
                    lines.append(f"  • {r['brand']} {r['model']} — {r['name']} | {float(r['qty']):.2f}")
            lines.append("")
        return "\n".join(lines).strip()
    finally:
        conn.close()

def move_stock(src: str, dst: str, brand: str, model: str, qty: float) -> Tuple[bool, str]:
    if qty <= 0:
        return False, "qty должно быть > 0"
    if src == dst:
        return False, "src и dst одинаковые"

    prod = find_product(brand, model)
    if not prod:
        return False, "товар не найден"

    conn = _connect()
    try:
        conn.execute("BEGIN")
        pid = int(prod["id"])
        src_qty = _get_stock_qty(conn, src, pid)
        if src_qty < qty:
            conn.execute("ROLLBACK")
            return False, f"на {src} недостаточно: есть {src_qty:.2f}, нужно {qty:.2f}"

        dst_qty = _get_stock_qty(conn, dst, pid)
        _set_stock_qty(conn, src, pid, src_qty - qty)
        _set_stock_qty(conn, dst, pid, dst_qty + qty)

        conn.commit()
        return True, "ok"
    except Exception as e:
        conn.execute("ROLLBACK")
        return False, str(e)
    finally:
        conn.close()

def create_invoice_from_cart(cart) -> Tuple[bool, Any]:
    """
    Делает:
    - проверка остатков на SHOP
    - списание из SHOP
    - запись invoices + invoice_items
    - запись ledger (invoice)
    - генерация PDF
    """
    conn = _connect()
    try:
        conn.execute("BEGIN")

        created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        total = 0.0

        # 1) проверить, что всё есть в магазине
        for it in cart.items:
            prod = conn.execute(
                "SELECT id, name FROM products WHERE brand=? AND model=?",
                (it.brand, it.model),
            ).fetchone()
            if not prod:
                conn.execute("ROLLBACK")
                return False, f"Товар не найден: {it.brand} {it.model}"
            pid = int(prod["id"])
            shop_qty = _get_stock_qty(conn, "SHOP", pid)
            if shop_qty < it.qty:
                conn.execute("ROLLBACK")
                return False, f"В SHOP мало {it.brand} {it.model}: есть {shop_qty:.2f}, нужно {it.qty:.2f}"

        # 2) создать invoice
        conn.execute(
            "INSERT INTO invoices(client_id, created_at, total, currency) VALUES(?,?,?,?)",
            (cart.client_id, created_at, 0.0, settings.currency),
        )
        invoice_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])

        # 3) списать со склада + записать items
        for it in cart.items:
            prod = conn.execute(
                "SELECT id FROM products WHERE brand=? AND model=?",
                (it.brand, it.model),
            ).fetchone()
            pid = int(prod["id"])

            shop_qty = _get_stock_qty(conn, "SHOP", pid)
            _set_stock_qty(conn, "SHOP", pid, shop_qty - it.qty)

            line_total = round(it.qty * it.price, settings.decimals)
            total = round(total + line_total, settings.decimals)

            conn.execute(
                """
                INSERT INTO invoice_items(invoice_id, product_id, brand, model, name, qty, price, line_total)
                VALUES(?,?,?,?,?,?,?,?)
                """,
                (invoice_id, pid, it.brand, it.model, it.name, it.qty, it.price, line_total),
            )

        # 4) обновить total
        conn.execute("UPDATE invoices SET total=? WHERE id=?", (total, invoice_id))

        # 5) ledger invoice
        conn.execute(
            "INSERT INTO ledger(client_id, type, amount, created_at, note) VALUES(?,?,?,?,?)",
            (cart.client_id, "invoice", total, created_at, f"invoice#{invoice_id}"),
        )

        conn.commit()

        # 6) PDF
        pdf_path = generate_invoice_pdf(invoice_id)

        return True, (invoice_id, pdf_path, total)
    except Exception as e:
        conn.execute("ROLLBACK")
        return False, str(e)
    finally:
        conn.close()

def add_payment(client_id: int, amount: float) -> None:
    if amount <= 0:
        raise ValueError("amount must be > 0")
    conn = _connect()
    try:
        created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            "INSERT INTO ledger(client_id, type, amount, created_at, note) VALUES(?,?,?,?,?)",
            (client_id, "payment", float(amount), created_at, "payment"),
        )
        conn.commit()
    finally:
        conn.close()

def get_debt_usd(client_id: int) -> float:
    conn = _connect()
    try:
        inv = conn.execute(
            "SELECT COALESCE(SUM(amount),0) as s FROM ledger WHERE client_id=? AND type='invoice'",
            (client_id,),
        ).fetchone()["s"]
        pay = conn.execute(
            "SELECT COALESCE(SUM(amount),0) as s FROM ledger WHERE client_id=? AND type='payment'",
            (client_id,),
        ).fetchone()["s"]
        return round(float(inv) - float(pay), settings.decimals)
    finally:
        conn.close()

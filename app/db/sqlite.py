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

        for code, title in WAREHOUSES.items():
            conn.execute(
                "INSERT OR IGNORE INTO warehouses(code, title) VALUES(?, ?)",
                (code, title),
            )
        conn.commit()
    finally:
        conn.close()


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
            INSERT INTO products(brand, model, name, wh_price)
            VALUES(?, ?, ?, ?)
            ON CONFLICT(brand, model) DO UPDATE SET
              name=excluded.name,
              wh_price=excluded.wh_price
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
        (warehouse, product_id, float(qty)),
    )


def receive_stock(warehouse: str, brand: str, model: str, qty: float) -> Tuple[bool, str]:
    init_db()
    wh = warehouse.strip().upper()
    qty = float(qty)

    if wh not in WAREHOUSES:
        return False, "Неизвестный склад"
    if qty <= 0:
        return False, "QTY должно быть > 0"

    product = find_product(brand, model)
    if not product:
        return False, "Товар не найден. Добавь через /product_add"

    conn = _connect()
    try:
        pid = int(product["id"])
        have = _get_stock_qty(conn, wh, pid)
        _set_stock_qty(conn, wh, pid, have + qty)
        conn.commit()
        return True, ""
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


def get_stock(warehouse: Optional[str] = None) -> list[dict[str, Any]]:
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


# -------- cart / invoice --------

def _get_or_create_client_id(conn: sqlite3.Connection, client_name: str) -> int:
    client = conn.execute(
        "SELECT id FROM clients WHERE lower(name)=lower(?)",
        (client_name.strip(),),
    ).fetchone()
    if client:
        return int(client["id"])
    conn.execute("INSERT INTO clients(name) VALUES(?)", (client_name.strip(),))
    return int(conn.execute("SELECT last_insert_rowid() as id").fetchone()["id"])


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
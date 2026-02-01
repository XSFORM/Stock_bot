import os
import sqlite3
from datetime import datetime
from typing import Optional, List, Dict, Any

WAREHOUSES = ["CHINA_DEPOT", "WAREHOUSE", "SHOP"]


def _db_path() -> str:
    # Default path used by install.sh on server
    return os.getenv("DB_PATH", "/opt/stock_bot/app/db/stock.db")


def _connect() -> sqlite3.Connection:
    db_path = _db_path()
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_db() -> None:
    conn = _connect()
    try:
        # Clients
        conn.execute("""
            CREATE TABLE IF NOT EXISTS clients (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
        """)

        # Products
        conn.execute("""
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                brand TEXT NOT NULL,
                model TEXT NOT NULL,
                name TEXT NOT NULL,
                wholesale_price REAL NOT NULL,
                UNIQUE(brand, model)
            );
        """)

        # Stock (qty by warehouse & product)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS stock (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                warehouse TEXT NOT NULL,
                product_id INTEGER NOT NULL,
                qty INTEGER NOT NULL DEFAULT 0,
                UNIQUE(warehouse, product_id),
                FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE CASCADE
            );
        """)

        # Movements log
        conn.execute("""
            CREATE TABLE IF NOT EXISTS movements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dt TEXT NOT NULL DEFAULT (datetime('now')),
                w_from TEXT NOT NULL,
                w_to TEXT NOT NULL,
                product_id INTEGER NOT NULL,
                qty INTEGER NOT NULL,
                FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE CASCADE
            );
        """)

        # Cart (single active cart at a time, for simplicity)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS carts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_name TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                is_active INTEGER NOT NULL DEFAULT 1
            );
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS cart_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cart_id INTEGER NOT NULL,
                product_id INTEGER NOT NULL,
                qty INTEGER NOT NULL,
                price_mode TEXT NOT NULL,
                price REAL NOT NULL,
                UNIQUE(cart_id, product_id),
                FOREIGN KEY(cart_id) REFERENCES carts(id) ON DELETE CASCADE,
                FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE CASCADE
            );
        """)

        # Invoices
        conn.execute("""
            CREATE TABLE IF NOT EXISTS invoices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                number INTEGER NOT NULL UNIQUE,
                client_name TEXT NOT NULL,
                dt TEXT NOT NULL DEFAULT (datetime('now')),
                total REAL NOT NULL,
                debt INTEGER NOT NULL DEFAULT 1
            );
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS invoice_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                invoice_id INTEGER NOT NULL,
                product_id INTEGER NOT NULL,
                qty INTEGER NOT NULL,
                price REAL NOT NULL,
                line_total REAL NOT NULL,
                FOREIGN KEY(invoice_id) REFERENCES invoices(id) ON DELETE CASCADE,
                FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE CASCADE
            );
        """)

        conn.commit()
    finally:
        conn.close()


def _row_to_dict(r: sqlite3.Row) -> Dict[str, Any]:
    return {k: r[k] for k in r.keys()}


def add_client(name: str) -> None:
    init_db()
    conn = _connect()
    try:
        conn.execute("INSERT INTO clients(name) VALUES (?)", (name,))
        conn.commit()
    finally:
        conn.close()


def list_clients() -> List[Dict[str, Any]]:
    init_db()
    conn = _connect()
    try:
        rows = conn.execute("SELECT id, name, created_at FROM clients ORDER BY name").fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def add_product(brand: str, model: str, name: str, wholesale_price: float) -> None:
    init_db()
    conn = _connect()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO products(brand, model, name, wholesale_price) VALUES (?,?,?,?)",
            (brand, model, name, float(wholesale_price)),
        )
        conn.commit()
    finally:
        conn.close()


def list_products() -> List[Dict[str, Any]]:
    init_db()
    conn = _connect()
    try:
        rows = conn.execute("""
            SELECT id, brand, model, name, wholesale_price
            FROM products
            ORDER BY brand, model
        """).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def _get_product_id(conn: sqlite3.Connection, brand: str, model: str) -> int:
    r = conn.execute(
        "SELECT id FROM products WHERE brand=? AND model=?",
        (brand, model),
    ).fetchone()
    if not r:
        raise ValueError(f"Товар не найден: {brand} {model}. Добавь через /product_add")
    return int(r["id"])


def _ensure_stock_row(conn: sqlite3.Connection, warehouse: str, product_id: int) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO stock(warehouse, product_id, qty) VALUES (?,?,0)",
        (warehouse, product_id),
    )


def get_stock(warehouse: Optional[str] = None) -> List[Dict[str, Any]]:
    init_db()
    conn = _connect()
    try:
        if warehouse:
            warehouse = warehouse.upper()
            rows = conn.execute("""
                SELECT s.warehouse, p.brand, p.model, s.qty
                FROM stock s
                JOIN products p ON p.id = s.product_id
                WHERE s.warehouse=?
                ORDER BY p.brand, p.model
            """, (warehouse,)).fetchall()
        else:
            rows = conn.execute("""
                SELECT s.warehouse, p.brand, p.model, s.qty
                FROM stock s
                JOIN products p ON p.id = s.product_id
                ORDER BY s.warehouse, p.brand, p.model
            """).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def move_stock(w_from: str, w_to: str, brand: str, model: str, qty: int) -> None:
    init_db()
    w_from = w_from.upper()
    w_to = w_to.upper()
    if w_from not in WAREHOUSES or w_to not in WAREHOUSES:
        raise ValueError(f"Склады только: {', '.join(WAREHOUSES)}")
    if qty <= 0:
        raise ValueError("Количество должно быть > 0")

    conn = _connect()
    try:
        pid = _get_product_id(conn, brand, model)
        _ensure_stock_row(conn, w_from, pid)
        _ensure_stock_row(conn, w_to, pid)

        cur_qty = conn.execute(
            "SELECT qty FROM stock WHERE warehouse=? AND product_id=?",
            (w_from, pid),
        ).fetchone()["qty"]

        if int(cur_qty) < qty:
            raise ValueError(f"Недостаточно на {w_from}: есть {cur_qty}, нужно {qty}")

        conn.execute(
            "UPDATE stock SET qty = qty - ? WHERE warehouse=? AND product_id=?",
            (qty, w_from, pid),
        )
        conn.execute(
            "UPDATE stock SET qty = qty + ? WHERE warehouse=? AND product_id=?",
            (qty, w_to, pid),
        )
        conn.execute(
            "INSERT INTO movements(w_from, w_to, product_id, qty) VALUES (?,?,?,?)",
            (w_from, w_to, pid, qty),
        )
        conn.commit()
    finally:
        conn.close()


def cart_start(client_name: str) -> None:
    init_db()
    conn = _connect()
    try:
        # Deactivate any old cart
        conn.execute("UPDATE carts SET is_active=0 WHERE is_active=1")
        conn.execute("INSERT INTO carts(client_name, is_active) VALUES (?,1)", (client_name,))
        conn.commit()
    finally:
        conn.close()


def _get_active_cart_id(conn: sqlite3.Connection) -> int:
    r = conn.execute("SELECT id FROM carts WHERE is_active=1 ORDER BY id DESC LIMIT 1").fetchone()
    if not r:
        raise ValueError("Нет активной корзины. Сначала: /cart_start CLIENT_NAME")
    return int(r["id"])


def cart_add(brand: str, model: str, qty: int, price_mode: str = "wh", custom_price: Optional[float] = None) -> None:
    init_db()
    if qty <= 0:
        raise ValueError("Количество должно быть > 0")

    price_mode = (price_mode or "wh").lower()
    if price_mode not in ("wh", "wh10", "custom"):
        raise ValueError("price должен быть: wh / wh10 / custom")

    conn = _connect()
    try:
        cart_id = _get_active_cart_id(conn)
        pid = _get_product_id(conn, brand, model)

        wh = conn.execute("SELECT wholesale_price FROM products WHERE id=?", (pid,)).fetchone()["wholesale_price"]
        wh = float(wh)

        if price_mode == "wh":
            price = wh
        elif price_mode == "wh10":
            price = round(wh * 1.10, 2)
        else:
            if custom_price is None:
                raise ValueError("Для custom надо указать custom_price")
            price = float(custom_price)

        # Insert or update qty
        exists = conn.execute(
            "SELECT qty FROM cart_items WHERE cart_id=? AND product_id=?",
            (cart_id, pid),
        ).fetchone()
        if exists:
            conn.execute(
                "UPDATE cart_items SET qty = qty + ?, price_mode=?, price=? WHERE cart_id=? AND product_id=?",
                (qty, price_mode, price, cart_id, pid),
            )
        else:
            conn.execute(
                "INSERT INTO cart_items(cart_id, product_id, qty, price_mode, price) VALUES (?,?,?,?,?)",
                (cart_id, pid, qty, price_mode, price),
            )
        conn.commit()
    finally:
        conn.close()


def cart_show() -> str:
    init_db()
    conn = _connect()
    try:
        cart_id = _get_active_cart_id(conn)
        cart = conn.execute("SELECT client_name, created_at FROM carts WHERE id=?", (cart_id,)).fetchone()
        rows = conn.execute("""
            SELECT p.brand, p.model, p.name, ci.qty, ci.price
            FROM cart_items ci
            JOIN products p ON p.id = ci.product_id
            WHERE ci.cart_id=?
            ORDER BY p.brand, p.model
        """, (cart_id,)).fetchall()

        if not rows:
            return "Корзина пустая. Добавь: /cart_add BRAND MODEL QTY ..."

        total = 0.0
        lines = [f"🧺 Корзина: <b>{cart['client_name']}</b>"]
        for r in rows:
            line_total = float(r["qty"]) * float(r["price"])
            total += line_total
            lines.append(f"• {r['brand']} {r['model']} x{r['qty']} = {line_total:.2f}$ (цена {float(r['price']):.2f}$)")

        lines.append(f"\n<b>Итого:</b> {total:.2f}$")
        return "\n".join(lines)
    finally:
        conn.close()


def cart_remove(brand: str, model: str) -> None:
    init_db()
    conn = _connect()
    try:
        cart_id = _get_active_cart_id(conn)
        pid = _get_product_id(conn, brand, model)
        conn.execute("DELETE FROM cart_items WHERE cart_id=? AND product_id=?", (cart_id, pid))
        conn.commit()
    finally:
        conn.close()


def cart_finish() -> Dict[str, Any]:
    """
    списывает из SHOP, создает инвойс, возвращает структуру:
    {
      "invoice": {"number":..., "client":..., "date":..., "items":[...], "total":...},
      "total": float,
      "debt": bool
    }
    """
    init_db()
    conn = _connect()
    try:
        cart_id = _get_active_cart_id(conn)
        cart = conn.execute("SELECT client_name, created_at FROM carts WHERE id=?", (cart_id,)).fetchone()
        items = conn.execute("""
            SELECT ci.product_id, p.brand, p.model, p.name, ci.qty, ci.price
            FROM cart_items ci
            JOIN products p ON p.id = ci.product_id
            WHERE ci.cart_id=?
            ORDER BY p.brand, p.model
        """, (cart_id,)).fetchall()

        if not items:
            raise ValueError("Корзина пустая.")

        # Check stock in SHOP first
        for it in items:
            _ensure_stock_row(conn, "SHOP", int(it["product_id"]))
            have = conn.execute(
                "SELECT qty FROM stock WHERE warehouse='SHOP' AND product_id=?",
                (int(it["product_id"]),),
            ).fetchone()["qty"]
            if int(have) < int(it["qty"]):
                raise ValueError(f"Недостаточно в SHOP для {it['brand']} {it['model']}: есть {have}, нужно {it['qty']}")

        # Determine next invoice number
        rnum = conn.execute("SELECT COALESCE(MAX(number), 0) + 1 AS next_num FROM invoices").fetchone()
        inv_number = int(rnum["next_num"])

        total = 0.0
        inv_items = []
        for it in items:
            qty = int(it["qty"])
            price = float(it["price"])
            line_total = qty * price
            total += line_total
            inv_items.append({
                "brand": it["brand"],
                "model": it["model"],
                "name": it["name"],
                "qty": qty,
                "price": price,
                "line_total": round(line_total, 2),
            })

        total = round(total, 2)
        debt = True  # пока всегда "долг: да" (как ты просил)

        # Create invoice
        conn.execute(
            "INSERT INTO invoices(number, client_name, total, debt) VALUES (?,?,?,?)",
            (inv_number, cart["client_name"], total, 1 if debt else 0),
        )
        inv_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

        # Write invoice items + deduct stock from SHOP
        for it in items:
            pid = int(it["product_id"])
            qty = int(it["qty"])
            price = float(it["price"])
            line_total = round(qty * price, 2)

            conn.execute("""
                INSERT INTO invoice_items(invoice_id, product_id, qty, price, line_total)
                VALUES (?,?,?,?,?)
            """, (inv_id, pid, qty, price, line_total))

            conn.execute(
                "UPDATE stock SET qty = qty - ? WHERE warehouse='SHOP' AND product_id=?",
                (qty, pid),
            )

        # Close cart
        conn.execute("UPDATE carts SET is_active=0 WHERE id=?", (cart_id,))
        conn.commit()

        invoice = {
            "number": inv_number,
            "client": cart["client_name"],
            "date": datetime.now().strftime("%Y-%m-%d"),
            "items": inv_items,
            "total": total,
            "currency": "USD",
        }

        return {"invoice": invoice, "total": total, "debt": debt}
    finally:
        conn.close()

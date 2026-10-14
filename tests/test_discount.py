"""Phase 11 — invoice-level percentage discount.

Pins the invariant that the discount is set on the OPEN cart, frozen on
the invoice at finish time, applies to the invoice total, and can be
re-edited through update_sale_invoice without touching line items.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.fixture(scope="function")
def db(tmp_path: Path):
    path = tmp_path / "stock.db"
    os.environ["DB_PATH"] = str(path)
    import app.db.sqlite as _sql
    _sql.DB_PATH = path
    _sql.init_db()
    with _sql._connect() as con:
        con.execute("INSERT OR IGNORE INTO warehouses (code, title) VALUES ('WH1','W')")
        con.execute("INSERT INTO clients (name) VALUES ('C')")
        con.commit()
    return _sql


def _open_cart(sql, warehouse="WH1") -> int:
    with sql._connect() as con:
        cid = int(con.execute("SELECT id FROM clients").fetchone()["id"])
        con.execute(
            "INSERT INTO carts (client_id, warehouse_code, status)"
            " VALUES (?, ?, 'OPEN')",
            (cid, warehouse),
        )
        cart_id = int(con.execute("SELECT last_insert_rowid() AS id").fetchone()["id"])
        con.commit()
    return cart_id


def _add_free(sql, cart_id: int, price: float, qty: float = 1) -> None:
    """Add a free line so test doesn't depend on products/stock seeding."""
    ok, err = sql.cart_add_free_item(
        cart_id, qty=qty, unit_price=price, free_name=f"Item@{price}",
    )
    assert ok, err


class TestSetDiscount:
    def test_set_discount_on_open_cart(self, db) -> None:
        cart_id = _open_cart(db)
        ok, err = db.set_cart_discount(cart_id, 10)
        assert ok, err
        cart, _ = db.get_cart_items_list(cart_id)
        assert cart["discount_pct"] == pytest.approx(10)

    def test_set_discount_clamps_bounds(self, db) -> None:
        cart_id = _open_cart(db)
        db.set_cart_discount(cart_id, 200)
        cart, _ = db.get_cart_items_list(cart_id)
        assert cart["discount_pct"] == pytest.approx(100)  # clamped high
        db.set_cart_discount(cart_id, -5)
        cart, _ = db.get_cart_items_list(cart_id)
        assert cart["discount_pct"] == pytest.approx(0)   # clamped low

    def test_closed_cart_rejected(self, db) -> None:
        cart_id = _open_cart(db)
        with db._connect() as con:
            con.execute("UPDATE carts SET status='CLOSED' WHERE id=?", (cart_id,))
            con.commit()
        ok, err = db.set_cart_discount(cart_id, 5)
        assert not ok
        assert err == "cart_not_open"


class TestFinishAppliesDiscount:
    def test_discount_applied_to_total(self, db) -> None:
        """Subtotal 1000, discount 5% → invoice.total = 950."""
        cart_id = _open_cart(db)
        _add_free(db, cart_id, price=1000.0, qty=1)
        db.set_cart_discount(cart_id, 5)

        ok, err, invoice, _items = db._finish_cart.__wrapped__ if hasattr(
            db._finish_cart, "__wrapped__") else (None, None, None, None)
        # Use the public finish function instead — it opens its own connection.
        with db._connect() as con:
            # Simulate what cart_finish_from_shop does but shorter.
            pass
        # Easier: call cart_finish_from_shop
        ok, err, invoice, _items = db.cart_finish_from_shop("C", "WH1")
        # cart_finish picks the open cart by client name — ensure it hit ours.
        assert ok, err
        assert invoice["total"] == pytest.approx(950.0)
        assert invoice["discount_pct"] == pytest.approx(5.0)
        assert invoice["discount_amount"] == pytest.approx(50.0)

    def test_zero_discount_unchanged(self, db) -> None:
        """Legacy carts (pct=0 default) → total == subtotal."""
        _open_cart(db)
        with db._connect() as con:
            # Open cart was created by _open_cart; use it via cart_finish_from_shop.
            pass
        # Need to add items via free-line to the open cart. The fixture
        # already opened one cart; grab its id.
        with db._connect() as con:
            open_id = int(con.execute("SELECT id FROM carts WHERE status='OPEN'").fetchone()["id"])
        _add_free(db, open_id, price=100.0, qty=3)
        ok, _, invoice, _ = db.cart_finish_from_shop("C", "WH1")
        assert ok
        assert invoice["total"] == pytest.approx(300.0)
        assert invoice["discount_pct"] == pytest.approx(0.0)


class TestEditDiscount:
    def test_update_invoice_changes_discount(self, db) -> None:
        _open_cart(db)
        with db._connect() as con:
            open_id = int(con.execute("SELECT id FROM carts WHERE status='OPEN'").fetchone()["id"])
        _add_free(db, open_id, price=200.0, qty=1)
        db.set_cart_discount(open_id, 0)
        ok, _, invoice, _ = db.cart_finish_from_shop("C", "WH1")
        assert ok
        number = invoice["number"]

        # Re-open: change discount to 10% via update_sale_invoice.
        inv = db.get_invoice_by_number(number)
        items = db.get_invoice_items_by_number(number)
        new_items = [
            {"product_id": i.get("product_id"), "qty": i["qty"],
             "unit_price": i["unit_price"],
             "free_brand": i.get("free_brand") or "",
             "free_model": i.get("free_model") or "",
             "free_name":  i.get("free_name")  or "",
             "free_barcode": i.get("free_barcode") or ""} for i in items
        ]
        ok, err = db.update_sale_invoice(
            number, inv["client_id"], inv["warehouse_code"],
            new_items, discount_pct=10,
        )
        assert ok, err
        inv2 = db.get_invoice_by_number(number)
        assert inv2["discount_pct"] == pytest.approx(10.0)
        assert inv2["total"] == pytest.approx(180.0)  # 200 - 10%

    def test_update_without_discount_param_keeps_old(self, db) -> None:
        """If discount_pct is not passed, existing one must survive."""
        _open_cart(db)
        with db._connect() as con:
            open_id = int(con.execute("SELECT id FROM carts WHERE status='OPEN'").fetchone()["id"])
        _add_free(db, open_id, price=100.0, qty=1)
        db.set_cart_discount(open_id, 15)
        db.cart_finish_from_shop("C", "WH1")

        inv = db.get_invoice_by_number(1)
        items = db.get_invoice_items_by_number(1)
        new_items = [
            {"product_id": i.get("product_id"), "qty": i["qty"],
             "unit_price": i["unit_price"],
             "free_brand": i.get("free_brand") or "",
             "free_model": i.get("free_model") or "",
             "free_name":  i.get("free_name")  or "",
             "free_barcode": i.get("free_barcode") or ""} for i in items
        ]
        # Don't pass discount_pct — the previous 15% must stick.
        db.update_sale_invoice(1, inv["client_id"], inv["warehouse_code"], new_items)
        inv2 = db.get_invoice_by_number(1)
        assert inv2["discount_pct"] == pytest.approx(15.0)

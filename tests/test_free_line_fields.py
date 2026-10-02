"""Phase 10 — free-line items (products not in stock) with 4 separate
columns for brand / model / name / barcode.

The whole point of the phase is that each piece of a free-line
description lands in the matching column of the printed invoice,
rather than everything mashed into a single «Модель» cell.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.fixture(scope="function")
def db_path(tmp_path: Path) -> Path:
    """Fresh DB per test — these mutate stock, we don't want cross-test leakage."""
    path = tmp_path / "stock.db"
    os.environ["DB_PATH"] = str(path)
    import app.db.sqlite as _sql
    _sql.DB_PATH = path
    _sql.init_db()
    with _sql._connect() as con:
        con.execute("INSERT OR IGNORE INTO warehouses (code, title) VALUES ('WH1', 'Main')")
        con.execute("INSERT INTO clients (name) VALUES ('Test Client')")
        con.commit()
    return path


def _open_cart(wh: str = "WH1") -> int:
    import app.db.sqlite as _sql
    with _sql._connect() as con:
        cid = int(con.execute("SELECT id FROM clients").fetchone()["id"])
        con.execute(
            "INSERT INTO carts (client_id, warehouse_code, status)"
            " VALUES (?, ?, 'OPEN')",
            (cid, wh),
        )
        cart_id = int(con.execute("SELECT last_insert_rowid() AS id").fetchone()["id"])
        con.commit()
        return cart_id


# ═════════════════════════════════════════════════════════════════════════════
# cart_add_free_item — new signature
# ═════════════════════════════════════════════════════════════════════════════


class TestCartAddFreeItem:
    def test_all_four_fields_stored(self, db_path: Path) -> None:
        from app.db.sqlite import cart_add_free_item, cart_edit_get
        cart_id = _open_cart()
        ok, err = cart_add_free_item(
            cart_id,
            qty=2,
            unit_price=15.0,
            free_brand="SONIFER",
            free_model="sf-9999",
            free_name="Steam Iron 2000W",
            free_barcode="6971184589999",
        )
        assert ok, err
        _cart, items = cart_edit_get(cart_id)
        assert len(items) == 1
        it = items[0]
        assert it["free_line"] == 1
        assert it["free_brand"]   == "SONIFER"
        assert it["free_model"]   == "sf-9999"
        assert it["free_name"]    == "Steam Iron 2000W"
        assert it["free_barcode"] == "6971184589999"
        # The display columns also pick these up via COALESCE.
        assert it["brand"]   == "SONIFER"
        assert it["model"]   == "sf-9999"
        assert it["name"]    == "Steam Iron 2000W"
        assert it["barcode"] == "6971184589999"

    def test_only_barcode_is_enough(self, db_path: Path) -> None:
        """If the operator only knows the barcode, that's still a valid line."""
        from app.db.sqlite import cart_add_free_item
        cart_id = _open_cart()
        ok, err = cart_add_free_item(
            cart_id, qty=1, unit_price=5.0,
            free_barcode="6971184580000",
        )
        assert ok, err

    def test_all_empty_rejected(self, db_path: Path) -> None:
        from app.db.sqlite import cart_add_free_item
        cart_id = _open_cart()
        ok, err = cart_add_free_item(cart_id, qty=1, unit_price=5.0)
        assert not ok
        assert err == "free_description_required"


# ═════════════════════════════════════════════════════════════════════════════
# Legacy backfill — orphan free_name becomes free_model
# ═════════════════════════════════════════════════════════════════════════════


class TestLegacyBackfill:
    def test_old_free_name_moves_to_free_model(self, db_path: Path) -> None:
        """Rows that already had free_name='SONIFER sf-9999' before the
        migration must now have that value in free_model (preserving the
        previous visual column) and free_name empty."""
        import app.db.sqlite as _sql
        cart_id = _open_cart()
        # Simulate a pre-Phase-10 insert: free_name has the model-like
        # string, free_* columns default to ''.
        with _sql._connect() as con:
            con.execute(
                "INSERT INTO cart_items"
                "  (cart_id, product_id, free_line, free_name,"
                "   free_brand, free_model, free_barcode,"
                "   qty, price_mode, unit_price, total, cost_price)"
                " VALUES (?, NULL, 1, 'SONIFER sf-LEGACY', '', '', '',"
                "         1, 'custom', 10, 10, 0)",
                (cart_id,),
            )
            con.commit()

        # Re-run init_db to trigger the backfill statement.
        _sql.init_db()

        with _sql._connect() as con:
            row = con.execute(
                "SELECT free_brand, free_model, free_name, free_barcode"
                "   FROM cart_items WHERE cart_id = ?",
                (cart_id,),
            ).fetchone()
            assert row["free_brand"]   == ""
            assert row["free_model"]   == "SONIFER sf-LEGACY"  # ← moved
            assert row["free_name"]    == ""                   # ← cleared
            assert row["free_barcode"] == ""


# ═════════════════════════════════════════════════════════════════════════════
# Printed invoice (XLSX bytes) — each piece lands in its own column
# ═════════════════════════════════════════════════════════════════════════════


class TestInvoiceOutputColumns:
    def test_xlsx_generator_splits_free_fields(self, db_path: Path) -> None:
        """The XLSX generator is the thing the user sees on the printed
        invoice. Make sure a free item with 4 fields fills 4 columns, not
        just «Модель»."""
        from app.services.invoice_xlsx import _make_workbook
        invoice = {
            "number":  1,
            "created_at": "2026-10-02 12:00:00",
            "client":  "Test",
        }
        items = [{
            "free_line": 1,
            "free_brand":   "SONIFER",
            "free_model":   "sf-9999",
            "free_name":    "Steam Iron 2000W",
            "free_barcode": "6971184589999",
            "qty": 1,
            "unit_price": 15.0,
            "total": 15.0,
        }]
        wb = _make_workbook(invoice, items)
        ws = wb.active
        # Header is in row 5 (hard-coded by _make_workbook). Our first data
        # row is row 6 and the columns are 1=#, 2=Model, 3=Name, 4=Barcode.
        assert ws.cell(row=6, column=2).value == "SONIFER sf-9999"
        assert ws.cell(row=6, column=3).value == "Steam Iron 2000W"
        assert ws.cell(row=6, column=4).value == "6971184589999"

    def test_xlsx_generator_stock_items_unchanged(self, db_path: Path) -> None:
        """A regular stock item still shows brand+model in column 2,
        name in column 3, barcode in column 4 (same as before Phase 10)."""
        from app.services.invoice_xlsx import _make_workbook
        invoice = {"number": 2, "created_at": "2026-10-02 12:00:00", "client": "T"}
        items = [{
            "free_line": 0,
            "brand": "Samsung",
            "model": "A51",
            "name": "Phone A51",
            "barcode": "1234567890",
            "qty": 1,
            "unit_price": 200.0,
            "total": 200.0,
        }]
        wb = _make_workbook(invoice, items)
        ws = wb.active
        assert ws.cell(row=6, column=2).value == "Samsung A51"
        assert ws.cell(row=6, column=3).value == "Phone A51"
        assert ws.cell(row=6, column=4).value == "1234567890"

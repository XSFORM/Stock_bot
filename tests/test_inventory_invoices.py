"""Phase 8 — inventory_invoices tests.

What's locked in here:
  * Applying inventory creates a real invoice with a number, items (with
    system/actual/delta/cost_price snapshots), ADJUST rows in stock_ops
    all point back at the invoice, and stock levels move by delta.
  * Numbers monotonically increase on each apply.
  * The per-product history (list_history_by_product) now includes the
    ADJUST rows with the parent invoice number — the whole user complaint
    this phase came from.
  * The legacy-backfill migration reconstructs invoices from orphan
    ADJUST rows added before Phase 8 landed, so the user's existing
    inventory (made two days ago) is NOT silently dropped.
"""
from __future__ import annotations

import os
import sqlite3
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
    # Need a warehouse before any ADJUST will succeed.
    with _sql._connect() as con:
        con.execute("INSERT OR IGNORE INTO warehouses (code, title) VALUES ('WH1', 'Main')")
        con.commit()
    return path


def _add_product(brand: str, model: str, name: str, price: float = 10.0) -> int:
    from app.db.sqlite import add_or_get_product_id
    pid, _ = add_or_get_product_id(brand, model, name, price)
    return pid


def _seed_stock(warehouse: str, product_id: int, qty: float) -> None:
    from app.db.sqlite import receive_stock_by_product_id
    receive_stock_by_product_id(warehouse, product_id, qty)


# ═════════════════════════════════════════════════════════════════════════════
# apply_inventory_adjustments → creates an invoice
# ═════════════════════════════════════════════════════════════════════════════


class TestApplyCreatesInvoice:
    def test_apply_creates_invoice_and_items(self, db_path: Path) -> None:
        from app.db.sqlite import (
            apply_inventory_adjustments, list_inventory_invoices, get_inventory_invoice,
        )

        pid_a = _add_product("Samsung", "A1", "Phone A", 100.0)
        pid_b = _add_product("Xiaomi", "B2", "Phone B",  50.0)
        _seed_stock("WH1", pid_a, 10)  # system = 10 after this receive
        _seed_stock("WH1", pid_b,  5)

        ok, err, n = apply_inventory_adjustments(
            "WH1",
            [
                {"product_id": pid_a, "system_qty": 10, "actual_qty":  8},   # delta -2
                {"product_id": pid_b, "system_qty":  5, "actual_qty":  7},   # delta +2
            ],
            note="July 2026",
        )
        assert ok, err
        assert n == 2

        invs = list_inventory_invoices()
        assert len(invs) == 1
        inv_id = invs[0]["id"]
        assert invs[0]["number"] == 1
        assert invs[0]["warehouse_code"] == "WH1"
        assert invs[0]["note"] == "July 2026"
        assert invs[0]["lines"] == 2

        inv = get_inventory_invoice(inv_id)
        assert inv is not None
        items_by_pid = {it["product_id"]: it for it in inv["items"]}
        assert items_by_pid[pid_a]["system_qty"] == pytest.approx(10)
        assert items_by_pid[pid_a]["actual_qty"] == pytest.approx(8)
        assert items_by_pid[pid_a]["delta"]      == pytest.approx(-2)
        # cost_price was snapshotted (A = 100 USD)
        assert items_by_pid[pid_a]["cost_price"] == pytest.approx(100.0)
        assert items_by_pid[pid_b]["delta"]      == pytest.approx(+2)
        # Net cost: -2 × 100 + 2 × 50 = -100
        assert inv["net_cost_delta"]             == pytest.approx(-100.0)

    def test_stock_actually_changes_by_delta(self, db_path: Path) -> None:
        from app.db.sqlite import apply_inventory_adjustments, get_stock_qty
        pid = _add_product("Brand", "M", "X", 1.0)
        _seed_stock("WH1", pid, 100)
        apply_inventory_adjustments(
            "WH1",
            [{"product_id": pid, "system_qty": 100, "actual_qty": 95}],
            note="recount",
        )
        assert get_stock_qty("WH1", pid) == pytest.approx(95)

    def test_adjust_rows_link_back_to_invoice(self, db_path: Path) -> None:
        from app.db.sqlite import apply_inventory_adjustments, list_inventory_invoices
        import app.db.sqlite as _sql

        pid = _add_product("B", "M", "N", 5.0)
        _seed_stock("WH1", pid, 20)
        apply_inventory_adjustments(
            "WH1",
            [{"product_id": pid, "system_qty": 20, "actual_qty": 22}],
            note="recount",
        )
        inv_id = list_inventory_invoices()[0]["id"]
        with _sql._connect() as con:
            row = con.execute(
                "SELECT inventory_invoice_id FROM stock_ops"
                "  WHERE op_type = 'ADJUST' AND product_id = ?",
                (pid,),
            ).fetchone()
            assert row is not None
            assert row["inventory_invoice_id"] == inv_id

    def test_zero_delta_rows_are_skipped(self, db_path: Path) -> None:
        """A product whose actual == system shouldn't make it into the invoice."""
        from app.db.sqlite import apply_inventory_adjustments, get_inventory_invoice, list_inventory_invoices
        pid_a = _add_product("A", "a", "a", 1.0)
        pid_b = _add_product("B", "b", "b", 1.0)
        _seed_stock("WH1", pid_a, 10)
        _seed_stock("WH1", pid_b, 10)
        apply_inventory_adjustments(
            "WH1",
            [
                {"product_id": pid_a, "system_qty": 10, "actual_qty": 10},  # no-op
                {"product_id": pid_b, "system_qty": 10, "actual_qty": 11},
            ],
            note="partial",
        )
        inv = get_inventory_invoice(list_inventory_invoices()[0]["id"])
        pids = {it["product_id"] for it in inv["items"]}
        assert pids == {pid_b}

    def test_numbers_increase_monotonically(self, db_path: Path) -> None:
        from app.db.sqlite import apply_inventory_adjustments, list_inventory_invoices
        pid = _add_product("X", "Y", "Z", 1.0)
        _seed_stock("WH1", pid, 100)
        apply_inventory_adjustments("WH1", [{"product_id": pid, "system_qty": 100, "actual_qty": 99}], note="first")
        apply_inventory_adjustments("WH1", [{"product_id": pid, "system_qty":  99, "actual_qty": 98}], note="second")
        invs = list_inventory_invoices()
        assert sorted(i["number"] for i in invs) == [1, 2]


# ═════════════════════════════════════════════════════════════════════════════
# Per-product history now shows inventory
# ═════════════════════════════════════════════════════════════════════════════


class TestProductHistoryShowsInventory:
    def test_adjust_appears_in_list_history_by_product(self, db_path: Path) -> None:
        from app.db.sqlite import apply_inventory_adjustments, list_history_by_product
        pid = _add_product("Hist", "M", "N", 7.0)
        _seed_stock("WH1", pid, 10)
        apply_inventory_adjustments(
            "WH1",
            [{"product_id": pid, "system_qty": 10, "actual_qty": 12}],
            note="recount",
        )
        events = list_history_by_product(pid)
        inv_events = [e for e in events if e["type"] == "INVENTORY"]
        assert len(inv_events) == 1
        ev = inv_events[0]
        assert ev["qty"] == pytest.approx(+2)              # signed delta
        assert ev["warehouse"] == "WH1"
        assert ev["counterparty"] == "recount"             # the note
        assert ev["ref"] == "1"                            # invoice number
        assert ev["view_url"].startswith("/documents/inventory/")
        assert ev["download_url"].endswith("/xlsx")


# ═════════════════════════════════════════════════════════════════════════════
# Legacy backfill — orphan ADJUSTs become invoices on next init_db()
# ═════════════════════════════════════════════════════════════════════════════


class TestLegacyBackfill:
    def test_orphan_adjusts_get_reconstructed_invoices(self, tmp_path: Path) -> None:
        """Simulate the user's situation: ADJUST rows written before Phase 8
        existed. init_db() must reconstruct an invoice for each session.
        """
        import app.db.sqlite as _sql
        path = tmp_path / "legacy.db"
        os.environ["DB_PATH"] = str(path)
        _sql.DB_PATH = path
        _sql.init_db()

        with _sql._connect() as con:
            con.execute("INSERT OR IGNORE INTO warehouses (code, title) VALUES ('WH1', 'W')")
            # Add a product so FK succeeds.
            con.execute("INSERT INTO products (brand, model, name, purchase_price)"
                        " VALUES ('L', 'eg', 'acy', 3)")
            pid = int(con.execute("SELECT id FROM products").fetchone()["id"])

            # Write pre-Phase-8-style ADJUSTs: no inventory_invoice_id.
            # Two separate sessions distinguished by note.
            con.execute(
                "INSERT INTO stock_ops"
                "  (created_at, op_type, source, warehouse_code, product_id, qty, note)"
                " VALUES ('2026-07-01 10:00:00', 'ADJUST', 'INVENTORY', 'WH1', ?, 2, 'July 2026')",
                (pid,),
            )
            con.execute(
                "INSERT INTO stock_ops"
                "  (created_at, op_type, source, warehouse_code, product_id, qty, note)"
                " VALUES ('2026-07-01 10:00:30', 'ADJUST', 'INVENTORY', 'WH1', ?, -1, 'July 2026')",
                (pid,),
            )
            con.execute(
                "INSERT INTO stock_ops"
                "  (created_at, op_type, source, warehouse_code, product_id, qty, note)"
                " VALUES ('2026-08-15 09:00:00', 'ADJUST', 'INVENTORY', 'WH1', ?, 5, 'August recount')",
                (pid,),
            )
            # Nuke any invoices that might've been auto-created by init_db
            # on the fresh DB — we want to test the backfill in isolation.
            con.execute("UPDATE stock_ops SET inventory_invoice_id = NULL WHERE op_type = 'ADJUST'")
            con.execute("DELETE FROM inventory_items")
            con.execute("DELETE FROM inventory_invoices")
            con.commit()

        # Second init_db call runs the backfill against the orphan rows.
        _sql.init_db()

        from app.db.sqlite import list_inventory_invoices
        invs = list_inventory_invoices()
        # Two sessions → two invoices (grouped by day + note).
        assert len(invs) == 2
        notes = {inv["note"] for inv in invs}
        # Legacy marker must be present on both.
        for inv in invs:
            assert "восстановлено" in inv["note"].lower() or "legacy" in inv["note"].lower()
        # The user's original notes are preserved as a prefix.
        joined = " ".join(notes).lower()
        assert "july 2026" in joined
        assert "august recount" in joined

    def test_backfill_links_adjust_rows_to_new_invoices(self, tmp_path: Path) -> None:
        """After backfill, no orphan ADJUST rows should remain."""
        import app.db.sqlite as _sql
        path = tmp_path / "legacy2.db"
        os.environ["DB_PATH"] = str(path)
        _sql.DB_PATH = path
        _sql.init_db()

        with _sql._connect() as con:
            con.execute("INSERT OR IGNORE INTO warehouses (code, title) VALUES ('WH1', 'W')")
            con.execute("INSERT INTO products (brand, model, name, purchase_price)"
                        " VALUES ('A','B','C',1)")
            pid = int(con.execute("SELECT id FROM products").fetchone()["id"])
            con.execute(
                "INSERT INTO stock_ops"
                "  (created_at, op_type, source, warehouse_code, product_id, qty, note,"
                "   inventory_invoice_id)"
                " VALUES ('2026-01-01 00:00:00','ADJUST','INVENTORY','WH1',?,3,'legacy', NULL)",
                (pid,),
            )
            con.execute("DELETE FROM inventory_items")
            con.execute("DELETE FROM inventory_invoices")
            con.commit()

        _sql.init_db()  # triggers backfill

        with _sql._connect() as con:
            orphans = con.execute(
                "SELECT COUNT(*) AS n FROM stock_ops"
                "  WHERE op_type = 'ADJUST' AND inventory_invoice_id IS NULL"
            ).fetchone()
            assert int(orphans["n"]) == 0

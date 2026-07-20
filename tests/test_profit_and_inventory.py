"""Tests for Fable5 Phases 1–4:
  Phase 1 — cost_price snapshot on sale and preservation on invoice edit.
  Phase 2 — profit report excludes cost=0 lines (sale + return).
  Phase 3 — client payment-discipline stats.
  Phase 4 — inventory (apply_inventory_adjustments + get_inventory_discrepancies).

Everything uses a scratch DB via DB_PATH so the real production database
is never touched. Module-scoped fixture initialises schema once; individual
tests create their own products/clients/invoices so they don't interfere
with each other.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import date, datetime, timedelta

import pytest


# ─── Test infrastructure ─────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def db_path(tmp_path_factory: pytest.TempPathFactory) -> str:
    """
    Give this module its own tmp DB and point the already-imported
    app.db.sqlite module at it.

    Setting os.environ["DB_PATH"] alone is not enough — sqlite.py reads
    that env var once at import time and stores the resolved value in a
    module-level DB_PATH constant. If a previous test module imported
    sqlite first, the constant is already frozen to a different tmp file,
    so init_db() would recreate tables there while our INSERTs went to
    a brand-new empty file. Overwriting the module attribute keeps every
    subsequent connection going to our path.
    """
    from pathlib import Path
    path = tmp_path_factory.mktemp("phase4") / "stock.db"
    os.environ["DB_PATH"] = str(path)
    import app.db.sqlite as _sql
    _sql.DB_PATH = Path(path)
    return str(path)


@pytest.fixture(scope="module", autouse=True)
def init(db_path: str) -> None:
    from app.db.sqlite import init_db
    init_db()
    # Seed at least one warehouse — sale flow requires one. Go through
    # the same _connect() the app uses so we hit the patched DB_PATH.
    import app.db.sqlite as _sql
    with _sql._connect() as con:
        con.execute(
            "INSERT OR IGNORE INTO warehouses (code, title) VALUES (?, ?)",
            ("TEST_WH", "Test warehouse"),
        )
        con.execute(
            "INSERT OR IGNORE INTO warehouses (code, title) VALUES (?, ?)",
            ("SECOND_WH", "Second warehouse"),
        )
        con.commit()


# ─── Small helpers to build a completed sale end-to-end ──────────────────────


def _connect() -> sqlite3.Connection:
    """Use the same connection the app uses (respects any monkey-patched DB_PATH)."""
    import app.db.sqlite as _sql
    return _sql._connect()


def _make_client(name: str) -> int:
    """Create a client and return its id. Names are made unique per test."""
    from app.db.sqlite import add_client
    ok, err = add_client(name, phone="", note="", client_type="wholesale")
    assert ok, err
    with _connect() as con:
        row = con.execute("SELECT id FROM clients WHERE name = ?", (name,)).fetchone()
    return int(row["id"])


def _make_product(brand: str, model: str, wh_price: float) -> int:
    from app.db.sqlite import add_or_get_product_id, receive_stock_by_product_id
    pid, _ = add_or_get_product_id(brand, model, f"{brand} {model}", wh_price)
    ok, err = receive_stock_by_product_id("TEST_WH", pid, 100.0)
    assert ok, err
    return pid


def _set_wh_price(product_id: int, new_price: float) -> None:
    """Bump products.wh_price without touching anything else."""
    with _connect() as con:
        con.execute(
            "UPDATE products SET wh_price = ? WHERE id = ?",
            (new_price, product_id),
        )
        con.commit()


def _finish_sale(
    client_id: int,
    brand: str,
    model: str,
    qty: float,
    unit_price: float,
    warehouse: str = "TEST_WH",
) -> tuple[int, int]:
    """
    Start a cart, add one item at a custom price, finish it, return (invoice_number, cart_id).
    Only one open cart is allowed at a time, so tests must finish before starting the next.
    """
    from app.db.sqlite import (
        cart_start_by_id, cart_add_by_cart_id, cart_finish_by_cart_id_shop1416,
    )
    ok, err, cart_id = cart_start_by_id(client_id, warehouse_code=warehouse)
    assert ok, err
    ok, err = cart_add_by_cart_id(
        cart_id, brand, model, qty,
        price_mode="custom", custom_price=unit_price,
    )
    assert ok, err
    ok, err, inv, _lines = cart_finish_by_cart_id_shop1416(cart_id)
    assert ok, err
    return int(inv["number"]), cart_id


def _cart_items(cart_id: int) -> list[sqlite3.Row]:
    with _connect() as con:
        return list(
            con.execute(
                "SELECT product_id, qty, unit_price, cost_price, free_line"
                " FROM cart_items WHERE cart_id = ?",
                (cart_id,),
            )
        )


# ═════════════════════════════════════════════════════════════════════════════
# PHASE 1 — cost_price snapshots
# ═════════════════════════════════════════════════════════════════════════════


class TestCostSnapshotOnSale:
    """Sale writes current wh_price into cart_items.cost_price."""

    def test_snapshot_matches_wh_price_at_moment_of_sale(self) -> None:
        pid = _make_product("SONY_P1", "A1", wh_price=10.0)
        cid = _make_client("Phase1 Client A")
        _, cart_id = _finish_sale(cid, "SONY_P1", "A1", qty=2, unit_price=15.0)

        items = _cart_items(cart_id)
        assert len(items) == 1
        row = items[0]
        assert row["product_id"] == pid
        assert float(row["qty"]) == 2.0
        assert float(row["unit_price"]) == 15.0
        assert float(row["cost_price"]) == 10.0


class TestCostPreservationOnEdit:
    """Critical: editing an invoice must NOT overwrite old snapshots
    with the current products.wh_price."""

    def test_old_line_keeps_original_cost_after_price_bump(self) -> None:
        pid = _make_product("SONY_P1B", "B1", wh_price=10.0)
        cid = _make_client("Phase1 Client B")
        inv_number, cart_id = _finish_sale(cid, "SONY_P1B", "B1", qty=1, unit_price=20.0)

        # Purchase cost was 10 when we sold. Now suppliers ship at 14.
        _set_wh_price(pid, 14.0)

        # Simulate a user edit that keeps the same product/qty but re-saves.
        from app.db.sqlite import update_sale_invoice
        ok, err = update_sale_invoice(
            number=inv_number,
            client_id=cid,
            warehouse_code="TEST_WH",
            new_items=[{"product_id": pid, "qty": 1, "unit_price": 20.0}],
        )
        assert ok, err

        items = _cart_items(cart_id)
        assert len(items) == 1
        assert float(items[0]["cost_price"]) == 10.0, (
            f"expected preserved cost 10, got {items[0]['cost_price']}"
        )

    def test_new_line_added_during_edit_gets_current_wh_price(self) -> None:
        pid_old = _make_product("SONY_P1C", "C1", wh_price=10.0)
        cid = _make_client("Phase1 Client C")
        inv_number, cart_id = _finish_sale(cid, "SONY_P1C", "C1", qty=1, unit_price=20.0)

        # Bump the old product's cost (should still stay frozen at 10 for existing line).
        _set_wh_price(pid_old, 14.0)

        # A brand new product added while editing.
        pid_new = _make_product("SONY_P1C2", "C2", wh_price=7.0)

        from app.db.sqlite import update_sale_invoice
        ok, err = update_sale_invoice(
            number=inv_number,
            client_id=cid,
            warehouse_code="TEST_WH",
            new_items=[
                {"product_id": pid_old, "qty": 1, "unit_price": 20.0},
                {"product_id": pid_new, "qty": 3, "unit_price": 12.0},
            ],
        )
        assert ok, err

        items = {int(r["product_id"]): r for r in _cart_items(cart_id)}
        assert len(items) == 2
        assert float(items[pid_old]["cost_price"]) == 10.0, "old line lost its snapshot"
        assert float(items[pid_new]["cost_price"]) == 7.0, "new line missed current wh_price"


# ═════════════════════════════════════════════════════════════════════════════
# PHASE 2 — profit report excludes cost=0 rows
# ═════════════════════════════════════════════════════════════════════════════


def _insert_legacy_sale_line(
    client_id: int,
    warehouse: str,
    product_id: int | None,
    qty: float,
    unit_price: float,
    cost_price: float,
    when: datetime | None = None,
) -> None:
    """
    Insert a completed sale row bypassing the normal cart flow.

    Lets us construct the exact combination the profit report cares about
    (cost_price=0 in particular) without depending on flow-specific side effects.
    """
    when = when or datetime.now()
    ts = when.strftime("%Y-%m-%d %H:%M:%S")
    with _connect() as con:
        # NB: sale carts get status='CLOSED' when finished (see _finish_cart),
        # so the profit report joins on that value.
        con.execute(
            "INSERT INTO carts (client_id, warehouse_code, status, created_at)"
            " VALUES (?, ?, 'CLOSED', ?)",
            (client_id, warehouse, ts),
        )
        cart_id = con.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
        free_line = 0 if product_id else 1
        con.execute(
            "INSERT INTO cart_items"
            " (cart_id, product_id, free_line, free_name, qty, price_mode,"
            "  unit_price, total, cost_price)"
            " VALUES (?, ?, ?, ?, ?, 'custom', ?, ?, ?)",
            (
                cart_id, product_id, free_line, "" if product_id else "legacy",
                qty, unit_price, unit_price * qty, cost_price,
            ),
        )
        # Fresh invoice number = max+1 (avoids collision with real cart flow).
        max_num = con.execute("SELECT COALESCE(MAX(number), 0) FROM invoices").fetchone()[0]
        con.execute(
            "INSERT INTO invoices (number, cart_id, currency, total, created_at)"
            " VALUES (?, ?, 'USD', ?, ?)",
            (max_num + 1, cart_id, unit_price * qty, ts),
        )
        con.commit()


def _insert_legacy_return_line(
    client_id: int,
    warehouse: str,
    product_id: int | None,
    qty: float,
    unit_price: float,
    cost_price: float,
    when: datetime | None = None,
) -> None:
    """Same idea for return_invoices/return_items."""
    when = when or datetime.now()
    ts = when.strftime("%Y-%m-%d %H:%M:%S")
    with _connect() as con:
        max_num = con.execute(
            "SELECT COALESCE(MAX(number), 0) FROM return_invoices"
        ).fetchone()[0]
        con.execute(
            "INSERT INTO return_invoices"
            " (number, client_id, warehouse_code, status, currency, total, created_at)"
            " VALUES (?, ?, ?, 'DONE', 'USD', ?, ?)",
            (max_num + 1, client_id, warehouse, unit_price * qty, ts),
        )
        inv_id = con.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
        free_line = 0 if product_id else 1
        con.execute(
            "INSERT INTO return_items"
            " (invoice_id, product_id, free_line, free_name, qty, unit_price,"
            "  total, cost_price)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                inv_id, product_id, free_line, "" if product_id else "legacy-ret",
                qty, unit_price, unit_price * qty, cost_price,
            ),
        )
        con.commit()


class TestProfitReportExcludesZeroCost:
    """cost=0 rows must NOT pollute the totals — they go into 'unknown' only."""

    def test_sale_with_cost_zero_lands_in_unknown_not_in_totals(self) -> None:
        # Use baseline/after so this test is independent of any state left
        # by earlier tests in the module (they run in alphabetical order and
        # share the same module-scoped DB).
        from app.db.sqlite import get_profit_report
        today = date.today().isoformat()
        baseline = get_profit_report(today, today, warehouse_codes=["TEST_WH"])

        pid = _make_product("PROFIT_A", "AA1", wh_price=0.0)  # 0 = legacy row
        cid = _make_client("Profit Client A")
        _insert_legacy_sale_line(
            cid, "TEST_WH", pid, qty=1, unit_price=100.0, cost_price=0.0,
        )

        after = get_profit_report(today, today, warehouse_codes=["TEST_WH"])

        # The row is present as one more unknown line…
        assert after["unknown"]["lines"] == baseline["unknown"]["lines"] + 1
        # …excluded revenue went up by exactly the sale amount ($100)…
        assert after["unknown"]["revenue"] - baseline["unknown"]["revenue"] == pytest.approx(100.0), (
            f"unknown revenue delta = "
            f"{after['unknown']['revenue'] - baseline['unknown']['revenue']}, expected +100"
        )
        # …and totals.revenue / totals.cost stayed put (the row was excluded).
        assert after["totals"]["revenue"] == baseline["totals"]["revenue"], (
            "unknown-cost sale leaked into totals.revenue"
        )
        assert after["totals"]["cost"] == baseline["totals"]["cost"]

    def test_totals_reflect_only_lines_with_known_cost(self) -> None:
        # A clean sale with real cost.
        pid_clean = _make_product("PROFIT_B", "BB1", wh_price=6.0)
        cid = _make_client("Profit Client B")
        _finish_sale(cid, "PROFIT_B", "BB1", qty=2, unit_price=10.0)  # revenue 20, cost 12

        # An unknown-cost row for the SAME client & date.
        pid_zero = _make_product("PROFIT_B_ZERO", "BB2", wh_price=0.0)
        _insert_legacy_sale_line(cid, "TEST_WH", pid_zero, qty=1, unit_price=999.0, cost_price=0.0)

        from app.db.sqlite import get_profit_report
        today = date.today().isoformat()
        rpt = get_profit_report(today, today, warehouse_codes=["TEST_WH"])

        # The known-cost row contributes revenue=20, cost=12, profit=8.
        # The unknown row (999 revenue) must NOT be added to totals.
        assert rpt["totals"]["revenue"] >= 20.0
        assert rpt["totals"]["revenue"] < 999.0, (
            f"unknown-cost line leaked into totals.revenue = {rpt['totals']['revenue']}"
        )
        # And unknown block reports the excluded revenue.
        assert rpt["unknown"]["revenue"] >= 999.0

    def test_return_with_cost_zero_also_excluded(self) -> None:
        pid = _make_product("PROFIT_RET", "RR1", wh_price=0.0)
        cid = _make_client("Profit Ret Client")

        # A clean sale so the report has some real data to work with.
        pid_clean = _make_product("PROFIT_RET_CLEAN", "RR2", wh_price=5.0)
        _finish_sale(cid, "PROFIT_RET_CLEAN", "RR2", qty=1, unit_price=20.0)  # rev=20

        # Baseline: without a zero-cost return, unknown.lines / ret_cost / totals.cost are known.
        from app.db.sqlite import get_profit_report
        today = date.today().isoformat()
        baseline = get_profit_report(today, today, warehouse_codes=["TEST_WH"])

        # Now add a legacy return with cost_price=0 (the case we care about).
        _insert_legacy_return_line(cid, "TEST_WH", pid, qty=1, unit_price=50.0, cost_price=0.0)

        after = get_profit_report(today, today, warehouse_codes=["TEST_WH"])

        # The zero-cost return must show up as an additional excluded line…
        assert after["unknown"]["lines"] == baseline["unknown"]["lines"] + 1, (
            f"return with cost=0 was not counted as unknown "
            f"({baseline['unknown']['lines']} → {after['unknown']['lines']})"
        )
        # …and must not have added anything to ret_cost (the whole point of exclusion).
        assert after["totals"]["ret_cost"] == baseline["totals"]["ret_cost"], (
            "excluded return leaked into ret_cost"
        )


# ═════════════════════════════════════════════════════════════════════════════
# PHASE 3 — client payment-discipline stats
# ═════════════════════════════════════════════════════════════════════════════


class TestClientPaymentStats:
    def test_one_payment_yields_correct_days_and_sums(self) -> None:
        from app.db.sqlite import add_client_adjustment, get_client_payment_stats
        cid = _make_client("Payment Client A")

        # Record a payment of $150 as of "now" (localtime).
        ok, err = add_client_adjustment(cid, 150.0, note="test payment")
        assert ok, err

        stats = get_client_payment_stats(cid)
        assert stats["last_payment_at"] is not None
        assert stats["days_since_last"] == 0, stats["days_since_last"]
        assert stats["sum_last_30d"] == 150.0
        assert stats["sum_last_90d"] == 150.0
        assert stats["count_last_90d"] == 1
        assert stats["avg_payment_90d"] == 150.0

    def test_client_with_no_payments_returns_none_without_crashing(self) -> None:
        from app.db.sqlite import get_client_payment_stats
        cid = _make_client("Payment Client Empty")

        stats = get_client_payment_stats(cid)
        assert stats["last_payment_at"] is None
        assert stats["days_since_last"] is None
        assert stats["sum_last_30d"] == 0.0
        assert stats["sum_last_90d"] == 0.0
        assert stats["count_last_90d"] == 0
        assert stats["avg_payment_90d"] == 0.0

    def test_manual_debt_addition_is_not_a_payment(self) -> None:
        """add_client_debt records negative amounts → must NOT count as a payment."""
        from app.db.sqlite import add_client_debt, get_client_payment_stats
        cid = _make_client("Payment Client Debt")

        ok, err = add_client_debt(cid, 200.0, note="manual debt")
        assert ok, err

        stats = get_client_payment_stats(cid)
        assert stats["last_payment_at"] is None
        assert stats["sum_last_30d"] == 0.0
        assert stats["count_last_90d"] == 0


# ═════════════════════════════════════════════════════════════════════════════
# PHASE 4 — inventory
# ═════════════════════════════════════════════════════════════════════════════


class TestInventoryValidation:
    def test_empty_note_is_rejected(self) -> None:
        from app.db.sqlite import apply_inventory_adjustments
        pid = _make_product("INV_A", "AA", wh_price=1.0)
        ok, err, n = apply_inventory_adjustments(
            "TEST_WH",
            [{"product_id": pid, "delta": -1}],
            note="",
        )
        assert not ok
        assert err == "note_required"
        assert n == 0

    def test_no_nonzero_deltas_returns_no_changes(self) -> None:
        from app.db.sqlite import apply_inventory_adjustments
        pid = _make_product("INV_B", "BB", wh_price=1.0)
        ok, err, n = apply_inventory_adjustments(
            "TEST_WH",
            [{"product_id": pid, "delta": 0}],
            note="unused",
        )
        assert not ok
        assert err == "no_changes"
        assert n == 0


class TestInventoryApply:
    def test_negative_delta_decrements_stock_and_writes_adjust_op(self) -> None:
        from app.db.sqlite import apply_inventory_adjustments
        pid = _make_product("INV_C", "CC", wh_price=1.0)  # helper puts 100 on TEST_WH

        note = "monthly count 2026-07"
        ok, err, n = apply_inventory_adjustments(
            "TEST_WH",
            [{"product_id": pid, "delta": -5}],
            note=note,
        )
        assert ok, err
        assert n == 1

        with _connect() as con:
            qty_after = con.execute(
                "SELECT qty FROM stock WHERE warehouse_code = ? AND product_id = ?",
                ("TEST_WH", pid),
            ).fetchone()[0]
            assert float(qty_after) == 95.0, f"expected 100-5=95, got {qty_after}"

            op = con.execute(
                "SELECT op_type, source, qty, note FROM stock_ops"
                " WHERE product_id = ? ORDER BY id DESC LIMIT 1",
                (pid,),
            ).fetchone()
            assert op["op_type"] == "ADJUST"
            assert op["source"] == "INVENTORY"
            assert float(op["qty"]) == -5.0
            assert op["note"] == note

    def test_zero_deltas_are_skipped_when_batched_with_real_ones(self) -> None:
        from app.db.sqlite import apply_inventory_adjustments
        p1 = _make_product("INV_D1", "D1", wh_price=1.0)
        p2 = _make_product("INV_D2", "D2", wh_price=1.0)  # will be delta=0
        ok, err, n = apply_inventory_adjustments(
            "TEST_WH",
            [
                {"product_id": p1, "delta": 3},
                {"product_id": p2, "delta": 0},
            ],
            note="mixed batch",
        )
        assert ok, err
        assert n == 1, f"expected 1 ADJUST op (zero deltas skipped), got {n}"


class TestInventoryDiscrepancyReport:
    def test_report_aggregates_surplus_and_shortage(self) -> None:
        from app.db.sqlite import apply_inventory_adjustments, get_inventory_discrepancies

        p_short = _make_product("INV_E1", "E1", wh_price=1.0)
        p_surp  = _make_product("INV_E2", "E2", wh_price=1.0)

        apply_inventory_adjustments(
            "TEST_WH",
            [
                {"product_id": p_short, "delta": -4},
                {"product_id": p_surp,  "delta":  2},
            ],
            note="discrepancy test",
        )

        today = date.today().isoformat()
        rpt = get_inventory_discrepancies(today, today, warehouse_codes=["TEST_WH"])

        # These are cumulative across every ADJUST test that ran on TEST_WH today,
        # so we use >= not == to stay independent of test order.
        assert rpt["totals"]["shortage"] >= 4.0
        assert rpt["totals"]["surplus"]  >= 2.0
        assert rpt["totals"]["n_ops"]    >= 2

        # Wrong-warehouse filter → this batch is invisible.
        rpt_other = get_inventory_discrepancies(
            today, today, warehouse_codes=["SECOND_WH"],
        )
        # Only ops on SECOND_WH count here. Our test used TEST_WH, so surplus/shortage
        # from THIS test must not appear. Other tests could have written to SECOND_WH,
        # but nothing in this file does.
        assert rpt_other["totals"]["surplus"] == 0.0
        assert rpt_other["totals"]["shortage"] == 0.0

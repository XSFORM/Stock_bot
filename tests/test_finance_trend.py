"""Phase 4: monthly trend for the finance report.

The key invariant the coworker asked for: summing month slices must equal
the full-range total (to the cent). If it doesn't, we have two separate
implementations of the same maths — which will eventually drift and give
the user contradicting numbers.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def db_path(tmp_path_factory: pytest.TempPathFactory) -> str:
    path = tmp_path_factory.mktemp("trend") / "stock.db"
    os.environ["DB_PATH"] = str(path)
    import app.db.sqlite as _sql
    _sql.DB_PATH = Path(path)
    return str(path)


@pytest.fixture(scope="module", autouse=True)
def init(db_path: str) -> None:
    from app.db.sqlite import init_db
    init_db()
    # One warehouse so profit_report can find anything.
    import app.db.sqlite as _sql
    with _sql._connect() as con:
        con.execute("INSERT OR IGNORE INTO warehouses (code, title) VALUES ('WH', 'Warehouse')")
        con.commit()


def _cat_id(name: str) -> int:
    from app.db.sqlite import list_expense_categories
    for c in list_expense_categories(include_archived=True):
        if c["name"] == name:
            return int(c["id"])
    raise AssertionError(f"category not found: {name}")


def _insert_legacy_sale(client_name: str, when_date: str, qty: float,
                        unit_price: float, cost_price: float) -> None:
    """Insert a completed sale bypassing cart flow, for a fixed date."""
    import app.db.sqlite as _sql
    from app.db.sqlite import add_client, add_or_get_product_id, receive_stock_by_product_id
    add_client(client_name)
    with _sql._connect() as con:
        cid_row = con.execute("SELECT id FROM clients WHERE name = ?", (client_name,)).fetchone()
        cid = int(cid_row["id"])
    pid, _ = add_or_get_product_id("TREND", f"m-{when_date}-{client_name}", "P", cost_price)
    receive_stock_by_product_id("WH", pid, qty + 10)
    ts = f"{when_date} 12:00:00"
    with _sql._connect() as con:
        con.execute(
            "INSERT INTO carts (client_id, warehouse_code, status, created_at)"
            " VALUES (?, 'WH', 'CLOSED', ?)", (cid, ts),
        )
        cart_id = con.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
        con.execute(
            "INSERT INTO cart_items (cart_id, product_id, free_line, free_name,"
            "                        qty, price_mode, unit_price, total, cost_price)"
            " VALUES (?, ?, 0, '', ?, 'custom', ?, ?, ?)",
            (cart_id, pid, qty, unit_price, unit_price * qty, cost_price),
        )
        max_num = con.execute("SELECT COALESCE(MAX(number), 0) FROM invoices").fetchone()[0]
        con.execute(
            "INSERT INTO invoices (number, cart_id, currency, total, created_at)"
            " VALUES (?, ?, 'USD', ?, ?)",
            (max_num + 1, cart_id, unit_price * qty, ts),
        )
        con.commit()


# ═════════════════════════════════════════════════════════════════════════════


class TestMonthSlicing:
    def test_single_month_range(self) -> None:
        from app.db.sqlite import _iter_month_slices
        slices = list(_iter_month_slices("2026-05-10", "2026-05-20"))
        assert slices == [("2026-05", "2026-05-10", "2026-05-20")]

    def test_two_months_range_clips_ends(self) -> None:
        from app.db.sqlite import _iter_month_slices
        slices = list(_iter_month_slices("2026-05-15", "2026-06-10"))
        assert slices == [
            ("2026-05", "2026-05-15", "2026-05-31"),
            ("2026-06", "2026-06-01", "2026-06-10"),
        ]

    def test_full_year(self) -> None:
        from app.db.sqlite import _iter_month_slices
        slices = list(_iter_month_slices("2026-01-01", "2026-12-31"))
        assert len(slices) == 12
        assert slices[0]  == ("2026-01", "2026-01-01", "2026-01-31")
        assert slices[1]  == ("2026-02", "2026-02-01", "2026-02-28")
        assert slices[11] == ("2026-12", "2026-12-01", "2026-12-31")

    def test_reversed_range_yields_nothing(self) -> None:
        from app.db.sqlite import _iter_month_slices
        assert list(_iter_month_slices("2026-06-10", "2026-05-01")) == []


# ═════════════════════════════════════════════════════════════════════════════


class TestFinanceMonthlyTrend:
    def test_sum_of_months_matches_totals(self) -> None:
        """
        The whole point of Phase 4: sum(monthly) == totals. Otherwise
        we've written a second, drift-prone implementation of profit maths.
        """
        from app.db.sqlite import (
            get_profit_report, get_expenses_summary,
            get_finance_monthly_trend, add_expense,
        )

        # Sales in three different months so we actually have >1 slice.
        _insert_legacy_sale("A", "2026-03-05", 2, 10.0, 4.0)
        _insert_legacy_sale("A", "2026-03-20", 1, 20.0, 8.0)
        _insert_legacy_sale("B", "2026-04-10", 3, 15.0, 6.0)
        _insert_legacy_sale("B", "2026-05-15", 5, 12.0, 5.0)

        # A mix of business + personal expenses across the same months.
        add_expense("2026-03-15", _cat_id("Аренда"),        500.0, "мар")
        add_expense("2026-04-15", _cat_id("Аренда"),        500.0, "апр")
        add_expense("2026-05-15", _cat_id("Аренда"),        500.0, "май")
        add_expense("2026-04-20", _cat_id("Зарплата"),      300.0, "апр")
        add_expense("2026-05-01", _cat_id("Личные покупки"), 80.0, "мой")

        date_from, date_to = "2026-03-01", "2026-05-31"
        monthly = get_finance_monthly_trend(date_from, date_to)
        assert len(monthly) == 3, [m["month"] for m in monthly]

        profit = get_profit_report(date_from, date_to)
        exp    = get_expenses_summary(date_from, date_to)
        total_gross = round(float(profit["totals"]["profit"]), 2)
        total_biz   = round(float(exp["totals"]["business"]), 2)
        total_pers  = round(float(exp["totals"]["personal"]), 2)
        total_biznet    = round(total_gross - total_biz, 2)
        total_walletnet = round(total_biznet - total_pers, 2)

        # Sum each column across months and compare (2-cent tolerance
        # for cumulative rounding is generous — we should hit exactly).
        sum_gross = round(sum(m["gross_profit"]      for m in monthly), 2)
        sum_biz   = round(sum(m["business_expenses"] for m in monthly), 2)
        sum_pers  = round(sum(m["personal_expenses"] for m in monthly), 2)
        sum_biznet    = round(sum(m["business_net"] for m in monthly), 2)
        sum_walletnet = round(sum(m["wallet_net"]   for m in monthly), 2)

        assert sum_gross      == pytest.approx(total_gross,     abs=0.02)
        assert sum_biz        == pytest.approx(total_biz,       abs=0.02)
        assert sum_pers       == pytest.approx(total_pers,      abs=0.02)
        assert sum_biznet     == pytest.approx(total_biznet,    abs=0.02)
        assert sum_walletnet  == pytest.approx(total_walletnet, abs=0.02)

    def test_row_shape_and_field_names(self) -> None:
        from app.db.sqlite import get_finance_monthly_trend
        trend = get_finance_monthly_trend("2026-03-01", "2026-05-31")
        assert trend
        row = trend[0]
        for key in ("month", "date_from", "date_to", "revenue",
                    "gross_profit", "business_expenses", "business_net",
                    "personal_expenses", "wallet_net"):
            assert key in row, f"missing field: {key}"
        # Types are numeric where expected
        assert isinstance(row["gross_profit"], float)

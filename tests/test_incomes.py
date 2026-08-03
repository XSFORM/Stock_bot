"""Phase 7 (bot idea): incomes from services.

The important invariants (mirror of the expenses module):
  * TMT rate is a snapshot — a later rate change never re-prices old rows.
  * Editing just the amount without switching currency keeps rate_used.
  * Adding service income NEVER moves get_profit_report totals — trading
    margin must stay clean. Services show up only in the finance report's
    wallet-level maths.
  * Monthly trend sums equal the total-range figures (same reuse pattern
    we already tested for finance_monthly_trend).
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def db_path(tmp_path_factory: pytest.TempPathFactory) -> str:
    path = tmp_path_factory.mktemp("incomes") / "stock.db"
    os.environ["DB_PATH"] = str(path)
    import app.db.sqlite as _sql
    _sql.DB_PATH = Path(path)
    return str(path)


@pytest.fixture(scope="module", autouse=True)
def init(db_path: str) -> None:
    from app.db.sqlite import init_db
    init_db()
    # Warehouse the profit-margin defence test needs.
    import app.db.sqlite as _sql
    with _sql._connect() as con:
        con.execute("INSERT OR IGNORE INTO warehouses (code, title) VALUES ('SRV_WH', 'W')")
        con.commit()


@pytest.fixture(autouse=True)
def _reset_rate():
    from app.db.sqlite import set_setting
    set_setting("pocket_price_tmt_rate", "19.40")


def _inc_cat_id(name: str) -> int:
    from app.db.sqlite import list_income_categories
    for c in list_income_categories(include_archived=True):
        if c["name"] == name:
            return int(c["id"])
    raise AssertionError(f"seeded income category not found: {name}")


def _one(rows: list) -> dict:
    assert len(rows) == 1, f"expected 1, got {len(rows)}: {rows}"
    return rows[0]


# ═════════════════════════════════════════════════════════════════════════════
# CRUD + storage
# ═════════════════════════════════════════════════════════════════════════════


class TestSeedAndCrud:
    def test_default_income_categories_present(self) -> None:
        from app.db.sqlite import list_income_categories
        names = {c["name"] for c in list_income_categories()}
        for n in ("Антивирус/ПО", "Ремонт ПК", "Ремонт PlayStation",
                  "Запись игр", "Прошивка/чиповка", "Прочие услуги"):
            assert n in names, f"missing seeded category: {n}"

    def test_add_and_list_income_categories(self) -> None:
        from app.db.sqlite import add_income_category, list_income_categories
        ok, err = add_income_category("Wi-Fi настройка")
        assert ok, err
        names = {c["name"] for c in list_income_categories()}
        assert "Wi-Fi настройка" in names

    def test_income_category_no_duplicates(self) -> None:
        from app.db.sqlite import add_income_category
        ok, err = add_income_category("Ремонт ПК")   # already seeded
        assert not ok and err == "duplicate_name"

    def test_income_category_archive_hides_from_default_list(self) -> None:
        from app.db.sqlite import (
            add_income_category, set_income_category_archived, list_income_categories,
        )
        add_income_category("Устаревшая услуга")
        cid = next(c["id"] for c in list_income_categories()
                   if c["name"] == "Устаревшая услуга")
        ok, err = set_income_category_archived(cid, True)
        assert ok, err
        assert "Устаревшая услуга" not in {c["name"] for c in list_income_categories()}
        assert "Устаревшая услуга" in {c["name"] for c in list_income_categories(include_archived=True)}


class TestTmtStorage:
    def test_tmt_income_stores_all_fields(self) -> None:
        """500 TMT @ 19.40 → 25.77 $, rate_used = 19.40, currency = 'TMT'."""
        from app.db.sqlite import add_income, list_incomes
        cid = _inc_cat_id("Ремонт ПК")
        ok, err = add_income("2027-04-01", cid,
                             currency="TMT", amount_original=500.0)
        assert ok, err
        row = _one([r for r in list_incomes(date_from="2027-04-01", date_to="2027-04-01")
                    if r["category_name"] == "Ремонт ПК"])
        assert row["currency"] == "TMT"
        assert float(row["amount_original"]) == pytest.approx(500.0)
        assert float(row["rate_used"]) == pytest.approx(19.40)
        assert float(row["amount_usd"]) == pytest.approx(25.77, abs=0.01)

    def test_usd_income_is_rate_1(self) -> None:
        from app.db.sqlite import add_income, list_incomes
        cid = _inc_cat_id("Запись игр")
        ok, err = add_income("2027-04-02", cid,
                             currency="USD", amount_original=15.0)
        assert ok, err
        row = _one([r for r in list_incomes(date_from="2027-04-02", date_to="2027-04-02")
                    if r["category_name"] == "Запись игр"])
        assert row["currency"] == "USD"
        assert float(row["rate_used"]) == pytest.approx(1.0)
        assert float(row["amount_usd"]) == pytest.approx(15.0)


# ═════════════════════════════════════════════════════════════════════════════
# The critical anti-drift test
# ═════════════════════════════════════════════════════════════════════════════


class TestRateSnapshot:
    def test_changing_rate_does_not_reprice_historical_incomes(self) -> None:
        from app.db.sqlite import (
            add_income, list_incomes, get_incomes_summary, set_setting,
        )
        cid = _inc_cat_id("Прошивка/чиповка")
        add_income("2027-05-05", cid, currency="TMT", amount_original=200.0)

        before = _one([r for r in list_incomes(date_from="2027-05-05", date_to="2027-05-05")
                       if r["category_name"] == "Прошивка/чиповка"])
        original_usd = float(before["amount_usd"])

        set_setting("pocket_price_tmt_rate", "22.00")

        after = _one([r for r in list_incomes(date_from="2027-05-05", date_to="2027-05-05")
                      if r["category_name"] == "Прошивка/чиповка"])
        assert float(after["amount_usd"]) == pytest.approx(original_usd)
        assert float(after["rate_used"]) == pytest.approx(19.40)

        summary = get_incomes_summary("2027-05-01", "2027-05-31")
        assert summary["totals"]["all"] == pytest.approx(original_usd, abs=0.02)


class TestEditingKeepsRate:
    def test_editing_amount_only_keeps_rate(self) -> None:
        from app.db.sqlite import (
            add_income, update_income, list_incomes, set_setting,
        )
        cid = _inc_cat_id("Антивирус/ПО")
        add_income("2027-06-05", cid, currency="TMT", amount_original=100.0)
        row = _one([r for r in list_incomes(date_from="2027-06-05", date_to="2027-06-05")
                    if r["category_name"] == "Антивирус/ПО"])
        income_id = int(row["id"])
        original_rate = float(row["rate_used"])

        set_setting("pocket_price_tmt_rate", "23.00")
        ok, err = update_income(income_id, "2027-06-05", cid,
                                currency="TMT", amount_original=120.0)
        assert ok, err
        updated = _one([r for r in list_incomes(date_from="2027-06-05", date_to="2027-06-05")
                        if r["category_name"] == "Антивирус/ПО"])
        assert float(updated["rate_used"]) == pytest.approx(original_rate)
        assert float(updated["amount_usd"]) == pytest.approx(120.0 / original_rate, abs=0.02)


# ═════════════════════════════════════════════════════════════════════════════
# The trading-margin defence — the whole point of Phase 2
# ═════════════════════════════════════════════════════════════════════════════


def _insert_legacy_sale(date_iso: str, unit_price: float, cost_price: float, qty: float = 1) -> None:
    """Insert a completed sale directly, so profit_report sees a known row."""
    import app.db.sqlite as _sql
    from app.db.sqlite import add_client, add_or_get_product_id, receive_stock_by_product_id
    client_name = f"SrvClient-{date_iso}-{unit_price}"
    add_client(client_name)
    with _sql._connect() as con:
        cid = int(con.execute("SELECT id FROM clients WHERE name = ?",
                              (client_name,)).fetchone()["id"])
    pid, _ = add_or_get_product_id("SRV", f"p-{date_iso}-{unit_price}", "p", cost_price)
    receive_stock_by_product_id("SRV_WH", pid, qty + 5)
    ts = f"{date_iso} 12:00:00"
    with _sql._connect() as con:
        con.execute(
            "INSERT INTO carts (client_id, warehouse_code, status, created_at)"
            " VALUES (?, 'SRV_WH', 'CLOSED', ?)", (cid, ts),
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


class TestTradingMarginNotAffected:
    def test_adding_service_income_does_not_move_profit_report(self) -> None:
        """
        Independent trading margin: revenue 20$, cost 12$, profit 8$, margin 40%.
        Add a 500 USD service income in the same period — profit_report totals
        MUST NOT change (services must never dilute trading margin).
        """
        from app.db.sqlite import (
            get_profit_report, add_income,
        )

        _insert_legacy_sale("2027-07-05", unit_price=20.0, cost_price=12.0, qty=1)

        before = get_profit_report("2027-07-01", "2027-07-31")
        before_snapshot = {
            "revenue":    round(float(before["totals"]["revenue"]), 2),
            "cost":       round(float(before["totals"]["cost"]), 2),
            "profit":     round(float(before["totals"]["profit"]), 2),
            "margin_pct": round(float(before["totals"]["margin_pct"]), 2),
        }

        # Add a big service income the same month.
        add_income("2027-07-10", _inc_cat_id("Ремонт ПК"),
                   currency="USD", amount_original=500.0)

        after = get_profit_report("2027-07-01", "2027-07-31")
        after_snapshot = {
            "revenue":    round(float(after["totals"]["revenue"]), 2),
            "cost":       round(float(after["totals"]["cost"]), 2),
            "profit":     round(float(after["totals"]["profit"]), 2),
            "margin_pct": round(float(after["totals"]["margin_pct"]), 2),
        }
        assert after_snapshot == before_snapshot, (
            f"profit_report shifted after adding service income:\n"
            f"  before: {before_snapshot}\n  after:  {after_snapshot}"
        )

    def test_monthly_trend_includes_service_income(self) -> None:
        from app.db.sqlite import (
            add_income, get_finance_monthly_trend, get_incomes_summary,
        )
        # A month where nothing else happens except services.
        add_income("2027-08-05", _inc_cat_id("Ремонт PlayStation"),
                   currency="USD", amount_original=40.0)
        add_income("2027-08-20", _inc_cat_id("Запись игр"),
                   currency="USD", amount_original=10.0)

        trend = get_finance_monthly_trend("2027-08-01", "2027-08-31")
        assert len(trend) == 1
        row = trend[0]
        assert row["month"] == "2027-08"
        assert row["service_income"] == pytest.approx(50.0)
        # total_income = gross_profit + service_income
        assert row["total_income"] == pytest.approx(
            row["gross_profit"] + row["service_income"], abs=0.02,
        )

    def test_monthly_service_income_sums_to_total(self) -> None:
        """sum(monthly.service_income) == totals(get_incomes_summary(range))."""
        from app.db.sqlite import (
            add_income, get_finance_monthly_trend, get_incomes_summary,
        )
        add_income("2027-09-05", _inc_cat_id("Прочие услуги"),
                   currency="USD", amount_original=100.0)
        add_income("2027-10-05", _inc_cat_id("Прочие услуги"),
                   currency="USD", amount_original=200.0)
        add_income("2027-11-05", _inc_cat_id("Прочие услуги"),
                   currency="USD", amount_original=50.0)

        trend = get_finance_monthly_trend("2027-09-01", "2027-11-30")
        summed = round(sum(m["service_income"] for m in trend), 2)
        totals = get_incomes_summary("2027-09-01", "2027-11-30")["totals"]["all"]
        assert summed == pytest.approx(round(totals, 2), abs=0.02)


# ═════════════════════════════════════════════════════════════════════════════
# ON DELETE RESTRICT — can't drop a category with bookings
# ═════════════════════════════════════════════════════════════════════════════


class TestForeignKey:
    def test_deleting_category_used_by_income_raises(self) -> None:
        """Requirement #22 — deletion must fail, archival must succeed."""
        import sqlite3
        import app.db.sqlite as _sql
        from app.db.sqlite import (
            add_income_category, add_income, set_income_category_archived,
            list_income_categories,
        )
        add_income_category("Врем.услуга")
        cid = next(c["id"] for c in list_income_categories(include_archived=True)
                   if c["name"] == "Врем.услуга")
        add_income("2027-12-01", cid, currency="USD", amount_original=10.0)

        # DELETE should fail thanks to ON DELETE RESTRICT.
        with _sql._connect() as con:
            con.execute("PRAGMA foreign_keys = ON")
            with pytest.raises(sqlite3.IntegrityError):
                con.execute("DELETE FROM income_categories WHERE id = ?", (cid,))
                con.commit()
        # Archive still works.
        ok, err = set_income_category_archived(cid, True)
        assert ok, err

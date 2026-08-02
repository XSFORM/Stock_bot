"""Phase 6 (bot idea): expenses in TMT with a frozen exchange rate.

The one contract we must never break: once an expense row is saved, its
USD equivalent and its rate_used never change based on today's setting.
If the operator raises `pocket_price_tmt_rate` next month, last month's
finance report reads exactly the same as before.

That's the whole reason we snapshot rate_used and never re-compute it.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def db_path(tmp_path_factory: pytest.TempPathFactory) -> str:
    path = tmp_path_factory.mktemp("tmt") / "stock.db"
    os.environ["DB_PATH"] = str(path)
    import app.db.sqlite as _sql
    _sql.DB_PATH = Path(path)
    return str(path)


@pytest.fixture(scope="module", autouse=True)
def init(db_path: str) -> None:
    from app.db.sqlite import init_db
    init_db()


@pytest.fixture(autouse=True)
def _reset_rate():
    """Every test starts from a known rate so cross-test pollution is impossible."""
    from app.db.sqlite import set_setting
    set_setting("pocket_price_tmt_rate", "19.40")


def _cat_id(name: str) -> int:
    from app.db.sqlite import list_expense_categories
    for c in list_expense_categories(include_archived=True):
        if c["name"] == name:
            return int(c["id"])
    raise AssertionError(f"seeded category not found: {name}")


def _one(rows: list) -> dict:
    """Small helper — assert a single row and return it."""
    assert len(rows) == 1, f"expected 1 row, got {len(rows)}: {rows}"
    return rows[0]


# ═════════════════════════════════════════════════════════════════════════════
# Storage semantics
# ═════════════════════════════════════════════════════════════════════════════


class TestTmtStorage:
    def test_tmt_expense_stores_all_four_fields(self) -> None:
        """1080 TMT @ 19.40 → 55.67 $, rate_used = 19.40, currency = 'TMT'."""
        from app.db.sqlite import add_expense, list_expenses

        rent = _cat_id("Аренда")
        ok, err = add_expense("2026-08-01", rent, note="",
                              currency="TMT", amount_original=1080.0)
        assert ok, err

        row = _one([e for e in list_expenses(date_from="2026-08-01", date_to="2026-08-01")
                    if e["category_name"] == "Аренда"])
        assert row["currency"] == "TMT"
        assert float(row["amount_original"]) == pytest.approx(1080.0)
        assert float(row["rate_used"]) == pytest.approx(19.40)
        assert float(row["amount_usd"]) == pytest.approx(55.67, abs=0.005)

    def test_usd_expense_is_rate_1(self) -> None:
        from app.db.sqlite import add_expense, list_expenses
        transport = _cat_id("Транспорт")
        ok, err = add_expense("2026-08-02", transport, note="",
                              currency="USD", amount_original=42.0)
        assert ok, err
        row = _one([e for e in list_expenses(date_from="2026-08-02", date_to="2026-08-02")
                    if e["category_name"] == "Транспорт"])
        assert row["currency"] == "USD"
        assert float(row["amount_original"]) == pytest.approx(42.0)
        assert float(row["rate_used"]) == pytest.approx(1.0)
        assert float(row["amount_usd"]) == pytest.approx(42.0)

    def test_legacy_signature_still_works(self) -> None:
        """Callers passing amount_usd positionally must still succeed (USD)."""
        from app.db.sqlite import add_expense, list_expenses
        util = _cat_id("Коммуналка")
        ok, err = add_expense("2026-08-03", util, 15.5, "старый API")
        assert ok, err
        row = _one([e for e in list_expenses(date_from="2026-08-03", date_to="2026-08-03")
                    if e["category_name"] == "Коммуналка"])
        assert row["currency"] == "USD"
        assert float(row["rate_used"]) == pytest.approx(1.0)
        assert float(row["amount_usd"]) == pytest.approx(15.5)

    def test_bad_currency_rejected(self) -> None:
        from app.db.sqlite import add_expense
        rent = _cat_id("Аренда")
        ok, err = add_expense("2026-08-04", rent, currency="EUR",
                              amount_original=100.0)
        assert not ok and err == "bad_currency"


# ═════════════════════════════════════════════════════════════════════════════
# The critical anti-drift test
# ═════════════════════════════════════════════════════════════════════════════


class TestRateSnapshot:
    def test_changing_rate_does_not_reprice_historical_rows(self) -> None:
        """
        The single most important guarantee.

        Save at 19.40 → change rate to 19.80 → the stored row and the
        report must still show the old USD figure. Otherwise closed
        months silently change value.
        """
        from app.db.sqlite import (
            add_expense, list_expenses, get_expenses_summary, set_setting,
        )

        salary = _cat_id("Зарплата")
        ok, err = add_expense("2026-09-05", salary,
                              currency="TMT", amount_original=1080.0)
        assert ok, err

        before = _one([e for e in list_expenses(date_from="2026-09-05", date_to="2026-09-05")
                       if e["category_name"] == "Зарплата"])
        original_usd = float(before["amount_usd"])
        original_rate = float(before["rate_used"])

        # Rate changes (e.g. September manat weakened).
        set_setting("pocket_price_tmt_rate", "19.80")

        after = _one([e for e in list_expenses(date_from="2026-09-05", date_to="2026-09-05")
                      if e["category_name"] == "Зарплата"])
        # The row itself is untouched.
        assert float(after["amount_usd"]) == pytest.approx(original_usd)
        assert float(after["rate_used"]) == pytest.approx(original_rate)

        # And the report totals are consistent (summed via amount_usd).
        summary = get_expenses_summary("2026-09-01", "2026-09-30")
        assert summary["totals"]["business"] == pytest.approx(original_usd, abs=0.02)


# ═════════════════════════════════════════════════════════════════════════════
# Editing rules (same trap as cost_price)
# ═════════════════════════════════════════════════════════════════════════════


class TestEditingKeepsRate:
    def test_updating_amount_keeps_original_rate(self) -> None:
        """Fix a typo in the amount — must NOT re-price at today's rate."""
        from app.db.sqlite import (
            add_expense, update_expense, list_expenses, set_setting,
        )

        rent = _cat_id("Аренда")
        add_expense("2026-10-05", rent, currency="TMT", amount_original=1080.0)
        row = _one([e for e in list_expenses(date_from="2026-10-05", date_to="2026-10-05")
                    if e["category_name"] == "Аренда"])
        expense_id = int(row["id"])
        original_rate = float(row["rate_used"])

        # Rate changes later …
        set_setting("pocket_price_tmt_rate", "22.00")
        # … and user fixes the amount (say it was 1100, not 1080).
        ok, err = update_expense(
            expense_id, "2026-10-05", rent,
            currency="TMT", amount_original=1100.0,
        )
        assert ok, err

        updated = _one([e for e in list_expenses(date_from="2026-10-05", date_to="2026-10-05")
                        if e["category_name"] == "Аренда"])
        # Rate stays frozen at the original.
        assert float(updated["rate_used"]) == pytest.approx(original_rate)
        # USD recomputed from the new amount but the OLD rate.
        assert float(updated["amount_usd"]) == pytest.approx(1100.0 / original_rate, abs=0.02)

    def test_changing_currency_uses_todays_rate(self) -> None:
        """
        Switching currency is a fresh valuation — new rate is taken from
        the current setting. This is intentional: converting a USD row to
        TMT (or vice-versa) is a re-declaration of what actually happened.
        """
        from app.db.sqlite import (
            add_expense, update_expense, list_expenses, set_setting,
        )

        misc = _cat_id("Прочие бизнес")
        add_expense("2026-11-05", misc, currency="USD", amount_original=50.0)
        expense_id = int(_one([e for e in list_expenses(date_from="2026-11-05", date_to="2026-11-05")
                               if e["category_name"] == "Прочие бизнес"])["id"])

        set_setting("pocket_price_tmt_rate", "20.00")
        ok, err = update_expense(
            expense_id, "2026-11-05", misc,
            currency="TMT", amount_original=1000.0,
        )
        assert ok, err

        row = _one([e for e in list_expenses(date_from="2026-11-05", date_to="2026-11-05")
                    if e["category_name"] == "Прочие бизнес"])
        assert row["currency"] == "TMT"
        assert float(row["rate_used"]) == pytest.approx(20.00)
        assert float(row["amount_usd"]) == pytest.approx(50.0, abs=0.02)


# ═════════════════════════════════════════════════════════════════════════════
# Migration backfill
# ═════════════════════════════════════════════════════════════════════════════


class TestMigrationBackfill:
    def test_new_columns_default_to_usd_after_migration(self) -> None:
        """
        Rows inserted before the migration must appear as USD rows with
        rate_used = 1 and amount_original == amount_usd. init_db is
        idempotent so the backfill runs each start-up on any 0-original
        legacy rows — this is a regression guard for that path.
        """
        import app.db.sqlite as _sql
        misc = _cat_id("Прочие личные")
        # Simulate a pre-migration row: only amount_usd, no currency fields.
        with _sql._connect() as con:
            con.execute(
                "INSERT INTO expenses (date, category_id, amount_usd, note,"
                "                      currency, amount_original, rate_used)"
                " VALUES (?, ?, ?, ?, 'USD', 0, 1)",
                ("2026-12-01", misc, 25.0, "legacy row"),
            )
            con.commit()
        # Rerun the migration (idempotent).
        with _sql._connect() as con:
            _sql._ensure_expenses_currency_columns(con)
            con.commit()

        from app.db.sqlite import list_expenses
        row = _one([e for e in list_expenses(date_from="2026-12-01", date_to="2026-12-01")
                    if e["note"] == "legacy row"])
        assert row["currency"] == "USD"
        assert float(row["rate_used"]) == pytest.approx(1.0)
        # amount_original was 0 → backfilled to amount_usd.
        assert float(row["amount_original"]) == pytest.approx(25.0)


# ═════════════════════════════════════════════════════════════════════════════
# tmt_original in the summary
# ═════════════════════════════════════════════════════════════════════════════


class TestSummaryTmtLine:
    def test_summary_reports_tmt_original_sum(self) -> None:
        from app.db.sqlite import add_expense, get_expenses_summary

        # Fresh period no other tests touch.
        rent = _cat_id("Аренда")
        add_expense("2027-01-05", rent, currency="TMT", amount_original=500.0)
        add_expense("2027-01-10", rent, currency="TMT", amount_original=700.0)
        add_expense("2027-01-15", rent, currency="USD", amount_original=30.0)

        s = get_expenses_summary("2027-01-01", "2027-01-31")
        # Only TMT rows are summed for tmt_original.
        assert s["totals"]["tmt_original"] == pytest.approx(1200.0)

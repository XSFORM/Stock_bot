"""Tests for Phase 5 — expenses & finance report.

Covers:
  - default categories seeded on init_db
  - CRUD for categories (add, edit, archive, duplicate name rejection)
  - CRUD for expenses (add, edit, delete, validation)
  - list_expenses filters (period, category, kind, search)
  - archived categories are excluded from the picker but still counted in
    reports for existing rows
  - get_expenses_summary aggregates business/personal separately
  - archived-category expense still lists and counts in the summary
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest


# ─── Test infrastructure ─────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def db_path(tmp_path_factory: pytest.TempPathFactory) -> str:
    """
    Own tmp DB for this module, and force the already-imported sqlite
    module to point at it (see test_profit_and_inventory for the reason).
    """
    path = tmp_path_factory.mktemp("expenses") / "stock.db"
    os.environ["DB_PATH"] = str(path)
    import app.db.sqlite as _sql
    _sql.DB_PATH = Path(path)
    return str(path)


@pytest.fixture(scope="module", autouse=True)
def init(db_path: str) -> None:
    from app.db.sqlite import init_db
    init_db()


# ═════════════════════════════════════════════════════════════════════════════
# Categories
# ═════════════════════════════════════════════════════════════════════════════


class TestExpenseCategoriesSeed:
    def test_default_categories_present(self) -> None:
        from app.db.sqlite import list_expense_categories
        cats = list_expense_categories()
        names = {c["name"] for c in cats}
        # Business core set
        for n in ("Аренда", "Зарплата", "Налоги/Госторг", "Прочие бизнес"):
            assert n in names, f"missing business category: {n}"
        # Personal core set
        for n in ("Личные покупки", "Семья", "Прочие личные"):
            assert n in names, f"missing personal category: {n}"

    def test_default_categories_have_correct_kind(self) -> None:
        from app.db.sqlite import list_expense_categories
        cats = {c["name"]: c for c in list_expense_categories()}
        assert cats["Аренда"]["kind"] == "business"
        assert cats["Личные покупки"]["kind"] == "personal"


class TestExpenseCategoriesCRUD:
    def test_add_custom_category(self) -> None:
        from app.db.sqlite import add_expense_category, list_expense_categories
        ok, err = add_expense_category("Реклама Instagram", "business")
        assert ok, err
        names = {c["name"] for c in list_expense_categories()}
        assert "Реклама Instagram" in names

    def test_add_rejects_empty_name(self) -> None:
        from app.db.sqlite import add_expense_category
        ok, err = add_expense_category("   ", "business")
        assert not ok and err == "name_required"

    def test_add_rejects_bad_kind(self) -> None:
        from app.db.sqlite import add_expense_category
        ok, err = add_expense_category("Тестовая", "junk")
        assert not ok and err == "bad_kind"

    def test_add_rejects_duplicate_name(self) -> None:
        from app.db.sqlite import add_expense_category
        # "Аренда" is a seeded category
        ok, err = add_expense_category("Аренда", "business")
        assert not ok and err == "duplicate_name"

    def test_update_category_renames_and_switches_kind(self) -> None:
        from app.db.sqlite import (
            add_expense_category, update_expense_category, list_expense_categories,
        )
        add_expense_category("Тест-Категория", "business")
        cid = next(c["id"] for c in list_expense_categories() if c["name"] == "Тест-Категория")
        ok, err = update_expense_category(cid, "Тест-Категория2", "personal")
        assert ok, err
        cats = {c["id"]: c for c in list_expense_categories()}
        assert cats[cid]["name"] == "Тест-Категория2"
        assert cats[cid]["kind"] == "personal"

    def test_archive_hides_from_default_listing(self) -> None:
        from app.db.sqlite import (
            add_expense_category, set_expense_category_archived,
            list_expense_categories,
        )
        add_expense_category("Устаревшая", "business")
        cid = next(c["id"] for c in list_expense_categories() if c["name"] == "Устаревшая")
        ok, err = set_expense_category_archived(cid, True)
        assert ok, err
        active_names = {c["name"] for c in list_expense_categories()}
        assert "Устаревшая" not in active_names
        all_names = {c["name"] for c in list_expense_categories(include_archived=True)}
        assert "Устаревшая" in all_names


# ═════════════════════════════════════════════════════════════════════════════
# Expenses CRUD
# ═════════════════════════════════════════════════════════════════════════════


def _cat_id(name: str) -> int:
    from app.db.sqlite import list_expense_categories
    for c in list_expense_categories(include_archived=True):
        if c["name"] == name:
            return int(c["id"])
    raise AssertionError(f"category not found: {name}")


class TestExpensesCRUD:
    def test_add_expense_validation(self) -> None:
        from app.db.sqlite import add_expense
        rent_id = _cat_id("Аренда")
        # Empty date
        ok, err = add_expense("", rent_id, 100)
        assert not ok and err == "date_required"
        # Zero amount
        ok, err = add_expense("2026-08-01", rent_id, 0)
        assert not ok and err == "amount_must_be_positive"
        # Negative amount
        ok, err = add_expense("2026-08-01", rent_id, -50)
        assert not ok and err == "amount_must_be_positive"
        # Non-existent category
        ok, err = add_expense("2026-08-01", 9999999, 100)
        assert not ok and err == "category_not_found"

    def test_add_edit_delete_happy_path(self) -> None:
        from app.db.sqlite import (
            add_expense, list_expenses, get_expense, update_expense, delete_expense,
        )
        rent_id = _cat_id("Аренда")
        ok, err = add_expense("2026-08-05", rent_id, 250.0, "test rent")
        assert ok, err
        found = [e for e in list_expenses() if e["note"] == "test rent"]
        assert len(found) == 1
        eid = found[0]["id"]

        # get_expense joins category info
        e = get_expense(eid)
        assert e["category_name"] == "Аренда"
        assert e["category_kind"] == "business"
        assert float(e["amount_usd"]) == 250.0

        # update
        ok, err = update_expense(eid, "2026-08-06", rent_id, 275.0, "test rent v2")
        assert ok, err
        e = get_expense(eid)
        assert e["date"] == "2026-08-06"
        assert float(e["amount_usd"]) == 275.0
        assert e["note"] == "test rent v2"

        # delete
        ok, err = delete_expense(eid)
        assert ok, err
        assert get_expense(eid) is None

    def test_delete_missing_expense(self) -> None:
        from app.db.sqlite import delete_expense
        ok, err = delete_expense(9999999)
        assert not ok and err == "expense_not_found"


# ═════════════════════════════════════════════════════════════════════════════
# List filters
# ═════════════════════════════════════════════════════════════════════════════


class TestListExpensesFilters:
    """Uses its own fresh set of expenses so we can assert exact counts."""

    def _seed(self) -> tuple[int, int]:
        """Insert 3 expenses across dates and kinds. Return (biz_id, pers_id)."""
        from app.db.sqlite import add_expense
        rent_id = _cat_id("Аренда")           # business
        phone_id = _cat_id("Личные покупки")  # personal
        # Distinct dates so tests can slice by period
        add_expense("2026-09-01", rent_id,  300.0, "list-test rent Sep")
        add_expense("2026-09-15", phone_id, 800.0, "list-test iPhone")
        add_expense("2026-10-01", rent_id,  300.0, "list-test rent Oct")
        return rent_id, phone_id

    def test_period_filter(self) -> None:
        from app.db.sqlite import list_expenses
        self._seed()
        september = [
            e for e in list_expenses("2026-09-01", "2026-09-30")
            if e["note"].startswith("list-test")
        ]
        assert len(september) == 2
        assert {e["note"] for e in september} == {
            "list-test rent Sep", "list-test iPhone",
        }

    def test_kind_filter(self) -> None:
        from app.db.sqlite import list_expenses
        biz = [e for e in list_expenses(kind="business") if e["note"].startswith("list-test")]
        pers = [e for e in list_expenses(kind="personal") if e["note"].startswith("list-test")]
        assert len(biz) == 2 and all(e["category_name"] == "Аренда" for e in biz)
        assert len(pers) == 1 and pers[0]["category_name"] == "Личные покупки"

    def test_category_filter(self) -> None:
        from app.db.sqlite import list_expenses
        rent_id = _cat_id("Аренда")
        rows = [
            e for e in list_expenses(category_id=rent_id)
            if e["note"].startswith("list-test")
        ]
        assert len(rows) == 2

    def test_search_matches_note_and_category(self) -> None:
        from app.db.sqlite import list_expenses
        # Note match
        rows = [e for e in list_expenses(search="iPhone") if e["note"].startswith("list-test")]
        assert len(rows) == 1
        # Category-name match (Аренда is Cyrillic — SQLite LIKE is case-sensitive
        # for non-ASCII by default, so search exactly as stored)
        rows = [e for e in list_expenses(search="Аренда") if e["note"].startswith("list-test")]
        assert len(rows) == 2


# ═════════════════════════════════════════════════════════════════════════════
# Summary + archived categories
# ═════════════════════════════════════════════════════════════════════════════


class TestExpensesSummary:
    def test_summary_splits_business_and_personal(self) -> None:
        from app.db.sqlite import get_expenses_summary
        # Uses the 3 expenses seeded in TestListExpensesFilters._seed
        # For September only: rent 300 + phone 800.
        summary = get_expenses_summary("2026-09-01", "2026-09-30")
        assert summary["totals"]["business"] >= 300.0
        assert summary["totals"]["personal"] >= 800.0
        # by_category is present and non-empty
        cats = {c["category"]: c for c in summary["by_category"]}
        assert "Аренда" in cats
        assert cats["Аренда"]["kind"] == "business"

    def test_archived_category_still_counts_in_summary(self) -> None:
        """
        If a category has been archived AFTER expenses were logged against it,
        those expenses must still show up in the finance report. Otherwise
        historical totals would silently change every time you archive a
        category.
        """
        from app.db.sqlite import (
            add_expense_category, set_expense_category_archived,
            add_expense, get_expenses_summary, list_expense_categories,
        )
        # Fresh category so we can archive it without touching seeded ones.
        ok, err = add_expense_category("Одноразовая аренда 2027", "business")
        assert ok, err
        cid = next(c["id"] for c in list_expense_categories() if c["name"] == "Одноразовая аренда 2027")

        # Book a bill dated in a period no other test touches.
        add_expense("2027-03-15", cid, 123.45, "archive-test")

        # Now archive the category and check the report still includes it.
        set_expense_category_archived(cid, True)

        summary = get_expenses_summary("2027-03-01", "2027-03-31")
        assert summary["totals"]["business"] >= 123.45
        cats = {c["category"]: c for c in summary["by_category"]}
        assert "Одноразовая аренда 2027" in cats
        assert cats["Одноразовая аренда 2027"]["amount"] == 123.45

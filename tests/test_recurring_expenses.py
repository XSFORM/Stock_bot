"""Phase 3: recurring expense templates.

Confirms the two things that matter for correctness:

1. CRUD + validation on the templates themselves.
2. get_missing_monthly_recurring uses (category_id AND current-month) as
   the key. Amount can differ (rent may have changed), a matching
   expense in the same month is still considered "fulfilled".
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def db_path(tmp_path_factory: pytest.TempPathFactory) -> str:
    path = tmp_path_factory.mktemp("recur") / "stock.db"
    os.environ["DB_PATH"] = str(path)
    import app.db.sqlite as _sql
    _sql.DB_PATH = Path(path)
    return str(path)


@pytest.fixture(scope="module", autouse=True)
def init(db_path: str) -> None:
    from app.db.sqlite import init_db
    init_db()


def _cat_id(name: str) -> int:
    from app.db.sqlite import list_expense_categories
    for c in list_expense_categories(include_archived=True):
        if c["name"] == name:
            return int(c["id"])
    raise AssertionError(f"seeded category not found: {name}")


# ═════════════════════════════════════════════════════════════════════════════
# CRUD / validation
# ═════════════════════════════════════════════════════════════════════════════


class TestRecurringCrud:
    def test_add_and_list(self) -> None:
        from app.db.sqlite import (
            add_recurring_expense, list_recurring_expenses,
        )
        rent = _cat_id("Аренда")
        ok, err = add_recurring_expense(rent, 500.0, 5, "магазин 15 м²")
        assert ok, err
        items = list_recurring_expenses()
        found = [i for i in items if i["category_name"] == "Аренда"
                                and float(i["amount_usd"]) == 500.0]
        assert len(found) == 1

    def test_validation(self) -> None:
        from app.db.sqlite import add_recurring_expense
        rent = _cat_id("Аренда")

        ok, err = add_recurring_expense(0, 100.0, 5)
        assert not ok and err == "category_required"

        ok, err = add_recurring_expense(rent, 0, 5)
        assert not ok and err == "amount_must_be_positive"

        ok, err = add_recurring_expense(rent, -10, 5)
        assert not ok and err == "amount_must_be_positive"

        ok, err = add_recurring_expense(rent, 100, 0)
        assert not ok and err == "day_out_of_range"

        ok, err = add_recurring_expense(rent, 100, 32)
        assert not ok and err == "day_out_of_range"

        ok, err = add_recurring_expense(99999, 100, 5)
        assert not ok and err == "category_not_found"

    def test_toggle_active(self) -> None:
        from app.db.sqlite import (
            add_recurring_expense, list_recurring_expenses,
            set_recurring_expense_active,
        )
        salary = _cat_id("Зарплата")
        add_recurring_expense(salary, 300.0, 1, "помощник")
        active_before = [i for i in list_recurring_expenses()
                         if i["category_name"] == "Зарплата"]
        assert active_before
        rid = active_before[0]["id"]

        ok, err = set_recurring_expense_active(rid, False)
        assert ok, err
        # Excluded from default list
        assert not any(i["id"] == rid for i in list_recurring_expenses())
        # But still present with include_inactive
        assert any(i["id"] == rid for i in list_recurring_expenses(include_inactive=True))

    def test_update_and_delete(self) -> None:
        from app.db.sqlite import (
            add_recurring_expense, update_recurring_expense,
            delete_recurring_expense, get_recurring_expense,
        )
        util = _cat_id("Коммуналка")
        add_recurring_expense(util, 40.0, 10, "свет+вода")
        rid = _find_template_id(util, 40.0)

        ok, err = update_recurring_expense(rid, util, 55.0, 12, "свет подорожал")
        assert ok, err
        row = get_recurring_expense(rid)
        assert float(row["amount_usd"]) == 55.0
        assert int(row["day_of_month"]) == 12

        ok, err = delete_recurring_expense(rid)
        assert ok, err
        assert get_recurring_expense(rid) is None


def _find_template_id(cat_id: int, amount: float) -> int:
    from app.db.sqlite import list_recurring_expenses
    for i in list_recurring_expenses(include_inactive=True):
        if int(i["category_id"]) == cat_id and abs(float(i["amount_usd"]) - amount) < 1e-6:
            return int(i["id"])
    raise AssertionError(f"template for cat={cat_id} amount={amount} not found")


# ═════════════════════════════════════════════════════════════════════════════
# Missing detection
# ═════════════════════════════════════════════════════════════════════════════


class TestGetMissingMonthlyRecurring:
    def test_empty_when_all_paid(self) -> None:
        from app.db.sqlite import (
            add_recurring_expense, add_expense, get_missing_monthly_recurring,
        )
        from datetime import date as _date

        ads = _cat_id("Реклама")
        # active template …
        add_recurring_expense(ads, 25.0, 1, "IG posts")
        month_iso = _date.today().strftime("%Y-%m")
        # … and the expense IS present this month → not in missing.
        add_expense(_date.today().isoformat(), ads, 30.0, "IG bought")
        missing_names = [m["category_name"] for m in get_missing_monthly_recurring(month_iso)]
        assert "Реклама" not in missing_names

    def test_present_when_no_expense_this_month(self) -> None:
        from app.db.sqlite import (
            add_recurring_expense, get_missing_monthly_recurring,
        )
        from datetime import date as _date

        transport = _cat_id("Транспорт")
        add_recurring_expense(transport, 60.0, 10, "монтёр")
        month_iso = _date.today().strftime("%Y-%m")
        missing_names = [m["category_name"] for m in get_missing_monthly_recurring(month_iso)]
        assert "Транспорт" in missing_names

    def test_amount_mismatch_does_not_matter(self) -> None:
        """User paid 470 but template says 500 — still counts as fulfilled."""
        from app.db.sqlite import (
            add_recurring_expense, add_expense, get_missing_monthly_recurring,
        )
        from datetime import date as _date

        internet = _cat_id("Связь/Интернет")
        add_recurring_expense(internet, 25.0, 1, "домашний тариф")
        add_expense(_date.today().isoformat(), internet, 22.0, "тариф снижен")
        missing_names = [m["category_name"] for m in get_missing_monthly_recurring()]
        assert "Связь/Интернет" not in missing_names

    def test_inactive_template_not_shown(self) -> None:
        from app.db.sqlite import (
            add_recurring_expense, set_recurring_expense_active,
            get_missing_monthly_recurring,
        )
        misc = _cat_id("Прочие бизнес")
        add_recurring_expense(misc, 10.0, 15, "будет отключён")
        rid = _find_template_id(misc, 10.0)
        set_recurring_expense_active(rid, False)
        missing_ids = [m["id"] for m in get_missing_monthly_recurring()]
        assert rid not in missing_ids

    def test_expense_in_previous_month_does_not_count(self) -> None:
        """An expense from a month ago must NOT fulfil this month."""
        from app.db.sqlite import (
            add_recurring_expense, add_expense, get_missing_monthly_recurring,
        )
        family = _cat_id("Семья")
        add_recurring_expense(family, 50.0, 3, "детсад")
        # A payment last month.
        add_expense("2026-01-15", family, 50.0, "январь")
        # Ask about a different month.
        missing_names = [m["category_name"]
                         for m in get_missing_monthly_recurring("2026-02")]
        assert "Семья" in missing_names

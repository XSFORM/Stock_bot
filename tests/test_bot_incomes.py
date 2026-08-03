"""Phase 7 (bot idea) — bot-side tests for /income wizard.

Same shape as test_bot_idempotency.py: no real Telegram connection, we
call the async handler directly with a MagicMock CallbackQuery. Focus:

  * Double-tap on «✅ Внести доход» writes exactly ONE row (LRU guard).
  * The confirm-screen rate travels inside the callback_data — a
    mid-flow rate drift never re-prices the row.
  * A service income added through the bot leaves get_profit_report
    absolutely untouched (main defence of trading margin).
  * /income_today never counts trading revenue.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest


# ─── Test infrastructure ─────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def db_path(tmp_path_factory: pytest.TempPathFactory) -> str:
    path = tmp_path_factory.mktemp("bot_inc") / "stock.db"
    os.environ["DB_PATH"] = str(path)
    os.environ.setdefault("BOT_TOKEN", "test")
    os.environ.setdefault("ADMIN_ID", "1")
    import app.db.sqlite as _sql
    _sql.DB_PATH = Path(path)
    return str(path)


@pytest.fixture(scope="module", autouse=True)
def init(db_path: str) -> None:
    from app.db.sqlite import init_db, set_setting
    init_db()
    set_setting("pocket_price_tmt_rate", "19.40")
    # Warehouse for the trading-margin defence test.
    import app.db.sqlite as _sql
    with _sql._connect() as con:
        con.execute("INSERT OR IGNORE INTO warehouses (code, title) VALUES ('BOT_INC_WH', 'W')")
        con.commit()


@pytest.fixture(autouse=True)
def _reset_lru():
    from app.bot import handlers
    handlers._PROCESSED_CALLBACKS.clear()


def _inc_cat_id(name: str) -> int:
    from app.db.sqlite import list_income_categories
    for c in list_income_categories(include_archived=True):
        if c["name"] == name:
            return int(c["id"])
    raise AssertionError(f"seeded income category not found: {name}")


def _fake_callback(chat_id: int, message_id: int, data: str) -> MagicMock:
    from app.config import settings
    cb = MagicMock()
    cb.data = data
    cb.from_user = MagicMock()
    cb.from_user.id = int(settings.admin_id)
    cb.message = MagicMock()
    cb.message.chat = MagicMock()
    cb.message.chat.id = chat_id
    cb.message.message_id = message_id
    cb.message.edit_reply_markup = AsyncMock()
    cb.message.edit_text = AsyncMock()
    cb.message.answer = AsyncMock()
    cb.answer = AsyncMock()
    return cb


def _fake_state(data: dict | None = None) -> MagicMock:
    state = MagicMock()
    _store = dict(data or {})
    state.get_data = AsyncMock(side_effect=lambda: dict(_store))
    async def _clear():
        _store.clear()
    state.clear = AsyncMock(side_effect=_clear)
    async def _update(**kw):
        _store.update(kw)
    state.update_data = AsyncMock(side_effect=_update)
    return state


# ═════════════════════════════════════════════════════════════════════════════
# Idempotency: same-tap defence
# ═════════════════════════════════════════════════════════════════════════════


class TestIncomeApplyIdempotency:
    def test_double_tap_produces_single_income_row(self) -> None:
        """Requirement: user can't accidentally book the same service twice."""
        from app.bot.handlers import cb_income_apply
        from app.db.sqlite import list_incomes

        cid = _inc_cat_id("Ремонт ПК")
        # ia:<cat>:<amt>:<date>:<cur>:<rate>
        data = f"ia:{cid}:250.00:2027-03-05:TMT:19.4000"
        state = _fake_state({"note": "клиент Иван"})

        cb1 = _fake_callback(100, 900, data)
        cb2 = _fake_callback(100, 900, data)  # SAME key

        asyncio.run(cb_income_apply(cb1, state))
        asyncio.run(cb_income_apply(cb2, state))

        rows = [r for r in list_incomes(date_from="2027-03-05", date_to="2027-03-05")
                if r["category_name"] == "Ремонт ПК"]
        assert len(rows) == 1, f"expected 1 row, got {len(rows)}"
        assert float(rows[0]["amount_original"]) == pytest.approx(250.0)
        assert rows[0]["currency"] == "TMT"

        # Second call must have surfaced an alert, not silently written.
        assert cb2.answer.await_count >= 1

    def test_new_message_id_is_not_blocked(self) -> None:
        from app.bot.handlers import cb_income_apply
        from app.db.sqlite import list_incomes

        cid = _inc_cat_id("Ремонт PlayStation")
        data = f"ia:{cid}:100.00:2027-03-06:USD:1.0000"
        state = _fake_state()

        cb1 = _fake_callback(100, 910, data)
        cb2 = _fake_callback(100, 911, data)  # NEW message_id

        asyncio.run(cb_income_apply(cb1, state))
        asyncio.run(cb_income_apply(cb2, state))

        rows = [r for r in list_incomes(date_from="2027-03-06", date_to="2027-03-06")
                if r["category_name"] == "Ремонт PlayStation"]
        assert len(rows) == 2

    def test_bot_income_has_telegram_source_tag(self) -> None:
        from app.bot.handlers import cb_income_apply
        from app.db.sqlite import list_incomes

        cid = _inc_cat_id("Запись игр")
        data = f"ia:{cid}:15.00:2027-03-07:USD:1.0000"
        state = _fake_state({"note": "Fifa для PS4"})
        cb = _fake_callback(100, 920, data)

        asyncio.run(cb_income_apply(cb, state))
        row = next(r for r in list_incomes(date_from="2027-03-07", date_to="2027-03-07")
                   if r["category_name"] == "Запись игр")
        note = row["note"] or ""
        assert "Fifa для PS4" in note
        assert "Telegram-бот" in note


# ═════════════════════════════════════════════════════════════════════════════
# Rate snapshot from callback_data (мид-флоу защита)
# ═════════════════════════════════════════════════════════════════════════════


class TestBotRateSnapshot:
    def test_button_rate_wins_when_setting_drifts_mid_flow(self) -> None:
        """
        User saw «курс 19.40» on the confirm screen. Between screen render
        and their tap, the admin bumps pocket_price_tmt_rate to 22.00.
        The row must still be stored @ 19.40 — that's what the user agreed to.
        """
        from app.bot.handlers import cb_income_apply
        from app.db.sqlite import list_incomes, set_setting

        cid = _inc_cat_id("Прошивка/чиповка")
        # Screen showed rate 19.40.
        data = f"ia:{cid}:388.00:2027-03-08:TMT:19.4000"

        # Admin bumped the rate between screens.
        set_setting("pocket_price_tmt_rate", "22.00")

        state = _fake_state({"note": ""})
        cb = _fake_callback(100, 930, data)
        asyncio.run(cb_income_apply(cb, state))

        row = next(r for r in list_incomes(date_from="2027-03-08", date_to="2027-03-08")
                   if r["category_name"] == "Прошивка/чиповка")
        # rate_used must be the button value, not the live setting.
        assert float(row["rate_used"]) == pytest.approx(19.40)
        assert float(row["amount_usd"]) == pytest.approx(388.0 / 19.40, abs=0.01)

        # Restore for other tests.
        set_setting("pocket_price_tmt_rate", "19.40")


# ═════════════════════════════════════════════════════════════════════════════
# The critical defence: bot income NEVER moves trading margin
# ═════════════════════════════════════════════════════════════════════════════


def _insert_legacy_sale(date_iso: str, unit_price: float, cost_price: float, qty: float = 1) -> None:
    """Same helper shape as test_incomes.py — direct-insert a sale row."""
    import app.db.sqlite as _sql
    from app.db.sqlite import add_client, add_or_get_product_id, receive_stock_by_product_id
    client_name = f"BotSrvClient-{date_iso}-{unit_price}"
    add_client(client_name)
    with _sql._connect() as con:
        cid = int(con.execute("SELECT id FROM clients WHERE name = ?",
                              (client_name,)).fetchone()["id"])
    pid, _ = add_or_get_product_id("BOT", f"p-{date_iso}-{unit_price}", "p", cost_price)
    receive_stock_by_product_id("BOT_INC_WH", pid, qty + 5)
    ts = f"{date_iso} 12:00:00"
    with _sql._connect() as con:
        con.execute(
            "INSERT INTO carts (client_id, warehouse_code, status, created_at)"
            " VALUES (?, 'BOT_INC_WH', 'CLOSED', ?)", (cid, ts),
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


class TestBotIncomeDoesNotTouchTradingMargin:
    def test_service_income_via_bot_does_not_shift_profit_report(self) -> None:
        """
        Same guarantee as test_incomes.TestTradingMarginNotAffected but
        through the ACTUAL bot handler path: cb_income_apply → add_income.
        """
        from app.bot.handlers import cb_income_apply
        from app.db.sqlite import get_profit_report

        _insert_legacy_sale("2027-04-15", unit_price=40.0, cost_price=25.0, qty=1)

        before = get_profit_report("2027-04-01", "2027-04-30")
        before_snap = {
            "revenue":    round(float(before["totals"]["revenue"]), 2),
            "cost":       round(float(before["totals"]["cost"]), 2),
            "profit":     round(float(before["totals"]["profit"]), 2),
            "margin_pct": round(float(before["totals"]["margin_pct"]), 2),
        }

        # Big service income via bot in the same period.
        cid = _inc_cat_id("Прочие услуги")
        data = f"ia:{cid}:1000.00:2027-04-20:USD:1.0000"
        state = _fake_state({"note": ""})
        cb = _fake_callback(100, 950, data)
        asyncio.run(cb_income_apply(cb, state))

        after = get_profit_report("2027-04-01", "2027-04-30")
        after_snap = {
            "revenue":    round(float(after["totals"]["revenue"]), 2),
            "cost":       round(float(after["totals"]["cost"]), 2),
            "profit":     round(float(after["totals"]["profit"]), 2),
            "margin_pct": round(float(after["totals"]["margin_pct"]), 2),
        }
        assert before_snap == after_snap, (
            "bot income shifted profit_report — trading margin got diluted:\n"
            f"  before: {before_snap}\n  after:  {after_snap}"
        )

    def test_income_today_ignores_trading_revenue(self) -> None:
        """/income_today shows only service incomes, never product sales."""
        from app.bot.handlers import cmd_income_today
        from app.db.sqlite import add_income
        from datetime import date as _date

        today_iso = _date.today().isoformat()

        # Trading sale today — must NOT show up in /income_today.
        _insert_legacy_sale(today_iso, unit_price=99.0, cost_price=1.0, qty=1)

        # Add a small service income the same day.
        add_income(today_iso, _inc_cat_id("Ремонт ПК"),
                   currency="USD", amount_original=7.50)

        message = MagicMock()
        message.from_user = MagicMock()
        from app.config import settings
        message.from_user.id = int(settings.admin_id)
        message.answer = AsyncMock()
        asyncio.run(cmd_income_today(message))

        assert message.answer.await_count == 1
        text = message.answer.call_args[0][0]
        # It must not leak the 99$ trading sale.
        assert "99" not in text
        # And must mention the service income figure.
        assert "7.50" in text

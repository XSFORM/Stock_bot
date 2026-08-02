"""Phase 1: guard the Telegram bot against double-tap on the confirm buttons.

Two things must be true after the second identical callback arrives:
  * client_ledger has exactly ONE new row, not two.
  * The user gets a friendly "Уже обработано" alert instead of silent
    double-charging.

We don't need a real Telegram connection — the handler is a plain async
function that takes a CallbackQuery and an FSMContext. AsyncMock stands in
for both, and we assert on real database state after each call.
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
    path = tmp_path_factory.mktemp("bot_idem") / "stock.db"
    os.environ["DB_PATH"] = str(path)
    os.environ.setdefault("BOT_TOKEN", "test")
    os.environ.setdefault("ADMIN_ID", "1")
    import app.db.sqlite as _sql
    _sql.DB_PATH = Path(path)
    return str(path)


@pytest.fixture(scope="module", autouse=True)
def init(db_path: str) -> None:
    from app.db.sqlite import init_db
    init_db()


@pytest.fixture(autouse=True)
def _reset_lru():
    """Clear the LRU between tests so their keys don't spill over."""
    from app.bot import handlers
    handlers._PROCESSED_CALLBACKS.clear()


def _make_client(name: str) -> int:
    from app.db.sqlite import add_client
    import app.db.sqlite as _sql
    ok, err = add_client(name)
    assert ok, err
    with _sql._connect() as con:
        return int(con.execute(
            "SELECT id FROM clients WHERE name = ?", (name,)
        ).fetchone()["id"])


def _ledger_rows(client_id: int) -> list[dict]:
    """Fetch all ledger rows for the client, newest last."""
    import app.db.sqlite as _sql
    with _sql._connect() as con:
        return [dict(r) for r in con.execute(
            "SELECT * FROM client_ledger WHERE client_id = ? ORDER BY id",
            (client_id,),
        ).fetchall()]


def _fake_callback(chat_id: int, message_id: int, data: str) -> MagicMock:
    """
    Build a stand-in CallbackQuery good enough for cb_apply_payment /
    cb_apply_debt. Only the attributes the handler actually reads are set.
    """
    from app.config import settings
    cb = MagicMock()
    cb.data = data
    cb.from_user = MagicMock()
    cb.from_user.id = int(settings.admin_id)
    cb.message = MagicMock()
    cb.message.chat = MagicMock()
    cb.message.chat.id = chat_id
    cb.message.message_id = message_id
    # Async methods must return awaitables.
    cb.message.edit_reply_markup = AsyncMock()
    cb.message.edit_text = AsyncMock()
    cb.message.answer = AsyncMock()
    cb.answer = AsyncMock()
    return cb


def _fake_state(data: dict | None = None) -> MagicMock:
    """FSMContext stub — the handler only calls get_data / clear / update_data."""
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
# LRU key helpers
# ═════════════════════════════════════════════════════════════════════════════


class TestCallbackKeyLRU:
    def test_key_is_tuple_of_chat_message_data(self) -> None:
        from app.bot.handlers import _callback_key
        cb = _fake_callback(100, 200, "ap:5:50.00")
        assert _callback_key(cb) == (100, 200, "ap:5:50.00")

    def test_mark_processed_returns_false_first_true_second(self) -> None:
        from app.bot.handlers import _mark_processed
        k = (1, 2, "ap:5:50.00")
        assert _mark_processed(k) is False
        assert _mark_processed(k) is True
        assert _mark_processed(k) is True  # sticks forever (until LRU eviction)

    def test_different_keys_are_independent(self) -> None:
        from app.bot.handlers import _mark_processed
        assert _mark_processed((1, 2, "ap:5:50")) is False
        assert _mark_processed((1, 3, "ap:5:50")) is False   # different msg
        assert _mark_processed((1, 2, "ap:5:100")) is False  # different data

    def test_lru_bounded(self) -> None:
        """When cache overflows, the oldest key is evicted first."""
        from app.bot import handlers
        handlers._PROCESSED_CALLBACKS.clear()
        for i in range(handlers._PROCESSED_CALLBACKS_MAX + 5):
            handlers._mark_processed((0, i, "x"))
        # Length capped.
        assert len(handlers._PROCESSED_CALLBACKS) == handlers._PROCESSED_CALLBACKS_MAX
        # The earliest keys have been evicted, most recent are still there.
        assert (0, 0, "x") not in handlers._PROCESSED_CALLBACKS
        assert (0, handlers._PROCESSED_CALLBACKS_MAX + 4, "x") in handlers._PROCESSED_CALLBACKS


# ═════════════════════════════════════════════════════════════════════════════
# Payment handler
# ═════════════════════════════════════════════════════════════════════════════


class TestApplyPaymentIdempotency:
    def test_double_tap_produces_single_ledger_row(self) -> None:
        from app.db.sqlite import add_client_debt, get_client_balance
        from app.bot.handlers import cb_apply_payment

        cid = _make_client("Idem Pay 1")
        add_client_debt(cid, 200.0, note="setup")
        assert get_client_balance(cid) == 200.0

        state = _fake_state()
        cb1 = _fake_callback(100, 500, f"ap:{cid}:50.00")
        cb2 = _fake_callback(100, 500, f"ap:{cid}:50.00")  # SAME key on purpose

        asyncio.run(cb_apply_payment(cb1, state))
        asyncio.run(cb_apply_payment(cb2, state))

        # Only one PAYMENT row must have been added on top of the setup row.
        pay_rows = [r for r in _ledger_rows(cid) if r["note"] == "Telegram-бот"]
        assert len(pay_rows) == 1, f"expected 1 bot payment, got {len(pay_rows)}: {pay_rows}"
        assert float(pay_rows[0]["amount"]) == 50.0

        # Balance dropped exactly once: 200 debt − 50 payment = 150.
        assert get_client_balance(cid) == 150.0

        # Second call must have surfaced an alert to the user, not silently
        # exited or (worse) recorded a phantom payment.
        assert cb2.answer.await_count >= 1

    def test_new_message_id_is_not_blocked(self) -> None:
        """A fresh confirmation on a DIFFERENT message must go through."""
        from app.db.sqlite import add_client_debt, get_client_balance
        from app.bot.handlers import cb_apply_payment

        cid = _make_client("Idem Pay 2")
        add_client_debt(cid, 300.0, note="setup")

        state = _fake_state()
        cb1 = _fake_callback(100, 600, f"ap:{cid}:100.00")
        cb2 = _fake_callback(100, 601, f"ap:{cid}:100.00")  # NEW message_id

        asyncio.run(cb_apply_payment(cb1, state))
        asyncio.run(cb_apply_payment(cb2, state))

        assert get_client_balance(cid) == 100.0  # 300 - 100 - 100 = 100

    def test_payment_note_is_telegram_source(self) -> None:
        """Every bot-created payment must be tagged so it's traceable in web."""
        from app.db.sqlite import add_client_debt
        from app.bot.handlers import cb_apply_payment

        cid = _make_client("Idem Pay 3")
        add_client_debt(cid, 500.0, note="setup")

        state = _fake_state()
        cb = _fake_callback(100, 700, f"ap:{cid}:75.00")
        asyncio.run(cb_apply_payment(cb, state))

        rows = [r for r in _ledger_rows(cid) if float(r["amount"]) == 75.0]
        assert len(rows) == 1
        assert rows[0]["note"] == "Telegram-бот"


# ═════════════════════════════════════════════════════════════════════════════
# Debt handler
# ═════════════════════════════════════════════════════════════════════════════


class TestApplyDebtIdempotency:
    def test_double_tap_produces_single_ledger_row(self) -> None:
        from app.db.sqlite import get_client_balance
        from app.bot.handlers import cb_apply_debt

        cid = _make_client("Idem Debt 1")
        # For debts the note lives in FSM state — set it up.
        state = _fake_state({"note": "взял в долг"})

        cb1 = _fake_callback(100, 800, f"ad:{cid}:120.00")
        cb2 = _fake_callback(100, 800, f"ad:{cid}:120.00")  # duplicate

        asyncio.run(cb_apply_debt(cb1, state))
        asyncio.run(cb_apply_debt(cb2, state))

        # Balance must have gone up by 120 exactly once.
        assert get_client_balance(cid) == 120.0

        # Only one debt row.
        rows = [r for r in _ledger_rows(cid) if "взял в долг" in (r["note"] or "")]
        assert len(rows) == 1
        # add_client_debt stores the debt as a NEGATIVE amount in ledger
        # (negative here means "you owe more", not "someone paid you").
        assert float(rows[0]["amount"]) == -120.0

    def test_debt_note_preserves_user_text_and_appends_source_tag(self) -> None:
        from app.bot.handlers import cb_apply_debt
        cid = _make_client("Idem Debt 2")
        state = _fake_state({"note": "предоплата не пришла"})
        cb = _fake_callback(100, 810, f"ad:{cid}:80.00")
        asyncio.run(cb_apply_debt(cb, state))

        rows = _ledger_rows(cid)
        assert len(rows) == 1
        note = rows[0]["note"] or ""
        # User text stays intact and the source is appended, not overwritten.
        assert "предоплата не пришла" in note
        assert "Telegram-бот" in note

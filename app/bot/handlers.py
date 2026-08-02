"""Telegram bot handlers — Phase 6 redesign.

Scope: client debt management from the field.
All previous product/receive/stock/move/cart/backup handlers are gone —
those flows live in the web app now, where they belong.

Flow (typical case: user is standing next to a client with cash):

    /start
      ↓
    [Aylar Tajir market — 350$]  ← tap
      ↓
    Client card with quick-amount buttons
      ↓
    tap [💵 100]
      ↓
    [✅ Да, списать] [❌ Отмена]
      ↓
    "Готово. Долг 250$"

Every payment/debt call reuses the same DB functions the web UI uses
(add_client_adjustment / add_client_debt), so both surfaces stay in
sync automatically. Rows created from the bot are tagged with a
`tg:<user_id>` note prefix so we can trace source in the ledger.
"""
from __future__ import annotations

import html
import logging
from collections import OrderedDict
from typing import Tuple

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from app.bot.keyboards import (
    QUICK_AMOUNTS,
    after_action_kb,
    cancel_kb,
    client_card_kb,
    confirm_debt_kb,
    confirm_payment_kb,
    expense_after_kb,
    expense_categories_kb,
    expense_confirm_kb,
    expense_note_kb,
    fmt_balance,
    main_menu_kb,
    search_results_kb,
)
from app.bot.states import ClientSearch, ExpenseFlow, Payment
from app.config import settings
from app.db.sqlite import (
    add_client_adjustment,
    add_client_debt,
    add_expense,
    find_clients_by_name,
    get_client,
    get_client_balance,
    get_client_history,
    get_expense_tmt_rate,
    get_expenses_summary,
    get_recent_active_clients,
    get_top_debtors,
    get_total_clients_debt,
    list_expense_categories,
)

logger = logging.getLogger(__name__)

router = Router()


# ─── Access control ──────────────────────────────────────────────────────────

def _is_admin_user(user_id: int | None) -> bool:
    try:
        return int(user_id) == int(settings.admin_id)
    except (TypeError, ValueError):
        return False


async def _guard(event: Message | CallbackQuery) -> bool:
    """Return True if the caller is the configured admin. Otherwise reply/answer with a rejection."""
    uid = event.from_user.id if event.from_user else None
    if _is_admin_user(uid):
        return True
    if isinstance(event, CallbackQuery):
        await event.answer("Доступ запрещён.", show_alert=True)
    else:
        await event.answer("Доступ запрещён.")
    return False


# ─── Formatting helpers ──────────────────────────────────────────────────────

def _esc(v) -> str:
    """
    HTML-escape a value coming from the DB before injecting it into a
    message rendered with parse_mode=HTML. Client names, phones, notes
    can contain <, >, & — Telegram silently rejects such messages with
    a 400, which shows up as «button does nothing» to the user.
    """
    return html.escape(str(v or ""), quote=False)


# ─── Idempotency for money-changing callbacks ────────────────────────────────
#
# The user is often out in the field on a flaky mobile connection. A tap on
# "✅ Yes" that stalls will get tapped again a second later — without a guard
# we'd write the same client_ledger row twice and quietly overwrite the debt.
#
# Two-layer protection:
#   1) edit_reply_markup(None) is called *before* the DB write, so after the
#      first tap the buttons vanish physically.
#   2) A tiny LRU set of (chat_id, message_id, callback.data) tuples catches
#      the race window where two callbacks arrived before the edit landed.
#
# The set is bounded (last 500 tuples). More than enough: even 100 taps a day
# would fill it in a week, and we'd never lose a legitimate second attempt to
# false-positive because *the same button on the same message* is what forms
# the key — a new confirmation for the same client generates a new message_id.

_PROCESSED_CALLBACKS: "OrderedDict[Tuple[int, int, str], None]" = OrderedDict()
_PROCESSED_CALLBACKS_MAX = 500


def _callback_key(callback: CallbackQuery) -> Tuple[int, int, str]:
    msg = callback.message
    chat_id = msg.chat.id if msg and msg.chat else 0
    message_id = msg.message_id if msg else 0
    return (int(chat_id), int(message_id), str(callback.data or ""))


def _mark_processed(key: Tuple[int, int, str]) -> bool:
    """
    Register the key as processed. Returns True if it was already there
    (i.e. this is a duplicate tap and the caller should abort).
    """
    if key in _PROCESSED_CALLBACKS:
        return True
    _PROCESSED_CALLBACKS[key] = None
    # Trim from the oldest end.
    while len(_PROCESSED_CALLBACKS) > _PROCESSED_CALLBACKS_MAX:
        _PROCESSED_CALLBACKS.popitem(last=False)
    return False


async def _lock_confirmation(callback: CallbackQuery) -> None:
    """Strip the inline keyboard so the button can't be tapped again."""
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest as exc:
        # Message could be too old to edit or the markup already gone —
        # not fatal, the LRU still protects us from double writes.
        logger.debug("edit_reply_markup(None) failed: %s", exc)


async def _safe_edit(callback: CallbackQuery, text: str, reply_markup=None) -> None:
    """
    Try to edit the message in place. If Telegram refuses (message too
    old, identical text, message-is-not-modified, etc.), fall back to
    sending a fresh message so the user gets a response either way.
    """
    try:
        await callback.message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as exc:
        logger.warning("edit_text failed, sending new: %s", exc)
        await callback.message.answer(text, reply_markup=reply_markup)


def _balance_line(balance: float) -> str:
    """Colour-coded balance for message body."""
    if balance > 0:
        return f"💰 Долг: <b>{balance:.2f} USD</b>"
    if balance < 0:
        return f"🟢 Аванс: <b>{abs(balance):.2f} USD</b>"
    return "⚪ Баланс: <b>0.00 USD</b>"


def _main_menu_text() -> str:
    total = get_total_clients_debt()
    lines = [
        f"👋 Привет!",
        f"",
        f"Мне должны в сумме: <b>{total:.2f} USD</b>",
        f"",
        f"Выбери клиента для оплаты или добавления долга:",
    ]
    return "\n".join(lines)


def _client_card_text(client: dict, balance: float, history: list[dict]) -> str:
    """Compact card: name, balance, phone, last 3 operations."""
    lines = [
        f"👤 <b>{_esc(client['name'])}</b>",
        _balance_line(balance),
    ]
    if client.get("phone"):
        lines.append(f"📞 {_esc(client['phone'])}")
    if history:
        lines.append("")
        lines.append("<b>Последние операции:</b>")
        for ev in history[:3]:
            dt = _esc(str(ev.get("dt") or "")[:10])  # YYYY-MM-DD
            kind = str(ev.get("kind") or "")
            amt = float(ev.get("amount") or 0)
            if kind == "INVOICE":
                lines.append(f"• {dt} — продажа <b>+{amt:.2f}</b> (#{_esc(ev.get('ref',''))})")
            elif kind == "RETURN":
                lines.append(f"• {dt} — возврат <b>−{amt:.2f}</b>")
            elif kind == "LEDGER":
                if amt > 0:
                    lines.append(f"• {dt} — оплата <b>−{amt:.2f}</b>")
                elif amt < 0:
                    lines.append(f"• {dt} — доп. долг <b>+{abs(amt):.2f}</b>")
    return "\n".join(lines)


# ─── /start, /help, /cancel ──────────────────────────────────────────────────

@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext) -> None:
    if not await _guard(message):
        return
    await state.clear()
    recent = get_recent_active_clients(days=7, limit=8)
    await message.answer(_main_menu_text(), reply_markup=main_menu_kb(recent))


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    if not await _guard(message):
        return
    text = (
        "<b>Stock_bot — учёт долгов и расходов</b>\n\n"
        "<b>Клиенты</b>\n"
        "/start — главное меню (недавние клиенты + поиск)\n"
        "/clients — то же, что /start\n"
        "/debt — общий долг клиентов передо мной\n"
        "/top_debtors — топ-10 должников\n\n"
        "<b>Расходы</b>\n"
        "/expense — внести расход (бензин, грузчик, аренда…)\n"
        "/expenses_today — сколько потратил сегодня\n\n"
        "/cancel — отменить текущий шаг\n\n"
        "Товары, приходы, склад и продажи — только в вэб-версии."
    )
    await message.answer(text)


@router.message(Command("clients"))
async def cmd_clients(message: Message, state: FSMContext) -> None:
    # Alias for /start — some users type either.
    await cmd_start(message, state)


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    if not await _guard(message):
        return
    await state.clear()
    await message.answer("Отменено.")


# ─── /debt, /top_debtors — read-only overview ────────────────────────────────

@router.message(Command("debt"))
async def cmd_debt(message: Message) -> None:
    if not await _guard(message):
        return
    total = get_total_clients_debt()
    await message.answer(f"💰 Мне должны в сумме: <b>{total:.2f} USD</b>")


@router.message(Command("top_debtors"))
async def cmd_top_debtors(message: Message) -> None:
    if not await _guard(message):
        return
    debtors = get_top_debtors(limit=10)
    if not debtors:
        await message.answer("Должников нет — все чисты 🎉")
        return
    lines = ["<b>📊 Топ должников:</b>", ""]
    for i, c in enumerate(debtors, 1):
        days = c.get("days_since_last")
        silent = ""
        if days is None:
            silent = " · <i>ни разу не платил</i>"
        elif days > 30:
            silent = f" · <b>🔴 {days} дн. без оплаты</b>"
        elif days > 14:
            silent = f" · 🟡 {days} дн. без оплаты"
        lines.append(f"{i}. <b>{_esc(c['name'])}</b> — {float(c['balance']):.2f}${silent}")
    await message.answer("\n".join(lines))


# ─── Main-menu button handlers ───────────────────────────────────────────────

@router.callback_query(F.data == "menu")
async def cb_menu(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _guard(callback):
        return
    await state.clear()
    recent = get_recent_active_clients(days=7, limit=8)
    await _safe_edit(callback, _main_menu_text(), reply_markup=main_menu_kb(recent))
    await callback.answer()


@router.callback_query(F.data == "top")
async def cb_top(callback: CallbackQuery) -> None:
    if not await _guard(callback):
        return
    debtors = get_top_debtors(limit=10)
    if not debtors:
        await _safe_edit(callback, "Должников нет — все чисты 🎉",
                         reply_markup=main_menu_kb(get_recent_active_clients()))
        await callback.answer()
        return
    lines = ["<b>📊 Топ должников:</b>", ""]
    for i, c in enumerate(debtors, 1):
        days = c.get("days_since_last")
        silent = ""
        if days is None:
            silent = " · <i>ни разу не платил</i>"
        elif days > 30:
            silent = f" · <b>🔴 {days} дн.</b>"
        elif days > 14:
            silent = f" · 🟡 {days} дн."
        lines.append(f"{i}. {_esc(c['name'])} — {float(c['balance']):.2f}${silent}")
    # Button labels are plain text (Telegram doesn't parse HTML there),
    # so no _esc needed for button texts.
    rows = [[InlineKeyboardButton(
                text=f"{c['name']} — {fmt_balance(float(c['balance']))}",
                callback_data=f"c:{c['id']}",
            )] for c in debtors]
    rows.append([InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu")])
    await _safe_edit(callback, "\n".join(lines),
                     reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await callback.answer()


@router.callback_query(F.data == "search")
async def cb_search(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _guard(callback):
        return
    await state.set_state(ClientSearch.waiting_query)
    await callback.message.answer(
        "🔍 Введи часть имени клиента:",
        reply_markup=cancel_kb(),
    )
    await callback.answer()


@router.callback_query(F.data == "cn")
async def cb_cancel_inline(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _guard(callback):
        return
    await state.clear()
    recent = get_recent_active_clients(days=7, limit=8)
    await _safe_edit(callback, _main_menu_text(), reply_markup=main_menu_kb(recent))
    await callback.answer("Отменено.")


# ─── Client card ─────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("c:"))
async def cb_client_card(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _guard(callback):
        return
    await state.clear()  # user picked a client → any half-typed amount is dropped
    try:
        client_id = int(callback.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await callback.answer("Неверные данные.", show_alert=True)
        return
    client = get_client(client_id)
    if not client:
        await callback.answer("Клиент не найден.", show_alert=True)
        return
    try:
        balance = get_client_balance(client_id)
        history = get_client_history(client_id)
        text = _client_card_text(client, balance, history)
        kb = client_card_kb(client_id, client.get("phone"))
    except Exception:
        logger.exception("cb_client_card build failed for client_id=%s", client_id)
        await callback.answer("Ошибка загрузки клиента.", show_alert=True)
        return
    await _safe_edit(callback, text, reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("h:"))
async def cb_full_history(callback: CallbackQuery) -> None:
    """Show up to 20 recent operations for the client."""
    if not await _guard(callback):
        return
    try:
        client_id = int(callback.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await callback.answer("Неверные данные.", show_alert=True)
        return
    client = get_client(client_id)
    if not client:
        await callback.answer("Клиент не найден.", show_alert=True)
        return
    history = get_client_history(client_id)[:20]
    if not history:
        await callback.message.answer("История пуста.")
        await callback.answer()
        return
    lines = [f"<b>📋 История: {_esc(client['name'])}</b>", ""]
    for ev in history:
        dt = _esc(str(ev.get("dt") or "")[:10])
        kind = str(ev.get("kind") or "")
        amt = float(ev.get("amount") or 0)
        bal = ev.get("balance_after")
        bal_str = f" → {float(bal):.2f}$" if bal is not None else ""
        if kind == "INVOICE":
            lines.append(f"{dt} 📄 продажа +{amt:.2f} (#{_esc(ev.get('ref',''))}){bal_str}")
        elif kind == "RETURN":
            lines.append(f"{dt} ↩ возврат −{amt:.2f}{bal_str}")
        elif kind == "LEDGER":
            if amt > 0:
                note = ev.get("note") or ""
                note_s = f" ({_esc(note)})" if note else ""
                lines.append(f"{dt} 💵 оплата −{amt:.2f}{note_s}{bal_str}")
            elif amt < 0:
                lines.append(f"{dt} ➕ доп. долг +{abs(amt):.2f}{bal_str}")
    await callback.message.answer("\n".join(lines))
    await callback.answer()


# ─── Quick payment (fixed amount) ────────────────────────────────────────────

@router.callback_query(F.data.startswith("p:"))
async def cb_pay_quick(callback: CallbackQuery) -> None:
    """User tapped one of the [💵 50/100/200/500] buttons."""
    if not await _guard(callback):
        return
    try:
        _, cid, amt = callback.data.split(":")
        client_id = int(cid)
        amount = float(amt)
    except (ValueError, IndexError):
        await callback.answer("Неверные данные.", show_alert=True)
        return
    client = get_client(client_id)
    if not client:
        await callback.answer("Клиент не найден.", show_alert=True)
        return
    balance = get_client_balance(client_id)
    new_balance = round(balance - amount, 2)
    text = (
        f"Списать <b>{amount:.2f} USD</b> с <b>{_esc(client['name'])}</b>?\n\n"
        f"Текущий долг: {balance:.2f} USD\n"
        f"После оплаты: <b>{new_balance:.2f} USD</b>"
    )
    await _safe_edit(callback, text, reply_markup=confirm_payment_kb(client_id, amount))
    await callback.answer()


# ─── Free-text payment / debt entry ──────────────────────────────────────────

@router.callback_query(F.data.startswith("o:"))
async def cb_other_amount(callback: CallbackQuery, state: FSMContext) -> None:
    """User tapped [💰 Другая оплата] — ask for amount via free text."""
    if not await _guard(callback):
        return
    try:
        client_id = int(callback.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await callback.answer("Неверные данные.", show_alert=True)
        return
    client = get_client(client_id)
    if not client:
        await callback.answer("Клиент не найден.", show_alert=True)
        return
    await state.set_state(Payment.waiting_amount)
    await state.update_data(client_id=client_id, kind="payment")
    await callback.message.answer(
        f"💰 Введи сумму оплаты для <b>{_esc(client['name'])}</b>:",
        reply_markup=cancel_kb(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("d:"))
async def cb_add_debt(callback: CallbackQuery, state: FSMContext) -> None:
    """User tapped [➕ Добавить долг] — ask for amount via free text."""
    if not await _guard(callback):
        return
    try:
        client_id = int(callback.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await callback.answer("Неверные данные.", show_alert=True)
        return
    client = get_client(client_id)
    if not client:
        await callback.answer("Клиент не найден.", show_alert=True)
        return
    await state.set_state(Payment.waiting_amount)
    await state.update_data(client_id=client_id, kind="debt")
    await callback.message.answer(
        f"➕ Введи сумму долга для <b>{_esc(client['name'])}</b>:",
        reply_markup=cancel_kb(),
    )
    await callback.answer()


@router.message(Payment.waiting_amount)
async def on_amount_typed(message: Message, state: FSMContext) -> None:
    if not await _guard(message):
        return
    raw = (message.text or "").strip().replace(",", ".")
    try:
        amount = float(raw)
    except ValueError:
        await message.answer("Не понял сумму. Введи число, например 100 или 75.50",
                             reply_markup=cancel_kb())
        return
    if amount <= 0:
        await message.answer("Сумма должна быть больше 0.", reply_markup=cancel_kb())
        return

    data = await state.get_data()
    client_id = int(data.get("client_id", 0))
    kind = str(data.get("kind", "payment"))

    client = get_client(client_id)
    if not client:
        await state.clear()
        await message.answer("Клиент не найден.")
        return

    if kind == "debt":
        # Debt requires a reason note (matches web form). Move to the
        # note step and keep amount + client_id in state.
        await state.set_state(Payment.waiting_note)
        await state.update_data(amount=amount)
        await message.answer(
            f"📝 Причина долга <b>{amount:.2f} USD</b> для "
            f"<b>{_esc(client['name'])}</b>?\n\n"
            f"Например: «взял в долг товар», «предоплата не пришла».",
            reply_markup=cancel_kb(),
        )
        return

    # Payment: no note required — go straight to confirmation.
    await state.clear()
    balance = get_client_balance(client_id)
    new_balance = round(balance - amount, 2)
    text = (
        f"Списать <b>{amount:.2f} USD</b> с <b>{_esc(client['name'])}</b>?\n\n"
        f"Текущий долг: {balance:.2f} USD\n"
        f"После оплаты: <b>{new_balance:.2f} USD</b>"
    )
    await message.answer(text, reply_markup=confirm_payment_kb(client_id, amount))


@router.message(Payment.waiting_note)
async def on_debt_note_typed(message: Message, state: FSMContext) -> None:
    """Second step of the debt flow: user types the reason."""
    if not await _guard(message):
        return
    note = (message.text or "").strip()
    if not note:
        await message.answer("Заметка обязательна. Опиши причину коротко.",
                             reply_markup=cancel_kb())
        return
    if len(note) > 200:
        note = note[:200]

    data = await state.get_data()
    client_id = int(data.get("client_id", 0))
    amount = float(data.get("amount", 0))
    # Keep note in state so cb_apply_debt can read it after confirm.
    await state.update_data(note=note)

    client = get_client(client_id)
    if not client:
        await state.clear()
        await message.answer("Клиент не найден.")
        return
    balance = get_client_balance(client_id)
    new_balance = round(balance + amount, 2)
    text = (
        f"Добавить долг <b>{amount:.2f} USD</b> клиенту "
        f"<b>{_esc(client['name'])}</b>?\n\n"
        f"Заметка: <i>{_esc(note)}</i>\n"
        f"Текущий долг: {balance:.2f} USD\n"
        f"После: <b>{new_balance:.2f} USD</b>"
    )
    await message.answer(text, reply_markup=confirm_debt_kb(client_id, amount))


# ─── Confirm & apply ─────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("ap:"))
async def cb_apply_payment(callback: CallbackQuery, state: FSMContext) -> None:
    """
    Apply a payment. Note is intentionally minimal ("Telegram-бот") so it's
    obvious in the client history where the entry came from — the web form
    doesn't set a note automatically, so this label is the only marker.

    Protected against double-tap on flaky mobile connections:
      • The confirm button is removed *before* the DB write.
      • A LRU set of processed (chat, message, data) tuples catches the race
        where two callbacks arrive within a few ms of each other.
    """
    if not await _guard(callback):
        return
    try:
        _, cid, amt = callback.data.split(":")
        client_id = int(cid)
        amount = float(amt)
    except (ValueError, IndexError):
        await callback.answer("Неверные данные.", show_alert=True)
        return

    # Idempotency guard #1: LRU set catches the race window.
    if _mark_processed(_callback_key(callback)):
        await callback.answer("Уже обработано", show_alert=True)
        return
    # Idempotency guard #2: strip the confirm keyboard so no more taps land.
    await _lock_confirmation(callback)

    await state.clear()  # in case user came here from a half-typed flow
    ok, err = add_client_adjustment(client_id, amount, note="Telegram-бот")
    if not ok:
        logger.error("bot payment failed: client=%s amt=%s err=%s", client_id, amount, err)
        await callback.answer(f"Ошибка: {err}", show_alert=True)
        return
    client = get_client(client_id)
    balance = get_client_balance(client_id)
    text = (
        f"✅ Оплата принята.\n\n"
        f"<b>{_esc(client['name'])}</b>\n"
        f"Списано: {amount:.2f} USD\n"
        f"{_balance_line(balance)}"
    )
    await _safe_edit(callback, text, reply_markup=after_action_kb(client_id))
    await callback.answer("Готово!")


@router.callback_query(F.data.startswith("ad:"))
async def cb_apply_debt(callback: CallbackQuery, state: FSMContext) -> None:
    """
    Apply a debt entry. The reason note lives in FSM state (set by
    on_debt_note_typed just before we drew the confirmation keyboard).

    Same double-tap protection as cb_apply_payment.
    """
    if not await _guard(callback):
        return
    try:
        _, cid, amt = callback.data.split(":")
        client_id = int(cid)
        amount = float(amt)
    except (ValueError, IndexError):
        await callback.answer("Неверные данные.", show_alert=True)
        return

    # Idempotency guard (LRU + strip keyboard) — see cb_apply_payment for details.
    if _mark_processed(_callback_key(callback)):
        await callback.answer("Уже обработано", show_alert=True)
        return
    await _lock_confirmation(callback)

    data = await state.get_data()
    user_note = str(data.get("note") or "").strip()
    await state.clear()
    if not user_note:
        # Shouldn't happen in a normal flow, but guard against a stale
        # confirmation button being tapped from an old chat message.
        await callback.answer(
            "Заметка потеряна. Открой /start и попробуй ещё раз.",
            show_alert=True,
        )
        return
    # Append the source label to the user's note (don't overwrite —
    # the reason the user typed is what matters most in the ledger view).
    note = f"{user_note} · Telegram-бот"
    ok, err = add_client_debt(client_id, amount, note=note)
    if not ok:
        logger.error("bot debt failed: client=%s amt=%s err=%s", client_id, amount, err)
        await callback.answer(f"Ошибка: {err}", show_alert=True)
        return
    client = get_client(client_id)
    balance = get_client_balance(client_id)
    text = (
        f"✅ Долг добавлен.\n\n"
        f"<b>{_esc(client['name'])}</b>\n"
        f"Добавлено: {amount:.2f} USD\n"
        f"Заметка: <i>{_esc(note)}</i>\n"
        f"{_balance_line(balance)}"
    )
    await _safe_edit(callback, text, reply_markup=after_action_kb(client_id))
    await callback.answer("Готово!")


# ─── Client search ───────────────────────────────────────────────────────────

@router.message(ClientSearch.waiting_query)
async def on_search_query(message: Message, state: FSMContext) -> None:
    if not await _guard(message):
        return
    query = (message.text or "").strip()
    if len(query) < 2:
        await message.answer("Введи хотя бы 2 символа.", reply_markup=cancel_kb())
        return
    await state.clear()
    results = find_clients_by_name(query, limit=10)
    if not results:
        await message.answer(f"По запросу «{query}» никого не нашёл.",
                             reply_markup=main_menu_kb(get_recent_active_clients()))
        return
    await message.answer(
        f"Нашёл {len(results)}:",
        reply_markup=search_results_kb(results),
    )


# ═════════════════════════════════════════════════════════════════════════════
# Phase 2 — /expense wizard
# ═════════════════════════════════════════════════════════════════════════════
#
# Small expenses (fuel, porter, taxi) happen in the field. If they can only be
# recorded from a desktop later, they get forgotten and the finance report
# ends up lying about profit. This wizard makes it a 30-second, 4-tap job on
# the phone.
#
# Callback prefixes (see keyboards.py for the master list):
#   xm                     open category picker
#   xc:<id>                category chosen → ask amount
#   xn:<id>:<amt>          skip note → confirm screen
#   xd:<t|y>:<id>:<amt>    toggle date on confirm screen
#   xa:<id>:<amt>:<date>   final apply (idempotency-protected)


def _fmt_date_ru(date_iso: str) -> str:
    """'2026-08-02' → '02.08.2026', gracefully returns the input on failure."""
    try:
        y, m, d = date_iso.split("-")
        return f"{d}.{m}.{y}"
    except (ValueError, AttributeError):
        return date_iso


def _get_category(cat_id: int) -> dict | None:
    for c in list_expense_categories(include_archived=True):
        if int(c["id"]) == int(cat_id):
            return c
    return None


def _expense_confirm_text(
    cat: dict, amount: float, date_iso: str, note: str,
    currency: str, rate: float, rate_is_fallback: bool = False,
) -> str:
    """
    Show BOTH values on the confirm screen so the operator can't get
    confused mid-flow (100 TMT and 100 USD look identical typed in).
    """
    kind_badge = "🛒 Личное" if cat.get("kind") == "personal" else "📦 Бизнес"
    if currency == "TMT" and rate > 0:
        usd = amount / rate
        amount_line = (
            f"Сумма: <b>{amount:.2f} TMT</b> ≈ <b>{usd:.2f} $</b>"
            f"  <i>(курс {rate:.2f})</i>"
        )
    else:
        amount_line = f"Сумма: <b>{amount:.2f} $</b>"
    parts = [
        f"💸 <b>Проверь расход:</b>",
        f"",
        f"Категория: <b>{_esc(cat['name'])}</b> ({kind_badge})",
        amount_line,
        f"Дата: <b>{_esc(_fmt_date_ru(date_iso))}</b>",
    ]
    if note:
        parts.append(f"Заметка: <i>{_esc(note)}</i>")
    if currency == "TMT" and rate_is_fallback:
        parts.append("")
        parts.append(f"⚠️ Курс не настроен, используется запасной {rate:.2f}.")
    return "\n".join(parts)


# ─── Entry points ────────────────────────────────────────────────────────────

@router.message(Command("expense"))
async def cmd_expense(message: Message, state: FSMContext) -> None:
    if not await _guard(message):
        return
    await state.clear()
    cats = list_expense_categories()  # non-archived only
    if not cats:
        await message.answer("Категории расходов не настроены. Открой /help.")
        return
    await message.answer("Выбери категорию расхода:", reply_markup=expense_categories_kb(cats))


@router.callback_query(F.data == "xm")
async def cb_expense_menu(callback: CallbackQuery, state: FSMContext) -> None:
    """Same as /expense but from a button (used from main menu and after add)."""
    if not await _guard(callback):
        return
    await state.clear()
    cats = list_expense_categories()
    if not cats:
        await callback.answer("Категории расходов не настроены.", show_alert=True)
        return
    await _safe_edit(callback, "Выбери категорию расхода:", reply_markup=expense_categories_kb(cats))
    await callback.answer()


# ─── Step 1: category chosen → ask amount ────────────────────────────────────

@router.callback_query(F.data.startswith("xc:"))
async def cb_expense_category(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _guard(callback):
        return
    try:
        cat_id = int(callback.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await callback.answer("Неверные данные.", show_alert=True)
        return
    cat = _get_category(cat_id)
    if not cat:
        await callback.answer("Категория не найдена.", show_alert=True)
        return
    await state.set_state(ExpenseFlow.waiting_amount)
    await state.update_data(cat_id=cat_id)
    kind_badge = "🛒" if cat.get("kind") == "personal" else "📦"
    # Ввод считается манатами по умолчанию — валюту всегда можно
    # переключить на USD одним тапом на экране подтверждения.
    await callback.message.answer(
        f"{kind_badge} <b>{_esc(cat['name'])}</b>\n\n"
        f"💰 Введи сумму (например 1080):\n"
        f"<i>По умолчанию TMT (манаты). Переключить на USD можно на следующем экране.</i>",
        reply_markup=cancel_kb(),
    )
    await callback.answer()


# ─── Step 2: amount typed → ask note (with Пропустить button) ───────────────

@router.message(ExpenseFlow.waiting_amount)
async def on_expense_amount(message: Message, state: FSMContext) -> None:
    if not await _guard(message):
        return
    raw = (message.text or "").strip().replace(",", ".")
    try:
        amount = float(raw)
    except ValueError:
        await message.answer("Не понял сумму. Введи число, например 12.50",
                             reply_markup=cancel_kb())
        return
    if amount <= 0:
        await message.answer("Сумма должна быть больше 0.", reply_markup=cancel_kb())
        return

    data = await state.get_data()
    cat_id = int(data.get("cat_id", 0))
    cat = _get_category(cat_id)
    if not cat:
        await state.clear()
        await message.answer("Категория не найдена. Начни заново: /expense")
        return

    await state.set_state(ExpenseFlow.waiting_note)
    await state.update_data(amount=amount)
    await message.answer(
        f"📝 Заметка (например «АЗС Мерседес», «грузчик рынок»)\n\n"
        f"Или нажми «Пропустить» — заметка необязательна.",
        reply_markup=expense_note_kb(cat_id, amount),
    )


# ─── Step 3a: user typed a note → confirm screen ────────────────────────────

@router.message(ExpenseFlow.waiting_note)
async def on_expense_note(message: Message, state: FSMContext) -> None:
    if not await _guard(message):
        return
    note = (message.text or "").strip()
    if len(note) > 200:
        note = note[:200]

    data = await state.get_data()
    cat_id = int(data.get("cat_id", 0))
    amount = float(data.get("amount", 0))
    cat = _get_category(cat_id)
    if not cat:
        await state.clear()
        await message.answer("Категория не найдена. Начни заново: /expense")
        return

    from datetime import date as _date
    date_iso = _date.today().isoformat()
    rate, is_fallback = get_expense_tmt_rate()
    # Default currency is TMT — that's what most local expenses are in.
    await state.update_data(note=note, date=date_iso, currency="TMT")

    await message.answer(
        _expense_confirm_text(cat, amount, date_iso, note, "TMT", rate, is_fallback),
        reply_markup=expense_confirm_kb(cat_id, amount, date_iso,
                                        is_today=True, currency="TMT", rate=rate),
    )


# ─── Step 3b: user tapped «Пропустить» → confirm screen with empty note ─────

@router.callback_query(F.data.startswith("xn:"))
async def cb_expense_skip_note(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _guard(callback):
        return
    try:
        _, cid, amt = callback.data.split(":")
        cat_id = int(cid)
        amount = float(amt)
    except (ValueError, IndexError):
        await callback.answer("Неверные данные.", show_alert=True)
        return
    cat = _get_category(cat_id)
    if not cat:
        await callback.answer("Категория не найдена.", show_alert=True)
        return

    from datetime import date as _date
    date_iso = _date.today().isoformat()
    rate, is_fallback = get_expense_tmt_rate()
    await state.update_data(cat_id=cat_id, amount=amount, note="",
                            date=date_iso, currency="TMT")

    await _safe_edit(
        callback,
        _expense_confirm_text(cat, amount, date_iso, "", "TMT", rate, is_fallback),
        reply_markup=expense_confirm_kb(cat_id, amount, date_iso,
                                        is_today=True, currency="TMT", rate=rate),
    )
    await callback.answer()


# ─── Step 3.5: toggle date today ↔ yesterday on the confirm screen ──────────

@router.callback_query(F.data.startswith("xd:"))
async def cb_expense_toggle_date(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _guard(callback):
        return
    try:
        _, mode, cid, amt = callback.data.split(":")
        cat_id = int(cid)
        amount = float(amt)
    except (ValueError, IndexError):
        await callback.answer("Неверные данные.", show_alert=True)
        return
    cat = _get_category(cat_id)
    if not cat:
        await callback.answer("Категория не найдена.", show_alert=True)
        return

    from datetime import date as _date, timedelta as _td
    is_today = mode == "t"
    d = _date.today() if is_today else _date.today() - _td(days=1)
    date_iso = d.isoformat()

    # note + currency were stored earlier; preserve them.
    data = await state.get_data()
    note = str(data.get("note") or "")
    currency = str(data.get("currency") or "TMT")
    rate, is_fallback = get_expense_tmt_rate()
    await state.update_data(date=date_iso)

    await _safe_edit(
        callback,
        _expense_confirm_text(cat, amount, date_iso, note, currency, rate, is_fallback),
        reply_markup=expense_confirm_kb(cat_id, amount, date_iso,
                                        is_today=is_today, currency=currency, rate=rate),
    )
    await callback.answer("Дата обновлена")


# ─── Step 3.6: toggle currency TMT ↔ USD on the confirm screen ──────────────

@router.callback_query(F.data.startswith("xt:"))
async def cb_expense_toggle_currency(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _guard(callback):
        return
    try:
        _, cid, amt, date_iso, cur = callback.data.split(":")
        cat_id = int(cid)
        amount = float(amt)
    except (ValueError, IndexError):
        await callback.answer("Неверные данные.", show_alert=True)
        return
    cat = _get_category(cat_id)
    if not cat:
        await callback.answer("Категория не найдена.", show_alert=True)
        return

    # Flip currency; TMT re-fetches the current rate (USD side never needs it).
    new_currency = "USD" if cur == "TMT" else "TMT"
    rate, is_fallback = get_expense_tmt_rate()

    data = await state.get_data()
    note = str(data.get("note") or "")
    await state.update_data(currency=new_currency)

    from datetime import date as _date
    is_today = date_iso == _date.today().isoformat()

    await _safe_edit(
        callback,
        _expense_confirm_text(cat, amount, date_iso, note, new_currency, rate, is_fallback),
        reply_markup=expense_confirm_kb(cat_id, amount, date_iso,
                                        is_today=is_today, currency=new_currency, rate=rate),
    )
    await callback.answer(f"→ {new_currency}")


# ─── Step 4: apply — with the same double-tap protection as payments ───────

@router.callback_query(F.data.startswith("xa:"))
async def cb_expense_apply(callback: CallbackQuery, state: FSMContext) -> None:
    """
    Final apply. Same idempotency guard as cb_apply_payment:
      1) LRU-set on (chat, message, data) key
      2) Strip keyboard before the DB write.

    Currency and rate come from the button's own callback_data — this way
    a rate change between the confirm screen and the tap can't retroactively
    re-price the row (the user committed to what they saw).
    """
    if not await _guard(callback):
        return
    try:
        _, cid, amt, date_iso, cur, rate_str = callback.data.split(":")
        cat_id = int(cid)
        amount = float(amt)
        rate = float(rate_str)
    except (ValueError, IndexError):
        await callback.answer("Неверные данные.", show_alert=True)
        return

    if _mark_processed(_callback_key(callback)):
        await callback.answer("Уже обработано", show_alert=True)
        return
    await _lock_confirmation(callback)

    data = await state.get_data()
    user_note = str(data.get("note") or "").strip()
    await state.clear()

    # Same source-tag pattern as bot payments/debts.
    note = f"{user_note} · Telegram-бот" if user_note else "Telegram-бот"

    # NB: we override the current pocket_price_tmt_rate for this one call
    # by temporarily writing the confirmed rate — otherwise add_expense
    # would re-fetch. Simpler: don't try to inject; just pass through the
    # normal path and rely on the fact that the settings value hasn't
    # meaningfully changed between screen render and tap (~seconds).
    # If it DID change, we log a warning below.
    from app.db.sqlite import get_expense_tmt_rate as _cur_rate
    live_rate, _ = _cur_rate()
    if cur == "TMT" and abs(live_rate - rate) > 0.001:
        logger.warning("expense TMT rate drifted %.4f → %.4f mid-flow; using screen value",
                       rate, live_rate)
        # Preserve the on-screen contract by temporarily setting the rate,
        # then restore it. Small race window but the user's expectation wins.
        from app.db.sqlite import set_setting, get_setting
        saved = get_setting("pocket_price_tmt_rate", "")
        set_setting("pocket_price_tmt_rate", f"{rate}")
        try:
            ok, err = add_expense(date_iso, cat_id, note=note,
                                  currency="TMT", amount_original=amount)
        finally:
            set_setting("pocket_price_tmt_rate", saved)
    else:
        ok, err = add_expense(date_iso, cat_id, note=note,
                              currency=cur, amount_original=amount)
    if not ok:
        logger.error("bot expense add failed: cat=%s amt=%s date=%s err=%s",
                     cat_id, amount, date_iso, err)
        await callback.answer(f"Ошибка: {err}", show_alert=True)
        return

    cat = _get_category(cat_id)
    kind_badge = "🛒 Личное" if cat and cat.get("kind") == "personal" else "📦 Бизнес"
    if cur == "TMT" and rate > 0:
        usd = amount / rate
        amount_line = f"Сумма: <b>{amount:.2f} TMT</b> ≈ <b>{usd:.2f} $</b>  <i>(курс {rate:.2f})</i>"
    else:
        amount_line = f"Сумма: <b>{amount:.2f} $</b>"
    text = (
        f"✅ Расход добавлен.\n\n"
        f"<b>{_esc(cat['name']) if cat else ''}</b> ({kind_badge})\n"
        f"{amount_line}\n"
        f"Дата: {_esc(_fmt_date_ru(date_iso))}\n"
    )
    if user_note:
        text += f"Заметка: <i>{_esc(user_note)}</i>\n"

    # Quick self-check: today's total after this row.
    from datetime import date as _date
    today_iso = _date.today().isoformat()
    summary = get_expenses_summary(today_iso, today_iso)
    text += f"\n💰 Итог за сегодня: <b>{summary['totals']['all']:.2f} $</b>"
    if summary['totals'].get('tmt_original', 0) > 0:
        text += f"  <i>(из них {summary['totals']['tmt_original']:.2f} TMT)</i>"

    await _safe_edit(callback, text, reply_markup=expense_after_kb())
    await callback.answer("Готово!")


# ─── /expenses_today — quick self-check summary ─────────────────────────────

@router.message(Command("expenses_today"))
async def cmd_expenses_today(message: Message) -> None:
    if not await _guard(message):
        return
    from datetime import date as _date
    today_iso = _date.today().isoformat()
    summary = get_expenses_summary(today_iso, today_iso)
    totals = summary["totals"]
    by_cat = summary["by_category"]

    if not by_cat:
        await message.answer(
            f"💸 Расходов за сегодня ({_esc(_fmt_date_ru(today_iso))}) нет."
        )
        return

    # Per-row breakdown with original currency (from list_expenses, not the
    # aggregated summary — the aggregate loses TMT/USD distinction).
    from app.db.sqlite import list_expenses
    rows = list_expenses(date_from=today_iso, date_to=today_iso)

    lines = [f"💸 <b>Расходы за сегодня ({_esc(_fmt_date_ru(today_iso))})</b>", ""]
    for r in rows:
        badge = "🛒" if r["category_kind"] == "personal" else "📦"
        if r["currency"] == "TMT" and r.get("amount_original"):
            amt = f"{float(r['amount_original']):.2f} TMT (≈ {float(r['amount_usd']):.2f} $)"
        else:
            amt = f"{float(r['amount_usd']):.2f} $"
        lines.append(f"{badge} {_esc(r['category_name'])} — <b>{amt}</b>")
    lines.append("")
    lines.append(f"📦 Бизнес: <b>{totals['business']:.2f} $</b>")
    lines.append(f"🛒 Личное: <b>{totals['personal']:.2f} $</b>")
    lines.append(f"💰 Всего: <b>{totals['all']:.2f} $</b>")
    if totals.get("tmt_original", 0) > 0:
        lines.append(f"   <i>из них в манатах: {totals['tmt_original']:.2f} TMT</i>")

    await message.answer("\n".join(lines))


# ─── Fallback: raw text outside FSM = quick search ───────────────────────────

@router.message(F.text & ~F.text.startswith("/"))
async def on_free_text(message: Message, state: FSMContext) -> None:
    """
    User types "Aylar" without pressing /start first — treat as search.
    Only fires when no FSM state is active (aiogram routes FSM states first).
    """
    if not await _guard(message):
        return
    query = (message.text or "").strip()
    if len(query) < 2:
        return
    results = find_clients_by_name(query, limit=10)
    if not results:
        await message.answer(
            f"По запросу «{query}» никого не нашёл. Открой /start для меню.")
        return
    await message.answer(
        f"Нашёл {len(results)}:",
        reply_markup=search_results_kb(results),
    )

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

import logging

from aiogram import F, Router
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
    fmt_balance,
    main_menu_kb,
    search_results_kb,
)
from app.bot.states import ClientSearch, Payment
from app.config import settings
from app.db.sqlite import (
    add_client_adjustment,
    add_client_debt,
    find_clients_by_name,
    get_client,
    get_client_balance,
    get_client_history,
    get_recent_active_clients,
    get_top_debtors,
    get_total_clients_debt,
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
        f"👤 <b>{client['name']}</b>",
        _balance_line(balance),
    ]
    if client.get("phone"):
        lines.append(f"📞 {client['phone']}")
    if history:
        lines.append("")
        lines.append("<b>Последние операции:</b>")
        for ev in history[:3]:
            dt = str(ev.get("dt") or "")[:10]  # YYYY-MM-DD
            kind = str(ev.get("kind") or "")
            amt = float(ev.get("amount") or 0)
            if kind == "INVOICE":
                lines.append(f"• {dt} — продажа <b>+{amt:.2f}</b> (#{ev.get('ref','')})")
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
        "<b>Stock_bot — учёт долгов клиентов</b>\n\n"
        "/start — главное меню (недавние клиенты + поиск)\n"
        "/clients — то же, что /start\n"
        "/debt — общий долг клиентов передо мной\n"
        "/top_debtors — топ-10 должников\n"
        "/cancel — отменить текущий шаг\n\n"
        "Товары, приходы, склад и продажи — теперь только в вэб-версии.\n"
        "Здесь только быстрая работа с долгом: тап по клиенту → сумма → подтвердить."
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
        lines.append(f"{i}. <b>{c['name']}</b> — {float(c['balance']):.2f}${silent}")
    await message.answer("\n".join(lines))


# ─── Main-menu button handlers ───────────────────────────────────────────────

@router.callback_query(F.data == "menu")
async def cb_menu(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _guard(callback):
        return
    await state.clear()
    recent = get_recent_active_clients(days=7, limit=8)
    await callback.message.edit_text(
        _main_menu_text(), reply_markup=main_menu_kb(recent),
    )
    await callback.answer()


@router.callback_query(F.data == "top")
async def cb_top(callback: CallbackQuery) -> None:
    if not await _guard(callback):
        return
    debtors = get_top_debtors(limit=10)
    if not debtors:
        await callback.message.edit_text("Должников нет — все чисты 🎉",
                                         reply_markup=main_menu_kb(
                                             get_recent_active_clients()))
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
        lines.append(f"{i}. {c['name']} — {float(c['balance']):.2f}${silent}")
    # Show as clickable buttons.
    rows = [[InlineKeyboardButton(
                text=f"{c['name']} — {fmt_balance(float(c['balance']))}",
                callback_data=f"c:{c['id']}",
            )] for c in debtors]
    rows.append([InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu")])
    await callback.message.edit_text("\n".join(lines),
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
    await callback.message.edit_text(
        _main_menu_text(), reply_markup=main_menu_kb(recent),
    )
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
    balance = get_client_balance(client_id)
    history = get_client_history(client_id)
    await callback.message.edit_text(
        _client_card_text(client, balance, history),
        reply_markup=client_card_kb(client_id, client.get("phone")),
    )
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
    lines = [f"<b>📋 История: {client['name']}</b>", ""]
    for ev in history:
        dt = str(ev.get("dt") or "")[:10]
        kind = str(ev.get("kind") or "")
        amt = float(ev.get("amount") or 0)
        bal = ev.get("balance_after")
        bal_str = f" → {float(bal):.2f}$" if bal is not None else ""
        if kind == "INVOICE":
            lines.append(f"{dt} 📄 продажа +{amt:.2f} (#{ev.get('ref','')}){bal_str}")
        elif kind == "RETURN":
            lines.append(f"{dt} ↩ возврат −{amt:.2f}{bal_str}")
        elif kind == "LEDGER":
            if amt > 0:
                note = ev.get("note") or ""
                note_s = f" ({note})" if note else ""
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
        f"Списать <b>{amount:.2f} USD</b> с <b>{client['name']}</b>?\n\n"
        f"Текущий долг: {balance:.2f} USD\n"
        f"После оплаты: <b>{new_balance:.2f} USD</b>"
    )
    await callback.message.edit_text(text, reply_markup=confirm_payment_kb(client_id, amount))
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
        f"💰 Введи сумму оплаты для <b>{client['name']}</b>:",
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
        f"➕ Введи сумму долга для <b>{client['name']}</b>:",
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
            f"<b>{client['name']}</b>?\n\n"
            f"Например: «взял в долг товар», «предоплата не пришла».",
            reply_markup=cancel_kb(),
        )
        return

    # Payment: no note required — go straight to confirmation.
    await state.clear()
    balance = get_client_balance(client_id)
    new_balance = round(balance - amount, 2)
    text = (
        f"Списать <b>{amount:.2f} USD</b> с <b>{client['name']}</b>?\n\n"
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
        f"<b>{client['name']}</b>?\n\n"
        f"Заметка: <i>{note}</i>\n"
        f"Текущий долг: {balance:.2f} USD\n"
        f"После: <b>{new_balance:.2f} USD</b>"
    )
    await message.answer(text, reply_markup=confirm_debt_kb(client_id, amount))


# ─── Confirm & apply ─────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("ap:"))
async def cb_apply_payment(callback: CallbackQuery, state: FSMContext) -> None:
    """Apply a payment. Note is intentionally empty — same as web behaviour."""
    if not await _guard(callback):
        return
    try:
        _, cid, amt = callback.data.split(":")
        client_id = int(cid)
        amount = float(amt)
    except (ValueError, IndexError):
        await callback.answer("Неверные данные.", show_alert=True)
        return
    await state.clear()  # in case user came here from a half-typed flow
    ok, err = add_client_adjustment(client_id, amount, note="")
    if not ok:
        logger.error("bot payment failed: client=%s amt=%s err=%s", client_id, amount, err)
        await callback.answer(f"Ошибка: {err}", show_alert=True)
        return
    client = get_client(client_id)
    balance = get_client_balance(client_id)
    text = (
        f"✅ Оплата принята.\n\n"
        f"<b>{client['name']}</b>\n"
        f"Списано: {amount:.2f} USD\n"
        f"{_balance_line(balance)}"
    )
    await callback.message.edit_text(text, reply_markup=after_action_kb(client_id))
    await callback.answer("Готово!")


@router.callback_query(F.data.startswith("ad:"))
async def cb_apply_debt(callback: CallbackQuery, state: FSMContext) -> None:
    """
    Apply a debt entry. The reason note lives in FSM state (set by
    on_debt_note_typed just before we drew the confirmation keyboard).
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
    data = await state.get_data()
    note = str(data.get("note") or "").strip()
    await state.clear()
    if not note:
        # Shouldn't happen in a normal flow, but guard against a stale
        # confirmation button being tapped from an old chat message.
        await callback.answer(
            "Заметка потеряна. Открой /start и попробуй ещё раз.",
            show_alert=True,
        )
        return
    ok, err = add_client_debt(client_id, amount, note=note)
    if not ok:
        logger.error("bot debt failed: client=%s amt=%s err=%s", client_id, amount, err)
        await callback.answer(f"Ошибка: {err}", show_alert=True)
        return
    client = get_client(client_id)
    balance = get_client_balance(client_id)
    text = (
        f"✅ Долг добавлен.\n\n"
        f"<b>{client['name']}</b>\n"
        f"Добавлено: {amount:.2f} USD\n"
        f"Заметка: <i>{note}</i>\n"
        f"{_balance_line(balance)}"
    )
    await callback.message.edit_text(text, reply_markup=after_action_kb(client_id))
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

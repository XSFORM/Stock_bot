"""Inline keyboards for the redesigned Telegram bot.

Callback data uses a tiny domain-specific mini-protocol so we can route
button taps through aiogram's callback_query handler without dragging in
an FSM for every step.

Formats:
    menu               → open main menu
    search             → prompt for a name substring
    top                → show top debtors
    c:<id>             → open client card
    p:<id>:<amount>    → apply payment of <amount> USD to client <id>
    d:<id>             → prompt "add debt" free amount for client <id>
    o:<id>             → prompt "other payment" free amount for client <id>
    h:<id>             → show full history of client <id>
    ap:<id>:<amount>   → confirm-apply payment (comes from the ✅ button)
    ad:<id>:<amount>   → confirm-apply debt
    cn                 → cancel current action

    Phase 2 — expenses via bot:
    xm                     → open /expense menu (category picker)
    xc:<cat_id>            → category chosen; ask amount next
    xn:<cat_id>:<amount>   → skip note; go straight to confirm
    xd:<mode>:<cat>:<amt>  → toggle date on confirm screen; mode ∈ {t=today, y=yesterday}
    xa:<cat>:<amt>:<date>  → apply expense (final confirm tap)
"""
from __future__ import annotations

from typing import Any

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


# Quick-pay amounts shown on the client card. Simple list — changing it
# is a one-line edit; if the user later wants configurable presets we
# can wire it to a setting.
QUICK_AMOUNTS: tuple[int, ...] = (50, 100, 200, 500)


def fmt_balance(balance: float) -> str:
    """Compact balance rendering for buttons and lines."""
    if balance > 0:
        return f"{balance:.2f}$"
    if balance < 0:
        return f"({abs(balance):.2f}$)"  # parentheses = advance
    return "0$"


def main_menu_kb(recent_clients: list[dict[str, Any]]) -> InlineKeyboardMarkup:
    """
    Main menu shown on /start.

    Recent clients are inline buttons — one per row (name + balance).
    Below them: search, top debtors.
    """
    rows: list[list[InlineKeyboardButton]] = []
    for c in recent_clients:
        label = f"{c['name']} — {fmt_balance(float(c.get('balance') or 0))}"
        rows.append([
            InlineKeyboardButton(text=label, callback_data=f"c:{c['id']}")
        ])
    rows.append([
        InlineKeyboardButton(text="🔍 Поиск клиента", callback_data="search"),
        InlineKeyboardButton(text="📊 Топ должников", callback_data="top"),
    ])
    rows.append([
        InlineKeyboardButton(text="💸 Добавить расход", callback_data="xm"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def client_card_kb(client_id: int, phone: str | None) -> InlineKeyboardMarkup:
    """
    Buttons under a single client's card: quick payments, add debt,
    other amount, history, back to menu.

    NOTE: no tel:-URL button here. Telegram Bot API validates URL buttons
    strictly and rejects the entire message with «Wrong port number
    specified in the URL» when it sees `tel:+9936…` (the `+` after the
    scheme confuses its parser). The phone number is already visible in
    the card body — on mobile Telegram long-press turns it into a live
    tel: link automatically.
    """
    rows: list[list[InlineKeyboardButton]] = []
    # Row 1: quick-pay buttons (payment reduces debt)
    rows.append([
        InlineKeyboardButton(
            text=f"💵 {amount}",
            callback_data=f"p:{client_id}:{amount}",
        )
        for amount in QUICK_AMOUNTS
    ])
    # Row 2: free-amount payment and add-debt
    rows.append([
        InlineKeyboardButton(text="💰 Другая оплата", callback_data=f"o:{client_id}"),
        InlineKeyboardButton(text="➕ Добавить долг", callback_data=f"d:{client_id}"),
    ])
    # Row 3: history + back
    rows.append([
        InlineKeyboardButton(text="📋 Вся история", callback_data=f"h:{client_id}"),
        InlineKeyboardButton(text="↺ Другой клиент", callback_data="menu"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def confirm_payment_kb(client_id: int, amount: float) -> InlineKeyboardMarkup:
    """✅/❌ pair shown before applying a payment."""
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="✅ Да, списать",
            callback_data=f"ap:{client_id}:{amount:.2f}",
        ),
        InlineKeyboardButton(text="❌ Отмена", callback_data=f"c:{client_id}"),
    ]])


def confirm_debt_kb(client_id: int, amount: float) -> InlineKeyboardMarkup:
    """✅/❌ pair shown before adding debt."""
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="✅ Да, добавить долг",
            callback_data=f"ad:{client_id}:{amount:.2f}",
        ),
        InlineKeyboardButton(text="❌ Отмена", callback_data=f"c:{client_id}"),
    ]])


def after_action_kb(client_id: int) -> InlineKeyboardMarkup:
    """Shown after a payment/debt is successfully applied."""
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="↺ К клиенту",  callback_data=f"c:{client_id}"),
        InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu"),
    ]])


def search_results_kb(clients: list[dict[str, Any]]) -> InlineKeyboardMarkup:
    """Result list after user types a search substring."""
    rows: list[list[InlineKeyboardButton]] = []
    for c in clients:
        label = f"{c['name']} — {fmt_balance(float(c.get('balance') or 0))}"
        rows.append([
            InlineKeyboardButton(text=label, callback_data=f"c:{c['id']}")
        ])
    rows.append([
        InlineKeyboardButton(text="🔍 Искать ещё", callback_data="search"),
        InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def cancel_kb() -> InlineKeyboardMarkup:
    """Shown while waiting for free-text input (amount, search query)."""
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="❌ Отмена", callback_data="cn"),
    ]])


# ═════════════════════════════════════════════════════════════════════════════
# Phase 2 — expenses via the bot
# ═════════════════════════════════════════════════════════════════════════════


def expense_categories_kb(categories: list[dict[str, Any]]) -> InlineKeyboardMarkup:
    """
    Two-columns of category buttons. Business rows first, then personal.
    Callback: xc:<id>.

    `categories` should already be filtered to non-archived. The caller
    typically passes list_expense_categories() (which excludes archived).
    """
    biz = [c for c in categories if c.get("kind") == "business"]
    per = [c for c in categories if c.get("kind") == "personal"]

    def _pairs(items: list[dict[str, Any]], emoji: str) -> list[list[InlineKeyboardButton]]:
        rows: list[list[InlineKeyboardButton]] = []
        row: list[InlineKeyboardButton] = []
        for c in items:
            row.append(InlineKeyboardButton(
                text=f"{emoji} {c['name']}",
                callback_data=f"xc:{c['id']}",
            ))
            if len(row) == 2:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        return rows

    rows: list[list[InlineKeyboardButton]] = []
    rows.extend(_pairs(biz, "📦"))
    rows.extend(_pairs(per, "🛒"))
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data="cn")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def expense_note_kb(cat_id: int, amount: float) -> InlineKeyboardMarkup:
    """After amount is entered — offer to skip the optional note."""
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="⏭ Пропустить", callback_data=f"xn:{cat_id}:{amount:.2f}"),
        InlineKeyboardButton(text="❌ Отмена", callback_data="cn"),
    ]])


def expense_confirm_kb(cat_id: int, amount: float, date_iso: str, is_today: bool) -> InlineKeyboardMarkup:
    """
    Final confirm screen. Two rows:
      [✅ Применить] [📅 Вчера | 📅 Сегодня] [❌ Отмена]
    The date-toggle button flips today↔yesterday so the user can quickly
    correct the date without cancelling the whole flow.
    """
    toggle_text = "📅 Вчера" if is_today else "📅 Сегодня"
    toggle_mode = "y" if is_today else "t"   # what to switch TO
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="✅ Применить",
            callback_data=f"xa:{cat_id}:{amount:.2f}:{date_iso}",
        ),
        InlineKeyboardButton(
            text=toggle_text,
            callback_data=f"xd:{toggle_mode}:{cat_id}:{amount:.2f}",
        ),
        InlineKeyboardButton(text="❌ Отмена", callback_data="cn"),
    ]])


def expense_after_kb() -> InlineKeyboardMarkup:
    """Shown after an expense was successfully added."""
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="💸 Ещё расход", callback_data="xm"),
        InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu"),
    ]])

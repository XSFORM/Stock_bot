"""FSM states for the Telegram bot.

Phase 6 (redesign): bot is now scoped to client-debt management from
the field. We only need two waiting states — for a free-form amount
and for a client search query.
"""
from aiogram.fsm.state import State, StatesGroup


class Payment(StatesGroup):
    """User is entering a custom payment/debt amount for a specific client."""
    waiting_amount = State()
    # For DEBT only — after amount we ask for a mandatory reason note.
    # Payments skip this step (note stays empty), matching how the web
    # form behaves: debt requires a reason, payment does not.
    waiting_note = State()


class ClientSearch(StatesGroup):
    """User is typing a substring to look up a client by name."""
    waiting_query = State()


class ExpenseFlow(StatesGroup):
    """
    /expense wizard:
      1. category chosen (inline button)  → waiting_amount
      2. amount typed as text             → waiting_note
      3. note typed OR "Пропустить"       → confirmation screen (no state)
      4. confirm tap                      → add_expense() and done
    """
    waiting_amount = State()
    waiting_note = State()

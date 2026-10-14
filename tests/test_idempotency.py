"""Phase 11 — server-side nonce guard for POST forms.

The whole point of the module is that a repeat submit with the same nonce
is a no-op. These tests pin that behaviour so a future refactor can't
quietly weaken it.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _reset():
    """Clear the LRU between tests so their nonces don't leak into each other."""
    from app.utils.idempotency import _reset_for_tests
    _reset_for_tests()


def test_make_nonce_returns_fresh_values() -> None:
    """Two renders of the same form must get different nonces — else double
    submit would be impossible to distinguish from a legit second submit."""
    from app.utils.idempotency import make_nonce
    a = make_nonce()
    b = make_nonce()
    assert a and b
    assert a != b
    assert len(a) >= 16  # 16 bytes of url-safe entropy, that's ~22 chars


def test_check_and_consume_is_idempotent() -> None:
    from app.utils.idempotency import make_nonce, check_and_consume
    n = make_nonce()
    assert check_and_consume(n) is True   # first use
    assert check_and_consume(n) is False  # second use blocked
    assert check_and_consume(n) is False  # third too


def test_empty_nonce_always_passes() -> None:
    """Legacy forms that haven't been migrated yet must keep working —
    the guard is opt-in per form."""
    from app.utils.idempotency import check_and_consume
    assert check_and_consume("") is True
    assert check_and_consume("") is True
    assert check_and_consume(None) is True  # type: ignore[arg-type]


def test_different_nonces_are_independent() -> None:
    from app.utils.idempotency import make_nonce, check_and_consume
    a, b = make_nonce(), make_nonce()
    assert check_and_consume(a) is True
    assert check_and_consume(b) is True  # b independent of a
    assert check_and_consume(a) is False
    assert check_and_consume(b) is False


def test_lru_evicts_oldest() -> None:
    """When the cache overflows, the earliest nonce is dropped. If the
    user had a 500-forms-ago tab still open, letting them submit it a
    second time is acceptable — the double-tap race window is seconds."""
    from app.utils.idempotency import check_and_consume, _MAX_TOKENS, _reset_for_tests
    _reset_for_tests()
    oldest = "oldest-nonce"
    assert check_and_consume(oldest) is True
    # Fill cache past capacity
    for i in range(_MAX_TOKENS + 10):
        check_and_consume(f"filler-{i}")
    # Oldest should've been evicted — a replay now goes through.
    assert check_and_consume(oldest) is True
    # But it also got re-recorded, so second replay is blocked again.
    assert check_and_consume(oldest) is False

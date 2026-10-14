"""Idempotency guard for POST forms.

Protects money-changing endpoints (/sale/add, /receive/item/add, …) from
double submissions. Common causes on a flaky connection:
  * user clicks «Добавить» twice because the first response is slow
  * browser retries POST after a network timeout
  * duplicate HTMX requests from double-click on the submit button

Mechanism:
  1. Each form render stamps a hidden <input name="nonce" value="…">.
     Nonce is a fresh random token per render (`make_nonce()`).
  2. On submit, the server calls `check_and_consume(nonce)`. First call for
     a given nonce returns True (go through); any subsequent call returns
     False (handler should silently redirect back without a second INSERT).
  3. Nonces live in a bounded in-process LRU cache (works for single-worker
     uvicorn deployments — both server stockweb.service and desktop run
     one worker; if ever we scale, swap `_tokens` for Redis-backed storage).

Client-side companion: forms with `data-idempotent="1"` have their submit
button disabled by JS in base.html for ~10 s after click, which stops the
most common cause (impatient re-click) before the request ever leaves the
browser. The server-side guard catches the harder cases (browser retry,
very slow network).
"""
from __future__ import annotations

import secrets
from collections import OrderedDict
from threading import Lock

# Max distinct in-flight nonces. 500 covers hours of active sale work by
# one operator; evicting FIFO is fine — if the user is still messing with
# a form from 500 submits ago, the race-condition window has long closed.
_MAX_TOKENS = 500

_tokens: "OrderedDict[str, bool]" = OrderedDict()
_lock = Lock()


def make_nonce() -> str:
    """Return a fresh random nonce for a form render. 16 bytes url-safe."""
    return secrets.token_urlsafe(16)


def check_and_consume(nonce: str) -> bool:
    """Return True on first use, False on subsequent uses.

    Empty / missing nonces return True (backward-compat for forms that
    haven't been migrated yet). This way the guard is opt-in per form,
    adding it never breaks older ones.

    Thread-safe: lock protects the OrderedDict against races between
    workers / background tasks hitting the same handler.
    """
    if not nonce:
        return True
    with _lock:
        if nonce in _tokens:
            return False
        _tokens[nonce] = True
        while len(_tokens) > _MAX_TOKENS:
            _tokens.popitem(last=False)
        return True


def _reset_for_tests() -> None:
    """Pytest helper — clear the cache between tests."""
    with _lock:
        _tokens.clear()

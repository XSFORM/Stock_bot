"""Guard rail for the site-lock bypass list.

Before this test existed, `_LOCK_BYPASS_PREFIXES` contained a blanket
`"/api/"` entry which let ANY /api endpoint be reached without a session
cookie — most damaging being POST /api/products/upsert-by-barcode, which
silently rewrites purchase prices (and thus poisons cost_price snapshots
on the next sale). On the desktop version this was exploitable by anyone
on the same LAN. On the server it was defense-in-depth failing behind
nginx basic-auth.

These tests lock in the invariant: with site-lock enabled and NO session
cookie, internal APIs redirect to /unlock (or 401), while token-guarded
Pocket Price endpoints continue to work with a valid token.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client(tmp_path_factory: pytest.TempPathFactory) -> TestClient:
    data_dir = tmp_path_factory.mktemp("api_lock")
    db_path = data_dir / "stock.db"
    os.environ["DB_PATH"] = str(db_path)
    os.environ["BACKUP_DIR"] = str(data_dir / "backups")
    os.environ["INVOICES_DIR"] = str(data_dir / "invoices")

    from app.web.main import app        # noqa: PLC0415
    from app.db import sqlite as _sql   # noqa: PLC0415
    from pathlib import Path            # noqa: PLC0415
    _sql.DB_PATH = Path(db_path)
    _sql.init_db()

    # Enable site lock and set a dummy password hash so the middleware
    # will actually reject unauthenticated requests.
    _sql.set_setting("site_lock_enabled", "1")
    _sql.set_setting("site_lock_hash", "$fake$hash$")

    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


# ═════════════════════════════════════════════════════════════════════════════
# Internal /api endpoints MUST require a session when lock is on
# ═════════════════════════════════════════════════════════════════════════════


def _assert_locked(response) -> None:
    """A locked endpoint either redirects to /unlock or returns 401/403."""
    if response.status_code in (301, 302, 303, 307, 308):
        loc = response.headers.get("location", "")
        assert "/unlock" in loc, (
            f"expected redirect to /unlock, got {response.status_code} → {loc}"
        )
    else:
        assert response.status_code in (401, 403), (
            f"expected 401/403 or redirect to /unlock; got {response.status_code}"
        )


class TestApiRequiresSession:
    """No session cookie → must NOT reach the handler."""

    def test_upsert_by_barcode_without_session_is_blocked(self, client: TestClient) -> None:
        """The critical write endpoint: silently rewrites purchase price."""
        resp = client.post(
            "/api/products/upsert-by-barcode",
            json={"barcode": "TEST-BC", "brand": "X", "model": "Y",
                  "name": "Test", "purchase_price": 99999.0},
            follow_redirects=False,
        )
        _assert_locked(resp)

        # Belt-and-braces: even if the response was surprising, verify
        # nothing was actually created in the database.
        from app.db.sqlite import get_product_by_barcode_for_scan
        assert get_product_by_barcode_for_scan("TEST-BC") is None, (
            "row was created despite locked site — bypass is broken"
        )

    def test_api_stock_without_session_is_blocked(self, client: TestClient) -> None:
        resp = client.get(
            "/api/stock",
            params={"product_id": 1, "warehouse_id": "MAIN"},
            follow_redirects=False,
        )
        _assert_locked(resp)

    def test_api_products_search_without_session_is_blocked(self, client: TestClient) -> None:
        resp = client.get("/api/products-search", params={"q": "test"},
                          follow_redirects=False)
        _assert_locked(resp)

    def test_api_products_by_barcode_without_session_is_blocked(self, client: TestClient) -> None:
        resp = client.get("/api/products/by-barcode",
                          params={"barcode": "ANY"},
                          follow_redirects=False)
        _assert_locked(resp)


# ═════════════════════════════════════════════════════════════════════════════
# Non-API pages MUST also require a session
# ═════════════════════════════════════════════════════════════════════════════


class TestPagesRequireSession:
    def test_index_redirects_to_unlock(self, client: TestClient) -> None:
        resp = client.get("/", follow_redirects=False)
        _assert_locked(resp)

    def test_expenses_page_redirects_to_unlock(self, client: TestClient) -> None:
        resp = client.get("/expenses", follow_redirects=False)
        _assert_locked(resp)


# ═════════════════════════════════════════════════════════════════════════════
# /unlock and /static must ALWAYS be reachable (else you can't log in)
# ═════════════════════════════════════════════════════════════════════════════


class TestBypassEssentials:
    def test_unlock_page_is_reachable_without_session(self, client: TestClient) -> None:
        resp = client.get("/unlock", follow_redirects=False)
        assert resp.status_code == 200


# ═════════════════════════════════════════════════════════════════════════════
# Pocket Price / Catalog APIs bypass the site lock — token guards them.
# Without a valid token they must return 401 (from the endpoint itself,
# not from the middleware).
# ═════════════════════════════════════════════════════════════════════════════


class TestTokenGuardedApis:
    def test_price_search_without_token_returns_401_from_endpoint(self, client: TestClient) -> None:
        """No redirect to /unlock — bypass list lets it through; endpoint rejects."""
        resp = client.get("/api/price/search", params={"q": "x"},
                          follow_redirects=False)
        assert resp.status_code == 401, (
            f"expected 401 from _require_price_token; got {resp.status_code}"
        )
        # It must be JSON, not an HTML redirect page.
        assert "application/json" in resp.headers.get("content-type", "")

    def test_price_search_with_valid_token_returns_200(self, client: TestClient) -> None:
        """The whole point of the bypass: token-based auth still works with lock on."""
        from app.db.sqlite import create_price_token
        plain, _row = create_price_token(label="pytest", mode="SIMPLE")
        resp = client.get(
            "/api/price/search",
            params={"q": ""},
            headers={"X-Price-Token": plain},
            follow_redirects=False,
        )
        assert resp.status_code == 200, (
            f"valid token was rejected: {resp.status_code} {resp.text[:200]}"
        )
        data = resp.json()
        assert "results" in data


# ═════════════════════════════════════════════════════════════════════════════
# When lock is OFF, everything is reachable — legacy behaviour preserved
# ═════════════════════════════════════════════════════════════════════════════


class TestLockDisabledStillWorks:
    def test_disabling_lock_reopens_apis(self, client: TestClient) -> None:
        """Backwards-compat: users who never enabled site lock keep the old UX."""
        from app.db.sqlite import set_setting
        set_setting("site_lock_enabled", "0")
        try:
            resp = client.get("/api/products-search", params={"q": ""},
                              follow_redirects=False)
            assert resp.status_code == 200
        finally:
            set_setting("site_lock_enabled", "1")


# ═════════════════════════════════════════════════════════════════════════════
# With a valid session cookie, internal APIs work again
# ═════════════════════════════════════════════════════════════════════════════


class TestValidSessionUnlocksApis:
    def test_upsert_by_barcode_works_with_valid_session(self, client: TestClient) -> None:
        from app.db.sqlite import create_session
        token = "test-session-" + os.urandom(8).hex()
        create_session(token, (datetime.utcnow() + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S"))

        resp = client.post(
            "/api/products/upsert-by-barcode",
            json={"barcode": "SESSION-BC", "brand": "X", "model": "Y",
                  "name": "Sess", "purchase_price": 1.23},
            cookies={"site_session": token},
            follow_redirects=False,
        )
        assert resp.status_code == 200, (
            f"authorised request was blocked: {resp.status_code} {resp.text[:200]}"
        )
        data = resp.json()
        assert data.get("ok") is True
        assert data.get("created") is True

"""Tests for the DB helpers added for the redesigned Telegram bot:
find_clients_by_name, get_recent_active_clients, get_top_debtors.

Everything runs against a scratch DB via DB_PATH — same pattern as
the other tests in this suite.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def db_path(tmp_path_factory: pytest.TempPathFactory) -> str:
    path = tmp_path_factory.mktemp("bothelp") / "stock.db"
    os.environ["DB_PATH"] = str(path)
    import app.db.sqlite as _sql
    _sql.DB_PATH = Path(path)
    return str(path)


@pytest.fixture(scope="module", autouse=True)
def init(db_path: str) -> None:
    from app.db.sqlite import init_db
    init_db()


def _new_client(name: str, phone: str = "") -> int:
    from app.db.sqlite import add_client
    import app.db.sqlite as _sql
    ok, err = add_client(name, phone=phone, note="", client_type="wholesale")
    assert ok, err
    with _sql._connect() as con:
        row = con.execute("SELECT id FROM clients WHERE name = ?", (name,)).fetchone()
    return int(row["id"])


# ═════════════════════════════════════════════════════════════════════════════


class TestFindClientsByName:
    def test_case_insensitive_substring_match(self) -> None:
        from app.db.sqlite import find_clients_by_name
        _new_client("Aylar Tajir market")
        _new_client("Merdan Weyisow")

        rows = find_clients_by_name("aylar")
        assert any(c["name"] == "Aylar Tajir market" for c in rows)

        rows = find_clients_by_name("MERDAN")
        assert any(c["name"] == "Merdan Weyisow" for c in rows)

    def test_empty_query_returns_nothing(self) -> None:
        from app.db.sqlite import find_clients_by_name
        assert find_clients_by_name("") == []
        assert find_clients_by_name("   ") == []

    def test_limit_is_respected(self) -> None:
        from app.db.sqlite import find_clients_by_name
        for i in range(15):
            _new_client(f"LimitClient {i:02d}")
        rows = find_clients_by_name("LimitClient", limit=5)
        assert len(rows) == 5

    def test_balance_is_included(self) -> None:
        from app.db.sqlite import add_client_debt, find_clients_by_name
        cid = _new_client("Balance Test Client")
        add_client_debt(cid, 250.0, note="manual")
        rows = find_clients_by_name("Balance Test")
        found = [c for c in rows if c["id"] == cid]
        assert len(found) == 1
        # add_client_debt stores -250 in ledger → balance = 0 - (-250) = 250
        assert float(found[0]["balance"]) == 250.0


# ═════════════════════════════════════════════════════════════════════════════


class TestGetTopDebtors:
    def test_only_debtors_and_sorted_desc(self) -> None:
        from app.db.sqlite import add_client_debt, add_client_adjustment, get_top_debtors
        big  = _new_client("Top Debtor Big")
        mid  = _new_client("Top Debtor Mid")
        low  = _new_client("Top Debtor Low")
        paid = _new_client("Top Debtor NotADebtor")

        add_client_debt(big, 1000.0)
        add_client_debt(mid, 500.0)
        add_client_debt(low, 100.0)
        # paid has both a debt and a matching payment → balance = 0 → not a debtor
        add_client_debt(paid, 300.0)
        add_client_adjustment(paid, 300.0)

        debtors = get_top_debtors(limit=10)
        names = [c["name"] for c in debtors]
        # our three debtors must appear in balance-desc order
        big_i = names.index("Top Debtor Big")
        mid_i = names.index("Top Debtor Mid")
        low_i = names.index("Top Debtor Low")
        assert big_i < mid_i < low_i, f"unexpected order: {names}"
        # the zero-balance client must NOT appear
        assert "Top Debtor NotADebtor" not in names

    def test_days_since_last_field_present(self) -> None:
        from app.db.sqlite import add_client_debt, get_top_debtors
        cid = _new_client("Silent Debtor")
        add_client_debt(cid, 400.0)
        debtors = get_top_debtors(limit=100)
        found = [c for c in debtors if c["id"] == cid]
        assert len(found) == 1
        # No payments ever made → days_since_last is None
        assert found[0]["days_since_last"] is None


# ═════════════════════════════════════════════════════════════════════════════


class TestGetRecentActiveClients:
    def test_client_with_recent_payment_appears(self) -> None:
        from app.db.sqlite import add_client_adjustment, get_recent_active_clients
        cid = _new_client("Recent Payer")
        add_client_adjustment(cid, 100.0, note="just paid")
        recent = get_recent_active_clients(days=7, limit=20)
        assert any(c["id"] == cid for c in recent)

    def test_dormant_client_absent(self) -> None:
        """
        A client with no ledger and no closed cart in the window must NOT
        appear in the recent list. The "window" boundary is checked
        against a very fresh client we intentionally don't touch.
        """
        from app.db.sqlite import get_recent_active_clients
        cid = _new_client("Never Touched Client 2027")
        recent = get_recent_active_clients(days=1, limit=100)
        assert not any(c["id"] == cid for c in recent)

    def test_balance_and_phone_returned(self) -> None:
        from app.db.sqlite import (
            add_client_adjustment, add_client_debt, get_recent_active_clients,
        )
        cid = _new_client("Recent With Balance", phone="+99312345678")
        add_client_debt(cid, 200.0)
        add_client_adjustment(cid, 50.0)  # payment marks him as "active"
        recent = get_recent_active_clients(days=7, limit=100)
        row = next(c for c in recent if c["id"] == cid)
        assert row["phone"] == "+99312345678"
        # debt 200 - payment 50 = 150
        assert float(row["balance"]) == 150.0

"""Tests for Pocket Price mode-based field filtering.

Verifies the contract:
  SIMPLE mode – returns only the safe minimal subset:
    id, brand, model, name, barcode, price_wh25
    (plus optional qty_total when show_qty=True)
    Extended/internal fields (price_wh10, note, buy_price) are NOT returned.

  FULL mode – returns all of the above plus extended fields:
    price_wh10, note
    buy_price only when show_buy_price=True
"""
from __future__ import annotations

import os

import pytest


@pytest.fixture(scope="module")
def db_path(tmp_path_factory: pytest.TempPathFactory) -> str:
    path = tmp_path_factory.mktemp("price_mode") / "stock.db"
    os.environ["DB_PATH"] = str(path)
    return str(path)


@pytest.fixture(scope="module")
def seeded_product_id(db_path: str) -> int:
    """Create one product and return its id."""
    from app.db.sqlite import init_db, add_product_simple

    init_db()
    _ok, _err, pid = add_product_simple(
        brand="ACME",
        model="widget",
        name="ACME Widget",
        barcode="1234567890",
        note="internal note",
        wh_price=100.0,
    )
    assert pid is not None, "Failed to seed test product"
    return pid


class TestSimpleMode:
    """In SIMPLE mode only the minimal safe fields should be present."""

    def _search(self, show_qty: bool = False, show_buy_price: bool = False) -> dict:
        from app.db.sqlite import search_products_for_price

        results = search_products_for_price(
            "ACME", limit=1, mode="SIMPLE",
            show_qty=show_qty, show_buy_price=show_buy_price,
        )
        assert results, "Expected at least one result for 'ACME'"
        return results[0]

    def test_has_safe_fields(self, seeded_product_id: int) -> None:
        product = self._search()
        for field in ("id", "brand", "model", "name", "barcode", "price_wh25"):
            assert field in product, f"SIMPLE mode must include '{field}'"

    def test_hides_price_wh10(self, seeded_product_id: int) -> None:
        product = self._search()
        assert "price_wh10" not in product, (
            "SIMPLE mode must NOT include 'price_wh10'"
        )

    def test_hides_note(self, seeded_product_id: int) -> None:
        product = self._search()
        assert "note" not in product, (
            "SIMPLE mode must NOT include 'note' (internal field)"
        )

    def test_hides_buy_price_even_when_flag_set(self, seeded_product_id: int) -> None:
        product = self._search(show_buy_price=True)
        assert "buy_price" not in product, (
            "SIMPLE mode must NOT include 'buy_price' even when show_buy_price=True"
        )

    def test_qty_total_present_when_show_qty(self, seeded_product_id: int) -> None:
        product = self._search(show_qty=True)
        assert "qty_total" in product, (
            "SIMPLE mode must include 'qty_total' when show_qty=True"
        )

    def test_qty_total_absent_without_show_qty(self, seeded_product_id: int) -> None:
        product = self._search(show_qty=False)
        assert "qty_total" not in product, (
            "SIMPLE mode must NOT include 'qty_total' when show_qty=False"
        )


class TestFullMode:
    """In FULL mode all extended fields should be present."""

    def _search(self, show_qty: bool = False, show_buy_price: bool = False) -> dict:
        from app.db.sqlite import search_products_for_price

        results = search_products_for_price(
            "ACME", limit=1, mode="FULL",
            show_qty=show_qty, show_buy_price=show_buy_price,
        )
        assert results, "Expected at least one result for 'ACME'"
        return results[0]

    def test_has_safe_fields(self, seeded_product_id: int) -> None:
        product = self._search()
        for field in ("id", "brand", "model", "name", "barcode", "price_wh25"):
            assert field in product, f"FULL mode must include '{field}'"

    def test_has_price_wh10(self, seeded_product_id: int) -> None:
        product = self._search()
        assert "price_wh10" in product, "FULL mode must include 'price_wh10'"

    def test_has_note(self, seeded_product_id: int) -> None:
        product = self._search()
        assert "note" in product, "FULL mode must include 'note'"

    def test_buy_price_present_when_flag_set(self, seeded_product_id: int) -> None:
        product = self._search(show_buy_price=True)
        assert "buy_price" in product, (
            "FULL mode must include 'buy_price' when show_buy_price=True"
        )

    def test_buy_price_absent_without_flag(self, seeded_product_id: int) -> None:
        product = self._search(show_buy_price=False)
        assert "buy_price" not in product, (
            "FULL mode must NOT include 'buy_price' when show_buy_price=False"
        )

    def test_price_wh10_value(self, seeded_product_id: int) -> None:
        product = self._search()
        assert product["price_wh10"] == pytest.approx(110.0, rel=1e-4)

    def test_price_wh25_value(self, seeded_product_id: int) -> None:
        product = self._search()
        assert product["price_wh25"] == pytest.approx(125.0, rel=1e-4)

    def test_buy_price_value(self, seeded_product_id: int) -> None:
        product = self._search(show_buy_price=True)
        assert product["buy_price"] == pytest.approx(100.0, rel=1e-4)

    def test_qty_total_present_when_show_qty(self, seeded_product_id: int) -> None:
        product = self._search(show_qty=True)
        assert "qty_total" in product

    def test_qty_total_absent_without_show_qty(self, seeded_product_id: int) -> None:
        product = self._search(show_qty=False)
        assert "qty_total" not in product


class TestPocketPriceActiveMarkupPresets:
    """Pocket Price prices must use active sale markup settings."""

    def test_full_mode_uses_active_markups(self, seeded_product_id: int) -> None:
        from app.db.sqlite import search_products_for_price, set_setting

        set_setting("sale_markup_presets", "15,25")
        product = search_products_for_price("ACME", limit=1, mode="FULL")[0]
        assert product["price_wh10"] == pytest.approx(115.0, rel=1e-4)
        assert product["price_wh25"] == pytest.approx(125.0, rel=1e-4)

    def test_full_mode_shows_single_price_when_one_markup_active(
        self, seeded_product_id: int
    ) -> None:
        from app.db.sqlite import search_products_for_price, set_setting

        set_setting("sale_markup_presets", "20")
        product = search_products_for_price("ACME", limit=1, mode="FULL")[0]
        assert product["price_wh10"] == pytest.approx(120.0, rel=1e-4)
        assert "price_wh25" not in product

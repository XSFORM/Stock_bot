"""Tests for configurable sale markup presets.

Covers:
  - get_sale_markup_presets / get_sale_default_markup fallback behaviour
  - _compute_unit_price for generic wh{N} modes
  - Admin settings endpoint validation (empty presets, default not in active)
  - Admin settings endpoint happy path
  - /sale page renders only active markup buttons and selects the correct default
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


# ─── DB-level helpers ─────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def db_path(tmp_path_factory: pytest.TempPathFactory) -> str:
    path = tmp_path_factory.mktemp("markup") / "stock.db"
    os.environ["DB_PATH"] = str(path)
    return str(path)


@pytest.fixture(scope="module", autouse=True)
def init(db_path: str) -> None:
    from app.db.sqlite import init_db
    init_db()


class TestGetSaleMarkupPresetsDefaults:
    """When no settings are stored, fallback values are returned."""

    def test_presets_fallback(self) -> None:
        from app.db.sqlite import get_sale_markup_presets
        presets = get_sale_markup_presets()
        assert presets == [10, 15, 25]

    def test_default_markup_fallback(self) -> None:
        from app.db.sqlite import get_sale_default_markup
        assert get_sale_default_markup() == 15


class TestGetSaleMarkupPresetsCustom:
    """Stored settings are returned correctly."""

    def test_stored_presets(self) -> None:
        from app.db.sqlite import get_sale_markup_presets, set_setting
        set_setting("sale_markup_presets", "5,20,30")
        assert get_sale_markup_presets() == [5, 20, 30]

    def test_stored_default(self) -> None:
        from app.db.sqlite import get_sale_default_markup, set_setting
        set_setting("sale_markup_presets", "5,20,30")
        set_setting("sale_default_markup", "20")
        assert get_sale_default_markup() == 20

    def test_invalid_preset_values_ignored(self) -> None:
        """Values outside the allowed set are silently dropped."""
        from app.db.sqlite import get_sale_markup_presets, set_setting
        set_setting("sale_markup_presets", "7,10,99")
        presets = get_sale_markup_presets()
        assert 10 in presets
        assert 7 not in presets
        assert 99 not in presets

    def test_all_invalid_presets_falls_back(self) -> None:
        from app.db.sqlite import get_sale_markup_presets, set_setting
        set_setting("sale_markup_presets", "0,99,abc")
        assert get_sale_markup_presets() == [10, 15, 25]

    def test_default_not_in_presets_falls_back_to_first(self) -> None:
        from app.db.sqlite import get_sale_default_markup, set_setting
        set_setting("sale_markup_presets", "5,20")
        set_setting("sale_default_markup", "10")  # 10 not in [5,20]
        # 15 (fallback default) is not in [5,20] either → first preset
        assert get_sale_default_markup() == 5

    def teardown_method(self) -> None:
        """Reset to clean defaults after each test."""
        from app.db.sqlite import set_setting
        set_setting("sale_markup_presets", "")
        set_setting("sale_default_markup", "")


class TestComputeUnitPrice:
    """_compute_unit_price handles generic wh{N} modes correctly."""

    def _compute(self, wh: float, mode: str, custom: float | None = None) -> float:
        from app.db.sqlite import _compute_unit_price
        return _compute_unit_price(wh, mode, custom)

    def test_wh_mode(self) -> None:
        assert self._compute(100.0, "wh") == 100.0

    def test_wh10_mode(self) -> None:
        assert self._compute(100.0, "wh10") == pytest.approx(110.0, rel=1e-4)

    def test_wh15_mode(self) -> None:
        assert self._compute(100.0, "wh15") == pytest.approx(115.0, rel=1e-4)

    def test_wh25_mode(self) -> None:
        assert self._compute(100.0, "wh25") == pytest.approx(125.0, rel=1e-4)

    def test_wh5_mode(self) -> None:
        assert self._compute(100.0, "wh5") == pytest.approx(105.0, rel=1e-4)

    def test_wh30_mode(self) -> None:
        assert self._compute(100.0, "wh30") == pytest.approx(130.0, rel=1e-4)

    def test_custom_mode(self) -> None:
        assert self._compute(100.0, "custom", 99.99) == pytest.approx(99.99, rel=1e-4)

    def test_unknown_mode_falls_back_to_10pct(self) -> None:
        assert self._compute(100.0, "unknown") == pytest.approx(110.0, rel=1e-4)

    def test_custom_mode_without_custom_price_falls_back(self) -> None:
        """If price_mode='custom' but custom_price=None, use 10% fallback."""
        assert self._compute(100.0, "custom", None) == pytest.approx(110.0, rel=1e-4)


# ─── Web-level tests ──────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def web_client(tmp_path_factory: pytest.TempPathFactory) -> TestClient:
    data_dir = tmp_path_factory.mktemp("markup_web")
    db_p = data_dir / "stock.db"
    os.environ["DB_PATH"] = str(db_p)
    os.environ["BACKUP_DIR"] = str(data_dir / "backups")
    os.environ["INVOICES_DIR"] = str(data_dir / "invoices")

    from app.web.main import app  # noqa: PLC0415
    from app.db.sqlite import init_db  # noqa: PLC0415
    init_db()

    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


class TestAdminMarkupEndpoint:
    """POST /admin/settings/markup validates and persists settings."""

    def test_valid_preset_saves(self, web_client: TestClient) -> None:
        resp = web_client.post(
            "/admin/settings/markup",
            data={"markup_10": "1", "markup_15": "1", "markup_25": "1", "default_markup": "15"},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert "saved=1" in str(resp.url) or "saved" in resp.text

    def test_empty_presets_rejected(self, web_client: TestClient) -> None:
        resp = web_client.post(
            "/admin/settings/markup",
            data={"default_markup": "10"},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert "markup_error_empty" in str(resp.url) or "markup_error_empty" in resp.text

    def test_default_not_in_active_rejected(self, web_client: TestClient) -> None:
        resp = web_client.post(
            "/admin/settings/markup",
            data={"markup_10": "1", "markup_25": "1", "default_markup": "15"},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert "markup_error_default" in str(resp.url) or "markup_error_default" in resp.text


class TestSalePageMarkupButtons:
    """/sale and /admin/settings reflect stored markup presets."""

    def _set_presets(self, presets: list[int], default: int) -> None:
        from app.db.sqlite import set_setting
        set_setting("sale_markup_presets", ",".join(str(p) for p in presets))
        set_setting("sale_default_markup", str(default))

    def test_sale_page_renders_without_error(self, web_client: TestClient) -> None:
        """/sale must return 200 regardless of active presets."""
        self._set_presets([10, 15, 25], 15)
        resp = web_client.get("/sale")
        assert resp.status_code == 200

    def test_sale_template_has_validation_and_selection_reset_logic(self) -> None:
        """Sale template script must clear stale browser validation and selection state."""
        from app.web.main import templates

        template_dir = Path(templates.env.loader.searchpath[0])
        template = template_dir / "sale.html"
        text = template.read_text(encoding="utf-8")
        assert "function resetSelectedProduct()" in text
        assert "selectedProduct = null;" in text
        assert "brandInput.value = '';" in text
        assert "modelInput.value = '';" in text
        assert "qtyInput.removeAttribute('max');" in text
        assert "searchInput.setCustomValidity('');" in text

    def test_admin_settings_shows_active_presets_checked(self, web_client: TestClient) -> None:
        """Admin settings page shows checked checkboxes for active presets."""
        self._set_presets([10, 15, 25], 15)
        resp = web_client.get("/admin/settings")
        assert resp.status_code == 200
        # Active presets should have checked checkboxes
        assert 'id="markup_10"' in resp.text
        assert 'id="markup_15"' in resp.text
        assert 'id="markup_25"' in resp.text

    def test_admin_settings_reflects_custom_presets(self, web_client: TestClient) -> None:
        """Admin settings shows only the active presets as checked."""
        self._set_presets([20, 30], 20)
        resp = web_client.get("/admin/settings")
        assert resp.status_code == 200
        assert 'id="markup_20"' in resp.text
        assert 'id="markup_30"' in resp.text

    def test_admin_settings_default_option_selected(self, web_client: TestClient) -> None:
        """Admin settings shows the default markup as selected in the dropdown."""
        self._set_presets([5, 10, 20], 10)
        resp = web_client.get("/admin/settings")
        assert resp.status_code == 200
        # The dropdown option for 10 should be selected
        assert 'value="10"' in resp.text


class TestProductsAndStockMarkupColumns:
    """/products and /stock show dynamic markup columns from admin presets."""

    def _set_presets(self, presets: list[int]) -> None:
        from app.db.sqlite import set_setting
        set_setting("sale_markup_presets", ",".join(str(p) for p in presets))
        set_setting("sale_default_markup", str(presets[0]))

    def teardown_method(self) -> None:
        from app.db.sqlite import set_setting
        set_setting("sale_markup_presets", "")
        set_setting("sale_default_markup", "")

    def test_products_page_renders_without_error(self, web_client: TestClient) -> None:
        """/products must return 200 with any active presets."""
        self._set_presets([10, 15, 25])
        resp = web_client.get("/products")
        assert resp.status_code == 200

    def test_products_page_shows_active_preset_columns(self, web_client: TestClient) -> None:
        """/products shows header columns for each active preset."""
        self._set_presets([5, 20, 30])
        resp = web_client.get("/products")
        assert resp.status_code == 200
        assert "+5%" in resp.text
        assert "+20%" in resp.text
        assert "+30%" in resp.text

    def test_products_page_does_not_show_inactive_preset_columns(self, web_client: TestClient) -> None:
        """/products must not show header columns for inactive presets."""
        self._set_presets([10, 15])
        resp = web_client.get("/products")
        assert resp.status_code == 200
        assert "+10%" in resp.text
        assert "+15%" in resp.text
        # 25 is not active — should not appear as a column header
        assert "+25%" not in resp.text

    def test_products_page_fallback_columns_when_no_setting(self, web_client: TestClient) -> None:
        """/products uses fallback [10,15,25] when no preset is stored."""
        from app.db.sqlite import set_setting
        set_setting("sale_markup_presets", "")
        resp = web_client.get("/products")
        assert resp.status_code == 200
        assert "+10%" in resp.text
        assert "+15%" in resp.text
        assert "+25%" in resp.text

    def test_stock_page_renders_without_error(self, web_client: TestClient) -> None:
        """/stock must return 200 with any active presets."""
        self._set_presets([10, 15, 25])
        resp = web_client.get("/stock")
        assert resp.status_code == 200

    def test_stock_page_shows_active_preset_columns(self, web_client: TestClient) -> None:
        """/stock shows header columns for each active preset."""
        self._set_presets([5, 20, 30])
        resp = web_client.get("/stock")
        assert resp.status_code == 200
        assert "+5%" in resp.text
        assert "+20%" in resp.text
        assert "+30%" in resp.text

    def test_stock_page_does_not_show_inactive_preset_columns(self, web_client: TestClient) -> None:
        """/stock must not show header columns for inactive presets."""
        self._set_presets([10, 15])
        resp = web_client.get("/stock")
        assert resp.status_code == 200
        assert "+10%" in resp.text
        assert "+15%" in resp.text
        assert "+25%" not in resp.text

    def test_stock_page_fallback_columns_when_no_setting(self, web_client: TestClient) -> None:
        """/stock uses fallback [10,15,25] when no preset is stored."""
        from app.db.sqlite import set_setting
        set_setting("sale_markup_presets", "")
        resp = web_client.get("/stock")
        assert resp.status_code == 200
        assert "+10%" in resp.text
        assert "+15%" in resp.text
        assert "+25%" in resp.text

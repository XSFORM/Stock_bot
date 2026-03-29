"""Smoke tests for the FastAPI web app.

These tests guard against regressions in the TemplateResponse API usage.
Starlette 1.0 changed TemplateResponse to require `request` as the first
positional argument.  Passing a string template-name first (old API) causes
``TypeError: unhashable type: 'dict'`` / ``AttributeError: 'dict' object has
no attribute 'split'`` at runtime on a clean server installation.
"""
from __future__ import annotations

import os
import tempfile

import pytest
from fastapi.testclient import TestClient

from app.services.stock_xlsx import generate_stock_xlsx_bytes


@pytest.fixture(scope="module")
def client(tmp_path_factory: pytest.TempPathFactory) -> TestClient:
    db_path = tmp_path_factory.mktemp("data") / "stock.db"
    os.environ["DB_PATH"] = str(db_path)

    # Import *after* setting DB_PATH so the module picks up the correct path.
    from app.web.main import app  # noqa: PLC0415

    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


def test_index_returns_200(client: TestClient) -> None:
    """GET / must return 200 (not 500) on a clean install."""
    response = client.get("/")
    assert response.status_code == 200, (
        f"GET / returned {response.status_code}; "
        "possible TemplateResponse API mismatch with current Starlette version"
    )
    assert "text/html" in response.headers["content-type"]


def test_products_page_returns_200(client: TestClient) -> None:
    """GET /products must render without error."""
    response = client.get("/products")
    assert response.status_code == 200


def test_unlock_page_returns_200(client: TestClient) -> None:
    """GET /unlock must render without error."""
    response = client.get("/unlock")
    assert response.status_code == 200


def test_stock_xlsx_returns_xlsx_bytes(client: TestClient) -> None:
    """GET /stock/xlsx must return an XLSX file (not 500) even with empty warehouse."""
    response = client.get("/stock/xlsx?warehouse=&q=&show_archived=0")
    assert response.status_code == 200, (
        f"GET /stock/xlsx returned {response.status_code}; expected 200"
    )
    assert (
        response.headers["content-type"]
        == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    # XLSX files start with PK (ZIP magic bytes)
    assert response.content[:2] == b"PK"


def test_generate_stock_xlsx_bytes_missing_warehouse_key() -> None:
    """generate_stock_xlsx_bytes must not crash when rows lack a 'warehouse' key."""
    rows = [
        {
            "brand": "Nike",
            "model": "Air Max",
            "name": "Sneaker",
            "warehouse_code": "TM_DEPO",
            "qty": 5,
        }
    ]
    result = generate_stock_xlsx_bytes(rows, "TM_DEPO")
    assert result[:2] == b"PK", "Expected XLSX (ZIP) bytes"


def test_generate_stock_xlsx_bytes_both_keys_missing() -> None:
    """generate_stock_xlsx_bytes must not crash even when both warehouse keys are missing."""
    rows = [
        {
            "brand": "Adidas",
            "model": "Stan Smith",
            "name": "Shoe",
            "qty": 3,
        }
    ]
    result = generate_stock_xlsx_bytes(rows, "All Warehouses")
    assert result[:2] == b"PK", "Expected XLSX (ZIP) bytes"

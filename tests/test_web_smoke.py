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
from app.services.invoice_xlsx import generate_invoice_xlsx_bytes
from app.services.return_xlsx import generate_return_xlsx_bytes
from app.services.receive_xlsx import generate_receive_xlsx_bytes
from app.utils.money import calc_line_total


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


def test_generate_invoice_xlsx_bytes_includes_barcode() -> None:
    """generate_invoice_xlsx_bytes must include a Barcode column and not crash."""
    import openpyxl, io

    invoice = {
        "number": 1,
        "client": "Test Client",
        "created_at": "2024-01-01T10:00:00",
        "total": 200.0,
    }
    items = [
        {
            "brand": "Nike",
            "model": "Air Max",
            "name": "Sneaker",
            "barcode": "1234567890",
            "qty": 2,
            "unit_price": 50.0,
            "total": 100.0,
        },
        {
            "brand": "Adidas",
            "model": "Stan Smith",
            "name": "Shoe",
            "barcode": None,
            "qty": 2,
            "unit_price": 50.0,
            "total": 100.0,
        },
    ]
    result = generate_invoice_xlsx_bytes(invoice, items)
    assert result[:2] == b"PK", "Expected XLSX (ZIP) bytes"

    wb = openpyxl.load_workbook(io.BytesIO(result))
    ws = wb.active
    # Header row is row 5; Barcode is column D (4)
    assert ws.cell(row=5, column=4).value == "Barcode"
    # First data row barcode value
    assert ws.cell(row=6, column=4).value == "1234567890"
    # Second data row barcode is empty (None → empty cell)
    assert not ws.cell(row=7, column=4).value


def test_generate_invoice_xlsx_bytes_missing_barcode_no_crash() -> None:
    """generate_invoice_xlsx_bytes must not crash when items have no barcode key."""
    invoice = {
        "number": 2,
        "client": "Client B",
        "created_at": "2024-02-01T10:00:00",
        "total": 50.0,
    }
    items = [
        {
            "brand": "Puma",
            "model": "RS-X",
            "name": "Sneaker",
            # No "barcode" key at all
            "qty": 1,
            "unit_price": 50.0,
            "total": 50.0,
        },
    ]
    result = generate_invoice_xlsx_bytes(invoice, items)
    assert result[:2] == b"PK", "Expected XLSX (ZIP) bytes"


def test_calc_line_total_uses_displayed_unit_price() -> None:
    assert calc_line_total(8.3033, 3) == pytest.approx(24.90)


def test_generate_invoice_xlsx_bytes_rounds_like_display_price() -> None:
    import io
    import openpyxl

    invoice = {"number": 3, "client": "Client C", "created_at": "2024-03-01T10:00:00", "total": 0}
    items = [
        {
            "brand": "Nike",
            "model": "Air",
            "name": "Sneaker",
            "barcode": "111",
            "qty": 3,
            "unit_price": 8.3033,
            "total": 24.91,
        }
    ]
    wb = openpyxl.load_workbook(io.BytesIO(generate_invoice_xlsx_bytes(invoice, items)))
    ws = wb.active
    assert ws.cell(row=6, column=6).value == pytest.approx(8.30)
    assert ws.cell(row=6, column=7).value == pytest.approx(24.90)
    assert ws.cell(row=7, column=7).value == pytest.approx(24.90)


def test_generate_return_xlsx_bytes_rounds_like_display_price() -> None:
    import io
    import openpyxl

    invoice = {
        "number": 7,
        "client": "Client D",
        "created_at": "2024-03-01T10:00:00",
        "warehouse_code": "1416_SHOP",
        "total": 0,
    }
    items = [
        {
            "brand": "Puma",
            "model": "X",
            "name": "Pair",
            "barcode": "222",
            "qty": 3,
            "unit_price": 8.3033,
            "total": 24.91,
        }
    ]
    wb = openpyxl.load_workbook(io.BytesIO(generate_return_xlsx_bytes(invoice, items)))
    ws = wb.active
    assert ws.cell(row=7, column=6).value == pytest.approx(8.30)
    assert ws.cell(row=7, column=7).value == pytest.approx(24.90)
    assert ws.cell(row=8, column=7).value == pytest.approx(24.90)


def test_generate_receive_xlsx_bytes_rounds_like_display_price() -> None:
    import io
    import openpyxl

    invoice = {
        "number": 9,
        "supplier": "Supplier X",
        "created_at": "2024-03-01T10:00:00",
        "destination_warehouse": "TM_DEPO",
        "total": 0,
    }
    items = [
        {
            "brand": "Asics",
            "model": "GEL",
            "name": "Runner",
            "barcode": "333",
            "qty": 3,
            "purchase_price": 8.3033,
            "total": 24.91,
        }
    ]
    wb = openpyxl.load_workbook(io.BytesIO(generate_receive_xlsx_bytes(invoice, items)))
    ws = wb.active
    assert ws.cell(row=7, column=6).value == pytest.approx(8.30)
    assert ws.cell(row=7, column=7).value == pytest.approx(24.90)
    assert ws.cell(row=8, column=7).value == pytest.approx(24.90)

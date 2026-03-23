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

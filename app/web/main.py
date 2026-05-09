from __future__ import annotations

import hashlib
import logging
import os
import secrets
import shutil
import tempfile
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from datetime import date
from typing import Any, List, Optional
from urllib.parse import quote

from fastapi import FastAPI, File, Form, Request, Response, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
import jinja2
from fastapi.templating import Jinja2Templates
from fastapi.responses import JSONResponse

from app.constants import WAREHOUSES, RECEIVE_SOURCES
from app.db.sqlite import (
    init_db,
    list_products,
    get_stock,
    receive_stock,
    receive_stock_by_product_id,
    add_or_get_product_id,
    move_stock,
    move_stock_by_product_id,
    move_all,
    cart_finish,
    list_brands,
    add_brand,
    list_brand_model_prefixes,
    add_brand_model_prefix,
    update_brand_model_prefix,
    delete_brand_model_prefix,
    # clients
    list_clients,
    add_client,
    get_client,
    update_client,
    set_client_archived,
    add_client_adjustment,
    add_client_debt,
    get_client_balance,
    list_clients_with_balance,
    get_client_history,
    # suppliers
    list_suppliers,
    add_supplier,
    get_supplier,
    update_supplier,
    set_supplier_archived,
    add_supplier_adjustment,
    add_supplier_debt,
    get_supplier_balance,
    list_suppliers_with_balance,
    get_supplier_history,
    # sale by id
    get_open_cart,
    cart_start_by_id,
    cart_add_by_id,
    cart_show_by_id,
    cart_finish_by_id,
    cart_add_by_cart_id,
    cart_show_by_cart_id,
    cart_finish_by_cart_id_shop1416,
    cart_add_free_item,
    # new
    search_products,
    search_stock,
    get_cart_items_list,
    cancel_cart,
    update_cart_item,
    delete_cart_item,
    get_invoice_by_number,
    get_invoice_items_by_number,
    cart_set_client,
    # receive invoices
    receive_invoice_get_open,
    receive_invoice_start,
    receive_invoice_cancel,
    receive_item_add,
    receive_item_update,
    receive_item_delete,
    receive_invoice_finish,
    receive_invoice_get,
    receive_invoice_get_items,
    add_product_simple,
    update_product_wh_price,
    update_product_full,
    # NEW: warehouse management
    list_warehouses,
    add_warehouse,
    # invoices listing
    list_sale_invoices_done,
    list_receive_invoices_done,
    list_history,
    list_history_by_product,
    # stock qty api
    get_stock_qty,
    # archive
    set_product_archived,
    # return invoices
    return_invoice_get_open,
    return_invoice_start,
    return_invoice_cancel,
    return_item_add,
    return_item_update,
    return_item_delete,
    return_invoice_finish,
    return_invoice_get,
    return_invoice_get_items,
    list_return_invoices_done,
    get_last_sale_price,
    get_total_clients_debt,
    get_total_suppliers_debt,
    get_total_stock_value,
    # invoice editing
    get_sale_invoice_full,
    get_sale_invoice_items_full,
    update_sale_invoice,
    delete_sale_invoice,
    get_receive_invoice_items_for_edit,
    update_receive_invoice,
    get_return_invoice_items_for_edit,
    update_return_invoice,
)
from app.services.invoice_pdf import generate_invoice_pdf
from app.services.invoice_xlsx import generate_invoice_xlsx, generate_invoice_xlsx_bytes
from app.services.receive_xlsx import generate_receive_xlsx_bytes
from app.services.return_xlsx import generate_return_xlsx_bytes
from app.services.stock_xlsx import generate_stock_xlsx_bytes
from app.services.backup import make_backup, INVOICES_DIR, BACKUPS_DIR
from app.i18n import get_translations, SUPPORTED_LANGS
from app.utils.money import calc_document_total
from app.db.sqlite import (
    DB_PATH,
    get_setting,
    set_setting,
    get_sale_markup_presets,
    get_sale_default_markup,
    _ALLOWED_MARKUPS,
    create_session,
    is_valid_session,
    delete_session,
    delete_all_sessions,
    purge_expired_sessions,
    # Pocket Price tokens
    create_price_token,
    list_price_tokens,
    revoke_price_token,
    delete_price_token,
    validate_price_token,
    touch_price_token,
    bind_price_token_device,
    set_price_token_mode,
    set_price_token_show_qty,
    set_price_token_show_buy_price,
    search_products_for_price,
    # Mobile barcode scan
    get_product_by_barcode_for_scan,
    create_product_with_barcode,
    update_product_purchase_price,
    # Pocket Catalog tokens
    create_catalog_token,
    list_catalog_tokens,
    revoke_catalog_token,
    delete_catalog_token,
    validate_catalog_token,
    touch_catalog_token,
    bind_catalog_token_device,
)


BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

logger = logging.getLogger(__name__)

app = FastAPI(title="Hasapcy")

# Disable Jinja2 template caching (cache_size=0) to avoid
# "TypeError: unhashable type: 'dict'" errors that occur when Starlette
# passes dict globals as part of the cache key on clean installs.
templates = Jinja2Templates(
    env=jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=jinja2.select_autoescape(),
        cache_size=0,
        auto_reload=True,
    )
)

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ──────────────────────────────────────────────────────────────────────────────
# Site-lock helpers
# ──────────────────────────────────────────────────────────────────────────────

_HASH_ITERS = 260_000  # PBKDF2 iteration count
_DOWNLOAD_ALLOWED_DIRS = {
    "invoices": INVOICES_DIR,
    "backups": BACKUPS_DIR,
}
_ALLOWED_BG_UPLOAD_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".gif")


def _hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), _HASH_ITERS)
    return f"{salt}${dk.hex()}"


def _verify_password(password: str, stored_hash: str) -> bool:
    try:
        salt, dk_hex = stored_hash.split("$", 1)
    except ValueError:
        return False
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), _HASH_ITERS)
    return secrets.compare_digest(dk.hex(), dk_hex)


def _session_hours() -> int:
    try:
        return max(1, int(get_setting("site_lock_session_hours", "24")))
    except (ValueError, TypeError):
        return 24


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        "site_session",
        token,
        max_age=_session_hours() * 3600,
        httponly=True,
        samesite="lax",
    )


def _require_admin_session(request: Request):
    if get_setting("admin_lock_enabled", "1") != "1":
        return None
    # Reuse site-lock session as the minimal admin check.
    if not get_setting("site_lock_hash", ""):
        return None
    token = request.cookies.get("site_session", "")
    if token and is_valid_session(token):
        return None
    next_url = quote(request.url.path, safe="")
    return RedirectResponse(url=f"/unlock?next={next_url}", status_code=303)


def _download_error(request: Request, status_code: int, error: str):
    if "application/json" in request.headers.get("accept", ""):
        return JSONResponse({"error": error}, status_code=status_code)
    return RedirectResponse(url=f"/invoices?msg={quote(error, safe='')}", status_code=303)


def _resolve_download_path(raw_path: str) -> tuple[Optional[Path], Optional[str]]:
    candidate_raw = (raw_path or "").strip()
    if not candidate_raw:
        return None, "download_not_found"
    candidate = Path(candidate_raw)
    if candidate.is_absolute():
        return None, "download_forbidden"
    if ".." in candidate.parts:
        return None, "download_forbidden"
    parts = candidate.parts
    if not parts:
        return None, "download_not_found"
    root = _DOWNLOAD_ALLOWED_DIRS.get(parts[0])
    if root is None:
        return None, "download_forbidden"
    target = (root / Path(*parts[1:])).resolve()
    root_resolved = root.resolve()
    if target != root_resolved and root_resolved not in target.parents:
        return None, "download_forbidden"
    if not target.exists() or not target.is_file():
        return None, "download_not_found"
    return target, None


def _to_download_ref(file_path: str) -> str:
    candidate_raw = (file_path or "").strip()
    if not candidate_raw:
        return ""
    try:
        resolved = Path(candidate_raw).resolve()
    except OSError:
        return ""
    for prefix, root in _DOWNLOAD_ALLOWED_DIRS.items():
        root_resolved = root.resolve()
        if resolved == root_resolved or root_resolved in resolved.parents:
            rel = resolved.relative_to(root_resolved).as_posix()
            return f"{prefix}/{rel}" if rel else prefix
    return ""


def _get_ui_lang(request: Request) -> str:
    lang = request.cookies.get("ui_lang", "")
    if lang not in SUPPORTED_LANGS:
        lang = get_setting("default_lang", "en")
    if lang not in SUPPORTED_LANGS:
        lang = "en"
    return lang


def _get_ui_theme(request: Request) -> str:
    theme = request.cookies.get("ui_theme", "")
    if theme not in ("light", "dark", "system"):
        theme = get_setting("default_theme", "system")
    if theme not in ("light", "dark", "system"):
        theme = "system"
    return theme


# ──────────────────────────────────────────────────────────────────────────────
# Site-lock middleware
# ──────────────────────────────────────────────────────────────────────────────

_LOCK_BYPASS_PREFIXES = ("/static", "/unlock", "/api/", "/price", "/catalog")


@app.middleware("http")
async def site_lock_middleware(request: Request, call_next):
    path = request.url.path
    if any(path.startswith(p) for p in _LOCK_BYPASS_PREFIXES):
        return await call_next(request)
    if get_setting("site_lock_enabled", "0") != "1":
        return await call_next(request)
    token = request.cookies.get("site_session", "")
    if token and is_valid_session(token):
        return await call_next(request)
    from urllib.parse import quote
    next_url = quote(path, safe="")
    return RedirectResponse(url=f"/unlock?next={next_url}", status_code=303)


@app.on_event("startup")
def _startup() -> None:
    init_db()
    # Bootstrap site-lock password from environment if not already set.
    env_pw = os.environ.get("SITE_LOCK_PASSWORD", "").strip()
    if env_pw and not get_setting("site_lock_hash"):
        set_setting("site_lock_hash", _hash_password(env_pw))
    purge_expired_sessions()


def _render(request: Request, name: str, ctx: dict[str, Any]) -> HTMLResponse:
    wh_list = list_warehouses()
    wh_codes = [w["code"] for w in wh_list]
    wh_labels = {w["code"]: w["title"] for w in wh_list}
    lang = _get_ui_lang(request)
    theme = _get_ui_theme(request)
    base = {
        "request": request,
        "warehouses": wh_codes,
        "sources": sorted(RECEIVE_SOURCES.keys()),
        "source_labels": RECEIVE_SOURCES,
        "warehouse_labels": wh_labels,
        "ui_lang": lang,
        "ui_theme": theme,
        "supported_langs": SUPPORTED_LANGS,
        "t": get_translations(lang),
        "site_lock_enabled": get_setting("site_lock_enabled", "0") == "1",
        "bg_enabled": get_setting("bg_enabled", "1") == "1",
        "bg_size": get_setting("bg_size", "cover"),
        "bg_overlay": get_setting("bg_overlay", "25"),
        "bg_version": _static_asset_version("bg.jpg"),
        "nav_total_debt_usd": get_total_clients_debt(),
        "nav_total_suppliers_debt_usd": get_total_suppliers_debt(),
        "nav_stock_value_usd": get_total_stock_value(),
    }
    base.update(ctx)
    return templates.TemplateResponse(request, name, base)


def _static_asset_version(name: str) -> str:
    path = STATIC_DIR / name
    try:
        return str(path.stat().st_mtime_ns)
    except OSError:
        return "0"


@app.get("/api/brand-prefixes")
def api_brand_prefixes(brand: str):
    prefixes = list_brand_model_prefixes(brand)
    # front will display with dash: tf -> "tf-"
    return JSONResponse({"brand": brand, "prefixes": prefixes})


@app.get("/api/stock-search")
def api_stock_search(warehouse: str = "1416_SHOP", q: str = ""):
    """Search stock for a specific warehouse. Returns up to 30 items."""
    items = search_stock(warehouse, q, limit=30)
    return JSONResponse({"results": items})


@app.get("/api/products-search")
def api_products_search(q: str = "", limit: int = 30, warehouse: str = ""):
    """Search all products catalog by brand/model/name. Returns up to 30 items.

    Optional ``warehouse`` param augments results with qty_in_wh for that warehouse
    and sorts products available there to the top.
    """
    items = search_products(q, limit=min(int(limit), 30), warehouse=warehouse)
    return JSONResponse({"results": items})


@app.get("/api/last-sale-price")
def api_last_sale_price(product_id: int, client_id: Optional[int] = None):
    """Return the last sale unit_price for a product (optionally for a specific client)."""
    price = get_last_sale_price(int(product_id), int(client_id) if client_id else None)
    return JSONResponse({"product_id": product_id, "last_sale_price": price})


@app.get("/api/stock")
def api_stock(product_id: int, warehouse_id: str):
    """Return available qty for a product in a given warehouse."""
    qty = get_stock_qty(warehouse_id, product_id)
    return JSONResponse({"product_id": product_id, "warehouse_id": warehouse_id, "qty": qty})


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return _render(request, "index.html", {})


# ---------------- products ----------------

@app.get("/products", response_class=HTMLResponse)
def products(request: Request, show_archived: int = 0, highlight: int = 0, q: str = ""):
    rows = list_products(include_archived=bool(show_archived), search=q if q else None)
    brands = list_brands()
    return _render(request, "products.html", {"products": rows, "brands": brands, "show_archived": show_archived, "highlight": highlight, "search_q": q, "markup_presets": get_sale_markup_presets()})


@app.post("/products/add")
def products_add(
    brand: str = Form(...),
    model: str = Form(...),
    name: str = Form(...),
    wh_price: float = Form(...),
    barcode: str = Form(""),
    create_receive: str = Form(""),
    source: str = Form("CHINA"),
    warehouse: str = Form("TM_DEPO"),
    qty: Optional[float] = Form(None),
):
    try:
        ok, err, product_id = add_product_simple(brand, model, name, barcode, note="", wh_price=float(wh_price))
        if not ok:
            return RedirectResponse(url=f"/products?msg=product_error:{err}", status_code=303)

        if create_receive and qty is not None:
            ok2, err2 = receive_stock_by_product_id(warehouse, product_id, float(qty), source=source)
            if not ok2:
                return RedirectResponse(url=f"/products?msg=received:{err2}", status_code=303)
            return RedirectResponse(url=f"/products?msg=created+received&highlight={product_id}", status_code=303)

        return RedirectResponse(url=f"/products?msg=created&highlight={product_id}", status_code=303)
    except Exception as e:
        # вместо черного экрана
        return RedirectResponse(url=f"/products?msg=error:{e}", status_code=303)


@app.post("/products/{product_id}/edit")
def products_edit(
    product_id: int,
    barcode: str = Form(""),
    wh_price: float = Form(...),
    model: str = Form(""),
    name: str = Form(""),
    brand: str = Form(""),
    show_archived: int = Form(0),
):
    ok, err = update_product_full(product_id, barcode, wh_price, model, name, brand)
    msg = "product_updated" if ok else f"error:{err}"
    return RedirectResponse(url=f"/products?msg={msg}&show_archived={show_archived}", status_code=303)


@app.post("/products/{product_id}/archive")
def product_archive(product_id: int, show_archived: int = Form(0)):
    ok, err = set_product_archived(int(product_id), 1)
    msg = "archived" if ok else f"archive_error:{err}"
    return RedirectResponse(url=f"/products?msg={msg}&show_archived={show_archived}", status_code=303)


@app.post("/products/{product_id}/unarchive")
def product_unarchive(product_id: int, show_archived: int = Form(0)):
    ok, err = set_product_archived(int(product_id), 0)
    msg = "unarchived" if ok else f"unarchive_error:{err}"
    return RedirectResponse(url=f"/products?msg={msg}&show_archived={show_archived}", status_code=303)


# ---------------- mobile barcode scan ----------------

@app.get("/m/scan", response_class=HTMLResponse)
def mobile_scan(request: Request):
    brands = list_brands()
    prefix_map = {b: list_brand_model_prefixes(b) for b in brands}
    return _render(request, "mobile_scan.html", {"brands": brands, "prefix_map": prefix_map})


@app.get("/api/products/by-barcode")
def api_products_by_barcode(barcode: str = ""):
    barcode = barcode.strip()
    if not barcode:
        return JSONResponse({"found": False, "product": None, "error": "barcode_empty"})
    product = get_product_by_barcode_for_scan(barcode)
    if product:
        return JSONResponse({"found": True, "product": product})
    return JSONResponse({"found": False, "product": None})


@app.post("/api/products/upsert-by-barcode")
async def api_products_upsert_by_barcode(request: Request):
    try:
        ct = request.headers.get("content-type", "")
        if "application/json" in ct:
            body = await request.json()
        else:
            form = await request.form()
            body = dict(form)
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    barcode = str(body.get("barcode", "")).strip()
    if not barcode:
        return JSONResponse({"ok": False, "error": "barcode_empty"}, status_code=400)

    existing = get_product_by_barcode_for_scan(barcode)

    if existing:
        # Barcode exists – update purchase_price if provided
        raw_price = body.get("purchase_price", "")
        if raw_price not in (None, ""):
            try:
                price = round(float(raw_price), 2)
            except (ValueError, TypeError):
                return JSONResponse({"ok": False, "error": "invalid_price"}, status_code=400)
            ok, err = update_product_purchase_price(existing["id"], price)
            if not ok:
                return JSONResponse({"ok": False, "error": err}, status_code=500)
            existing["purchase_price"] = price
        return JSONResponse({"ok": True, "created": False, "product": existing})
    else:
        # Barcode not found – create new product
        brand = str(body.get("brand", "")).strip()
        model = str(body.get("model", "")).strip()
        name = str(body.get("name", "")).strip()
        raw_price = body.get("purchase_price", "0")
        if not brand:
            return JSONResponse({"ok": False, "error": "brand_required"}, status_code=400)
        if not model:
            return JSONResponse({"ok": False, "error": "model_required"}, status_code=400)
        if not name:
            return JSONResponse({"ok": False, "error": "name_required"}, status_code=400)
        try:
            price = round(float(raw_price or 0), 2)
        except (ValueError, TypeError):
            return JSONResponse({"ok": False, "error": "invalid_price"}, status_code=400)

        ok, err, product_id = create_product_with_barcode(brand, model, name, price, barcode)
        if not ok:
            return JSONResponse({"ok": False, "error": err}, status_code=400)
        product = get_product_by_barcode_for_scan(barcode)
        return JSONResponse({"ok": True, "created": True, "product": product})


# ---------------- stock ----------------

@app.get("/stock", response_class=HTMLResponse)
def stock_get(request: Request, warehouse: str = "", q: str = "", msg: str = "", show_archived: int = 0, sort_by: str = "qty_asc"):
    rows = get_stock(warehouse if warehouse else None, q if q else None, include_archived=bool(show_archived), sort_by=sort_by)
    return _render(
        request,
        "stock.html",
        {
            "rows": rows,
            "selected_warehouse": (warehouse or "").strip().upper(),
            "search_q": q or "",
            "msg": msg,
            "show_archived": show_archived,
            "sort_by": sort_by,
            "markup_presets": get_sale_markup_presets(),
        },
    )


@app.post("/stock/product/wh_price")
def stock_update_wh_price(
    product_id: int = Form(...),
    wh_price: float = Form(...),
    warehouse: str = Form(""),
    q: str = Form(""),
    show_archived: int = Form(0),
    sort_by: str = Form("qty_asc"),
):
    ok, err = update_product_wh_price(product_id, wh_price)
    msg = "wh_price_updated" if ok else f"error:{err}"
    return RedirectResponse(url=f"/stock?warehouse={warehouse}&q={q}&msg={msg}&show_archived={show_archived}&sort_by={sort_by}", status_code=303)


@app.post("/stock/product/edit")
def stock_edit_product(
    product_id: int = Form(...),
    barcode: str = Form(""),
    wh_price: float = Form(...),
    model: str = Form(""),
    name: str = Form(""),
    warehouse: str = Form(""),
    q: str = Form(""),
    show_archived: int = Form(0),
    sort_by: str = Form("qty_asc"),
):
    ok, err = update_product_full(product_id, barcode, wh_price, model, name)
    msg = "product_updated" if ok else f"error:{err}"
    return RedirectResponse(url=f"/stock?warehouse={warehouse}&q={q}&msg={msg}&show_archived={show_archived}&sort_by={sort_by}", status_code=303)


@app.post("/stock/product/{product_id}/archive")
def stock_product_archive(product_id: int, warehouse: str = Form(""), q: str = Form(""), show_archived: int = Form(0), sort_by: str = Form("qty_asc")):
    ok, err = set_product_archived(product_id, 1)
    msg = "archived" if ok else f"error:{err}"
    return RedirectResponse(url=f"/stock?warehouse={warehouse}&q={q}&msg={msg}&show_archived={show_archived}&sort_by={sort_by}", status_code=303)


@app.post("/stock/product/{product_id}/unarchive")
def stock_product_unarchive(product_id: int, warehouse: str = Form(""), q: str = Form(""), show_archived: int = Form(0), sort_by: str = Form("qty_asc")):
    ok, err = set_product_archived(product_id, 0)
    msg = "unarchived" if ok else f"error:{err}"
    return RedirectResponse(url=f"/stock?warehouse={warehouse}&q={q}&msg={msg}&show_archived={show_archived}&sort_by={sort_by}", status_code=303)


@app.get("/products/{product_id}/history", response_class=HTMLResponse)
def product_history_get(request: Request, product_id: int):
    prods = list_products(include_archived=True)
    prod = next((p for p in prods if p["id"] == product_id), None)
    if not prod:
        return RedirectResponse(url="/stock?msg=product_not_found", status_code=303)
    events = list_history_by_product(product_id)
    return _render(request, "product_history.html", {"product": prod, "events": events})


@app.get("/stock/xlsx")
def stock_xlsx(warehouse: str = "", q: str = "", show_archived: int = 0, sort_by: str = "qty_asc"):
    rows = get_stock(warehouse if warehouse else None, q if q else None, include_archived=bool(show_archived), sort_by=sort_by)
    wh_key = (warehouse or "").strip().upper()
    today = date.today().isoformat()
    if wh_key == "TM_DEPO":
        label = WAREHOUSES["TM_DEPO"]
        suffix = "depo"
    elif wh_key == "1416_SHOP":
        label = WAREHOUSES["1416_SHOP"]
        suffix = "shop"
    else:
        label = "All Warehouses"
        suffix = "all"
    data = generate_stock_xlsx_bytes(rows, label)
    filename = f"stock_{suffix}_{today}.xlsx"
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------- receive (ERP style) ----------------

@app.get("/receive", response_class=HTMLResponse)
def receive_get(request: Request, msg: str = ""):
    open_inv = receive_invoice_get_open()
    suppliers = list_suppliers()

    inv_items: list = []
    inv_total: float = 0.0
    inv_qty: float = 0.0
    if open_inv:
        inv_items = receive_invoice_get_items(open_inv["id"])
        inv_total = calc_document_total(inv_items, "purchase_price")
        inv_qty = sum(float(i["qty"]) for i in inv_items)

    return _render(
        request,
        "receive.html",
        {
            "message": msg,
            "open_inv": open_inv,
            "inv_items": inv_items,
            "inv_total": inv_total,
            "inv_qty": inv_qty,
            "suppliers": suppliers,
        },
    )


@app.post("/receive/start")
def receive_start(
    supplier_id: int = Form(...),
    destination_warehouse: str = Form(...),
    destination_new_code: str = Form(""),
    destination_new_name: str = Form(""),
    note: str = Form(""),
):
    if destination_warehouse == "__new__":
        # Normalize code before creating so we have the canonical form for invoice_start
        destination_warehouse = (destination_new_code or "").strip().upper()
        ok, err = add_warehouse(destination_new_code, destination_new_name)
        if not ok:
            return RedirectResponse(url=f"/receive?msg=destination_error:{err}", status_code=303)
    ok, err, inv_id = receive_invoice_start(
        "",
        destination_warehouse,
        note,
        supplier_id=int(supplier_id),
    )
    if not ok:
        return RedirectResponse(url=f"/receive?msg={err}", status_code=303)
    return RedirectResponse(url="/receive?msg=receive_started", status_code=303)


@app.post("/receive/cancel")
def receive_cancel(invoice_id: int = Form(...)):
    ok, err = receive_invoice_cancel(int(invoice_id))
    msg = "receive_cancelled" if ok else f"cancel_error:{err}"
    return RedirectResponse(url=f"/receive?msg={msg}", status_code=303)


@app.post("/receive/item/add")
def receive_item_add_post(
    invoice_id: int = Form(...),
    product_id: int = Form(...),
    qty: float = Form(...),
    purchase_price: float = Form(...),
):
    ok, err = receive_item_add(int(invoice_id), int(product_id), float(qty), float(purchase_price))
    msg = "item_added" if ok else f"add_error:{err}"
    return RedirectResponse(url=f"/receive?msg={msg}", status_code=303)


@app.post("/receive/item/new")
def receive_item_new_post(
    invoice_id: int = Form(...),
    brand: str = Form(...),
    model: str = Form(...),
    name: str = Form(...),
    barcode: str = Form(""),
    product_note: str = Form(""),
    wh_price: float = Form(...),
    qty: float = Form(...),
    purchase_price: float = Form(...),
    update_wh_from_purchase: int = Form(0),
):
    if wh_price <= 0:
        return RedirectResponse(url="/receive?msg=new_product_error:wh_price_required", status_code=303)
    ok, err, product_id = add_product_simple(brand, model, name, barcode, product_note, wh_price)
    if not ok:
        return RedirectResponse(url=f"/receive?msg=new_product_error:{err}", status_code=303)
    if update_wh_from_purchase and purchase_price > 0:
        ok_wh, err_wh = update_product_wh_price(int(product_id), float(purchase_price))
        if not ok_wh:
            return RedirectResponse(url=f"/receive?msg=wh_price_update_error:{err_wh}", status_code=303)
    ok2, err2 = receive_item_add(int(invoice_id), int(product_id), float(qty), float(purchase_price))
    msg = "new_product_added" if ok2 else f"add_error:{err2}"
    return RedirectResponse(url=f"/receive?msg={msg}", status_code=303)


@app.post("/receive/item/update")
def receive_item_update_post(
    item_id: int = Form(...),
    qty: float = Form(...),
    purchase_price: float = Form(...),
):
    ok, err = receive_item_update(int(item_id), float(qty), float(purchase_price))
    msg = "item_updated" if ok else f"update_error:{err}"
    return RedirectResponse(url=f"/receive?msg={msg}", status_code=303)


@app.post("/receive/item/delete")
def receive_item_delete_post(item_id: int = Form(...)):
    ok, err = receive_item_delete(int(item_id))
    msg = "item_removed" if ok else f"delete_error:{err}"
    return RedirectResponse(url=f"/receive?msg={msg}", status_code=303)


@app.post("/receive/finish")
def receive_finish(invoice_id: int = Form(...)):
    ok, err = receive_invoice_finish(int(invoice_id))
    if not ok:
        return RedirectResponse(url=f"/receive?msg=finish_error:{err}", status_code=303)
    return RedirectResponse(url=f"/receive/done?n={invoice_id}", status_code=303)


@app.get("/receive/done", response_class=HTMLResponse)
def receive_done(request: Request, n: int):
    inv = receive_invoice_get(int(n))
    if not inv:
        return RedirectResponse(url="/receive?msg=invoice_not_found", status_code=303)
    return _render(request, "receive_done.html", {"invoice": inv})


@app.get("/receive/xlsx")
def receive_xlsx(n: int):
    inv = receive_invoice_get(int(n))
    if not inv:
        return RedirectResponse(url="/receive?msg=invoice_not_found", status_code=303)
    items = receive_invoice_get_items(int(n))
    data = generate_receive_xlsx_bytes(inv, items)
    filename = f"receive_{inv['number']:06d}.xlsx"
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/receive/xlsx/view", response_class=HTMLResponse)
def receive_xlsx_view(request: Request, n: int):
    inv = receive_invoice_get(int(n))
    if not inv:
        return RedirectResponse(url="/receive?msg=invoice_not_found", status_code=303)
    items = receive_invoice_get_items(int(n))
    inv_total = calc_document_total(items, "purchase_price")
    inv_qty = sum(float(i["qty"]) for i in items)
    return _render(
        request,
        "receive_xlsx_view.html",
        {"invoice": inv, "items": items, "inv_total": inv_total, "inv_qty": inv_qty},
    )


# ---------------- move ----------------

@app.get("/move", response_class=HTMLResponse)
def move_get(request: Request, msg: str = ""):
    return _render(request, "move.html", {"message": msg})


@app.post("/move")
def move_post(
    src: str = Form(...),
    dst: str = Form(...),
    product_id: int = Form(...),
    qty: float = Form(...),
):
    ok, err = move_stock_by_product_id(src, dst, int(product_id), float(qty))
    msg = "OK" if ok else err
    return RedirectResponse(url=f"/move?msg={msg}", status_code=303)


# ---------------- move all ----------------

@app.get("/move-all", response_class=HTMLResponse)
def move_all_get(request: Request, msg: str = ""):
    return _render(request, "move_all.html", {"message": msg})


@app.post("/move-all")
def move_all_post(
    src: str = Form(...),
    dst: str = Form("SHOP"),
):
    ok, err, moved = move_all(src, dst)
    msg = f"OK moved={moved}" if ok else err
    return RedirectResponse(url=f"/move-all?msg={msg}", status_code=303)


# ---------------- clients --------------------

@app.get("/clients", response_class=HTMLResponse)
def clients_get(request: Request, msg: str = "", show_archived: int = 0, q: str = "", sort_by: str = "name_asc"):
    clients = list_clients_with_balance(include_archived=bool(show_archived))
    # Filter by search query (name, phone, note)
    if q:
        q_lower = q.lower()
        clients = [
            c for c in clients
            if q_lower in (c.get("name") or "").lower()
            or q_lower in (c.get("phone") or "").lower()
            or q_lower in (c.get("note") or "").lower()
        ]
    # Sort
    if sort_by == "name_desc":
        clients = sorted(clients, key=lambda c: (c.get("name") or "").lower(), reverse=True)
    elif sort_by == "debt_desc":
        clients = sorted(clients, key=lambda c: c.get("balance") or 0, reverse=True)
    elif sort_by == "debt_asc":
        clients = sorted(clients, key=lambda c: c.get("balance") or 0)
    else:  # name_asc (default)
        clients = sorted(clients, key=lambda c: (c.get("name") or "").lower())
    return _render(request, "clients.html", {
        "clients": clients,
        "message": msg,
        "show_archived": show_archived,
        "search_q": q,
        "sort_by": sort_by,
    })


@app.post("/clients/add")
def clients_add(
    name: str = Form(...),
    phone: str = Form(""),
    note: str = Form(""),
):
    ok, err = add_client(name, phone, note)
    msg = "OK" if ok else err
    return RedirectResponse(url=f"/clients?msg={msg}", status_code=303)


@app.post("/clients/{client_id}/archive")
def client_archive(client_id: int, show_archived: int = Form(0)):
    ok, err = set_client_archived(int(client_id), 1)
    msg = "archived" if ok else f"archive_error:{err}"
    return RedirectResponse(url=f"/clients?msg={msg}&show_archived={show_archived}", status_code=303)


@app.post("/clients/{client_id}/unarchive")
def client_unarchive(client_id: int, show_archived: int = Form(0)):
    ok, err = set_client_archived(int(client_id), 0)
    msg = "unarchived" if ok else f"unarchive_error:{err}"
    return RedirectResponse(url=f"/clients?msg={msg}&show_archived={show_archived}", status_code=303)


@app.get("/clients/{client_id}/edit", response_class=HTMLResponse)
def client_edit_get(request: Request, client_id: int, msg: str = ""):
    c = get_client(int(client_id))
    if not c:
        return RedirectResponse(url="/clients?msg=client_not_found", status_code=303)
    return _render(request, "client_edit.html", {"client": c, "message": msg})


@app.post("/clients/{client_id}/edit")
def client_edit_post(
    client_id: int,
    name: str = Form(...),
    phone: str = Form(""),
    note: str = Form(""),
):
    ok, err = update_client(int(client_id), name, phone, note)
    msg = "OK" if ok else err
    return RedirectResponse(url=f"/clients/{int(client_id)}/edit?msg={msg}", status_code=303)


@app.post("/clients/{client_id}/adjustment")
def client_adjustment_post(
    client_id: int,
    amount: float = Form(...),
    note: str = Form(""),
    show_archived: int = Form(0),
):
    ok, err = add_client_adjustment(int(client_id), amount, note)
    msg = f"adjustment_ok:{amount:.2f}" if ok else f"adjustment_error:{err}"
    return RedirectResponse(url=f"/clients?msg={quote(msg)}&show_archived={show_archived}", status_code=303)


@app.post("/clients/{client_id}/debt/add")
def client_debt_add_post(
    client_id: int,
    amount: float = Form(...),
    note: str = Form(...),
    show_archived: int = Form(0),
):
    ok, err = add_client_debt(int(client_id), amount, note)
    msg = f"debt_added:{amount:.2f}" if ok else f"debt_add_error:{err}"
    return RedirectResponse(url=f"/clients?msg={quote(msg)}&show_archived={show_archived}", status_code=303)


@app.get("/clients/{client_id}/history", response_class=HTMLResponse)
def client_history_get(request: Request, client_id: int):
    c = get_client(int(client_id))
    if not c:
        return RedirectResponse(url="/clients?msg=client_not_found", status_code=303)
    events = get_client_history(int(client_id))
    balance = get_client_balance(int(client_id))
    return _render(request, "client_history.html", {
        "client": c,
        "events": events,
        "balance": balance,
    })


@app.get("/suppliers", response_class=HTMLResponse)
def suppliers_get(request: Request, msg: str = "", show_archived: int = 0):
    suppliers = list_suppliers_with_balance(include_archived=bool(show_archived))
    return _render(
        request,
        "suppliers.html",
        {"suppliers": suppliers, "message": msg, "show_archived": show_archived},
    )


@app.post("/suppliers/add")
def suppliers_add(
    name: str = Form(...),
    phone: str = Form(""),
    note: str = Form(""),
):
    ok, err = add_supplier(name, phone, note)
    msg = "OK" if ok else err
    return RedirectResponse(url=f"/suppliers?msg={msg}", status_code=303)


@app.post("/suppliers/{supplier_id}/archive")
def supplier_archive(supplier_id: int, show_archived: int = Form(0)):
    ok, err = set_supplier_archived(int(supplier_id), 1)
    msg = "archived" if ok else f"archive_error:{err}"
    return RedirectResponse(url=f"/suppliers?msg={msg}&show_archived={show_archived}", status_code=303)


@app.post("/suppliers/{supplier_id}/unarchive")
def supplier_unarchive(supplier_id: int, show_archived: int = Form(0)):
    ok, err = set_supplier_archived(int(supplier_id), 0)
    msg = "unarchived" if ok else f"unarchive_error:{err}"
    return RedirectResponse(url=f"/suppliers?msg={msg}&show_archived={show_archived}", status_code=303)


@app.get("/suppliers/{supplier_id}/edit", response_class=HTMLResponse)
def supplier_edit_get(request: Request, supplier_id: int, msg: str = ""):
    supplier = get_supplier(int(supplier_id))
    if not supplier:
        return RedirectResponse(url="/suppliers?msg=supplier_not_found", status_code=303)
    return _render(request, "supplier_edit.html", {"supplier": supplier, "message": msg})


@app.post("/suppliers/{supplier_id}/edit")
def supplier_edit_post(
    supplier_id: int,
    name: str = Form(...),
    phone: str = Form(""),
    note: str = Form(""),
):
    ok, err = update_supplier(int(supplier_id), name, phone, note)
    msg = "OK" if ok else err
    return RedirectResponse(url=f"/suppliers/{int(supplier_id)}/edit?msg={msg}", status_code=303)


@app.post("/suppliers/{supplier_id}/adjustment")
def supplier_adjustment_post(
    supplier_id: int,
    amount: float = Form(...),
    note: str = Form(""),
    show_archived: int = Form(0),
):
    ok, err = add_supplier_adjustment(int(supplier_id), amount, note)
    msg = f"adjustment_ok:{amount:.2f}" if ok else f"adjustment_error:{err}"
    return RedirectResponse(url=f"/suppliers?msg={quote(msg)}&show_archived={show_archived}", status_code=303)


@app.post("/suppliers/{supplier_id}/debt/add")
def supplier_debt_add_post(
    supplier_id: int,
    amount: float = Form(...),
    note: str = Form(...),
    show_archived: int = Form(0),
):
    ok, err = add_supplier_debt(int(supplier_id), amount, note)
    msg = f"debt_added:{amount:.2f}" if ok else f"debt_add_error:{err}"
    return RedirectResponse(url=f"/suppliers?msg={quote(msg)}&show_archived={show_archived}", status_code=303)


@app.get("/suppliers/{supplier_id}/history", response_class=HTMLResponse)
def supplier_history_get(request: Request, supplier_id: int):
    supplier = get_supplier(int(supplier_id))
    if not supplier:
        return RedirectResponse(url="/suppliers?msg=supplier_not_found", status_code=303)
    events = get_supplier_history(int(supplier_id))
    balance = get_supplier_balance(int(supplier_id))
    return _render(request, "supplier_history.html", {
        "supplier": supplier,
        "events": events,
        "balance": balance,
    })



@app.get("/sale", response_class=HTMLResponse)
def sale_get(request: Request, msg: str = ""):
    clients = list_clients()
    open_cart = get_open_cart()
    cart_items: list = []
    cart_total: float = 0.0
    cart_qty: float = 0.0
    if open_cart:
        _, cart_items = get_cart_items_list(open_cart["cart_id"])
        cart_total = calc_document_total(cart_items, "unit_price")
        cart_qty = sum(float(i["qty"]) for i in cart_items)
    return _render(
        request,
        "sale.html",
        {
            "message": msg,
            "clients": clients,
            "open_cart": open_cart,
            "cart_items": cart_items,
            "cart_total": cart_total,
            "cart_qty": cart_qty,
            "markup_presets": get_sale_markup_presets(),
            "default_markup": get_sale_default_markup(),
        },
    )


@app.post("/sale/start")
def sale_start(client_id: int = Form(...), warehouse_code: str = Form("1416_SHOP")):
    ok, err, cart_id = cart_start_by_id(int(client_id), warehouse_code)
    if not ok:
        return RedirectResponse(url=f"/sale?msg={err}", status_code=303)
    return RedirectResponse(url="/sale?msg=cart_started", status_code=303)


@app.post("/sale/add")
def sale_add(
    cart_id: int = Form(...),
    brand: str = Form(...),
    model: str = Form(...),
    qty: float = Form(...),
    price_mode: str = Form("wh10"),
    custom_price: Optional[float] = Form(None),
):
    ok, err = cart_add_by_cart_id(int(cart_id), brand, model, float(qty), price_mode, custom_price)
    msg = "OK" if ok else err
    return RedirectResponse(url=f"/sale?msg=add:{msg}", status_code=303)


@app.post("/sale/add-free")
def sale_add_free(
    cart_id: int = Form(...),
    free_name: str = Form(...),
    qty: float = Form(...),
    unit_price: float = Form(...),
):
    ok, err = cart_add_free_item(int(cart_id), free_name.strip(), float(qty), float(unit_price))
    msg = "OK" if ok else err
    return RedirectResponse(url=f"/sale?msg=add:{msg}", status_code=303)


@app.post("/sale/show")
def sale_show(cart_id: int = Form(...)):
    ok, text = cart_show_by_cart_id(int(cart_id))
    if not ok:
        return RedirectResponse(url=f"/sale?msg=show:{text}", status_code=303)
    return RedirectResponse(url=f"/sale?msg=cart:{text}", status_code=303)


@app.post("/sale/finish")
def sale_finish(cart_id: int = Form(...)):
    ok, err, invoice, items = cart_finish_by_cart_id_shop1416(int(cart_id))
    if not ok:
        return RedirectResponse(url=f"/sale?msg=finish:{err}", status_code=303)

    try:
        pdf_path = generate_invoice_pdf(invoice, items)
    except Exception:
        logger.exception("Failed to generate invoice PDF for invoice #%s", invoice.get("number"))
        pdf_path = ""

    try:
        backup_path = make_backup()
    except Exception:
        logger.exception("Failed to create backup after finishing invoice #%s", invoice.get("number"))
        backup_path = ""

    pdf_ref = _to_download_ref(pdf_path)
    backup_ref = _to_download_ref(backup_path)

    return RedirectResponse(
        url=f"/sale/done?pdf={quote(pdf_ref, safe='')}&backup={quote(backup_ref, safe='')}&n={invoice['number']}",
        status_code=303,
    )


@app.post("/sale/cancel")
def sale_cancel(cart_id: int = Form(...)):
    ok, err = cancel_cart(int(cart_id))
    msg = "invoice_cancelled" if ok else f"cancel_error:{err}"
    return RedirectResponse(url=f"/sale?msg={msg}", status_code=303)


@app.post("/sale/client/update")
def sale_client_update(
    cart_id: int = Form(...),
    client_id: int = Form(...),
):
    ok, err = cart_set_client(cart_id, client_id)
    msg = "client_updated" if ok else f"client_update_error:{err}"
    return RedirectResponse(url=f"/sale?msg={msg}", status_code=303)


@app.post("/sale/item/update")
def sale_item_update(
    item_id: int = Form(...),
    qty: float = Form(...),
    unit_price: float = Form(...),
):
    ok, err = update_cart_item(int(item_id), float(qty), float(unit_price))
    msg = "item_updated" if ok else f"update_error:{err}"
    return RedirectResponse(url=f"/sale?msg={msg}", status_code=303)


@app.post("/sale/item/delete")
def sale_item_delete(item_id: int = Form(...)):
    ok, err = delete_cart_item(int(item_id))
    msg = "item_removed" if ok else f"delete_error:{err}"
    return RedirectResponse(url=f"/sale?msg={msg}", status_code=303)


@app.get("/sale/xlsx")
def sale_xlsx(n: int):
    invoice = get_invoice_by_number(int(n))
    if not invoice:
        return RedirectResponse(url="/sale?msg=invoice_not_found", status_code=303)
    items = get_invoice_items_by_number(int(n))
    data = generate_invoice_xlsx_bytes(invoice, items)
    filename = f"invoice_{invoice['number']:06d}.xlsx"
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/sale/xlsx/view", response_class=HTMLResponse)
def sale_xlsx_view(request: Request, n: int):
    invoice = get_invoice_by_number(int(n))
    if not invoice:
        return RedirectResponse(url="/sale?msg=invoice_not_found", status_code=303)
    items = get_invoice_items_by_number(int(n))
    cart_total = calc_document_total(items, "unit_price")
    return _render(
        request,
        "sale_xlsx_view.html",
        {"invoice": invoice, "items": items, "cart_total": cart_total},
    )


@app.get("/sale/done", response_class=HTMLResponse)
def sale_done(request: Request, pdf: str, backup: str, n: str = ""):
    return _render(
        request,
        "sale_done.html",
        {"pdf": pdf, "backup": backup, "invoice_number": n},
    )


@app.get("/download", response_class=FileResponse)
def download(request: Request, path: str):
    p, err = _resolve_download_path(path)
    if err or not p:
        return _download_error(request, 403 if err == "download_forbidden" else 404, err or "download_not_found")
    return FileResponse(str(p), filename=p.name)


@app.get("/brands", response_class=HTMLResponse)
def brands_get(request: Request, msg: str = ""):
    brands = list_brands()
    prefix_map = {b: list_brand_model_prefixes(b) for b in brands}
    return _render(
        request,
        "brands.html",
        {"brands": brands, "prefix_map": prefix_map, "message": msg},
    )


@app.post("/brands/prefix/add")
def brand_prefix_add(
    brand_name: str = Form(...),
    prefix: str = Form(...),
):
    ok, err = add_brand_model_prefix(brand_name, prefix)
    msg = "OK" if ok else err
    return RedirectResponse(url=f"/brands?msg={msg}", status_code=303)


@app.post("/brands/prefix/update")
def brand_prefix_update(
    brand_name: str = Form(...),
    old_prefix: str = Form(...),
    new_prefix: str = Form(...),
):
    ok, err = update_brand_model_prefix(brand_name, old_prefix, new_prefix)
    msg = "OK" if ok else err
    return RedirectResponse(url=f"/brands?msg={msg}", status_code=303)


@app.post("/brands/prefix/delete")
def brand_prefix_delete(
    brand_name: str = Form(...),
    prefix: str = Form(...),
):
    ok, err = delete_brand_model_prefix(brand_name, prefix)
    msg = "OK" if ok else err
    return RedirectResponse(url=f"/brands?msg={msg}", status_code=303)


@app.post("/brands/add")
def brands_add(name: str = Form(...)):
    ok, err = add_brand(name)
    msg = "OK" if ok else err
    return RedirectResponse(url=f"/brands?msg={msg}", status_code=303)


# ---------------- invoices ----------------

@app.get("/invoices", response_class=HTMLResponse)
def invoices_get(request: Request, tab: str = "sale", q: str = ""):
    sale_invoices = list_sale_invoices_done(q=q)
    receive_invoices = list_receive_invoices_done(q=q)
    return_invoices = list_return_invoices_done(q=q)
    return _render(
        request,
        "invoices.html",
        {
            "sale_invoices": sale_invoices,
            "receive_invoices": receive_invoices,
            "return_invoices": return_invoices,
            "active_tab": tab,
            "q": q,
        },
    )


# ---------------- invoice editing ----------------

@app.get("/invoices/sale/{number}/edit", response_class=HTMLResponse)
def invoice_sale_edit_get(request: Request, number: int, msg: str = ""):
    invoice = get_sale_invoice_full(int(number))
    if not invoice:
        return RedirectResponse(url="/invoices?tab=sale", status_code=303)
    items = get_sale_invoice_items_full(int(number))
    clients = list_clients()
    return _render(
        request,
        "invoice_sale_edit.html",
        {
            "invoice": invoice,
            "items": items,
            "clients": clients,
            "message": msg,
        },
    )


@app.post("/invoices/sale/{number}/edit")
def invoice_sale_edit_post(
    number: int,
    client_id: int = Form(...),
    warehouse_code: str = Form(...),
    product_id: List[Optional[int]] = Form(...),
    qty: List[float] = Form(...),
    unit_price: List[float] = Form(...),
    free_name: List[str] = Form(default=[]),
):
    new_items = []
    for i, (pid, q, up) in enumerate(zip(product_id, qty, unit_price)):
        fname = free_name[i] if i < len(free_name) else ""
        new_items.append({
            "product_id": pid if pid else None,
            "free_name": fname,
            "qty": q,
            "unit_price": up,
        })
    ok, err = update_sale_invoice(int(number), int(client_id), warehouse_code, new_items)
    if not ok:
        return RedirectResponse(
            url=f"/invoices/sale/{number}/edit?msg={err}", status_code=303
        )
    # Regenerate PDF
    invoice = get_sale_invoice_full(int(number))
    items = get_sale_invoice_items_full(int(number))
    if invoice and items:
        pdf_invoice = {
            "number": invoice["number"],
            "client": invoice["client"],
            "date": invoice["created_at"],
            "total": invoice["total"],
            "currency": invoice["currency"],
        }
        try:
            generate_invoice_pdf(pdf_invoice, items)
        except Exception:
            pass
    return RedirectResponse(url="/invoices?tab=sale&msg=invoice_updated", status_code=303)


@app.post("/invoices/sale/{number}/delete")
def invoice_sale_delete(request: Request, number: int):
    admin_check = _require_admin_session(request)
    if admin_check:
        return admin_check
    ok, err = delete_sale_invoice(number)
    if not ok:
        return RedirectResponse(
            url=f"/invoices?tab=sale&msg=delete_error:{err}", status_code=303
        )
    try:
        pdf_path = INVOICES_DIR / f"invoice_{number:06d}.pdf"
        if pdf_path.exists():
            pdf_path.unlink()
    except Exception:
        pass
    return RedirectResponse(url="/invoices?tab=sale&msg=invoice_deleted", status_code=303)


@app.get("/invoices/receive/{invoice_id}/edit", response_class=HTMLResponse)
def invoice_receive_edit_get(request: Request, invoice_id: int, msg: str = ""):
    invoice = receive_invoice_get(int(invoice_id))
    if not invoice:
        return RedirectResponse(url="/invoices?tab=receive", status_code=303)
    items = get_receive_invoice_items_for_edit(int(invoice_id))
    suppliers = list_suppliers(include_archived=True)
    return _render(
        request,
        "invoice_receive_edit.html",
        {
            "invoice": invoice,
            "items": items,
            "suppliers": suppliers,
            "message": msg,
        },
    )


@app.post("/invoices/receive/{invoice_id}/edit")
def invoice_receive_edit_post(
    invoice_id: int,
    supplier_id: Optional[int] = Form(None),
    supplier: str = Form(""),
    destination_warehouse: str = Form(...),
    product_id: List[int] = Form(...),
    qty: List[float] = Form(...),
    purchase_price: List[float] = Form(...),
):
    new_items = [
        {"product_id": pid, "qty": q, "purchase_price": pp}
        for pid, q, pp in zip(product_id, qty, purchase_price)
    ]
    ok, err = update_receive_invoice(
        int(invoice_id),
        supplier,
        destination_warehouse,
        new_items,
        supplier_id=supplier_id,
    )
    if not ok:
        return RedirectResponse(
            url=f"/invoices/receive/{invoice_id}/edit?msg={err}", status_code=303
        )
    return RedirectResponse(url="/invoices?tab=receive&msg=invoice_updated", status_code=303)


@app.get("/invoices/return/{invoice_id}/edit", response_class=HTMLResponse)
def invoice_return_edit_get(request: Request, invoice_id: int, msg: str = ""):
    invoice = return_invoice_get(int(invoice_id))
    if not invoice:
        return RedirectResponse(url="/invoices?tab=return", status_code=303)
    items = get_return_invoice_items_for_edit(int(invoice_id))
    clients = list_clients()
    return _render(
        request,
        "invoice_return_edit.html",
        {
            "invoice": invoice,
            "items": items,
            "clients": clients,
            "message": msg,
        },
    )


@app.post("/invoices/return/{invoice_id}/edit")
def invoice_return_edit_post(
    invoice_id: int,
    client_id: int = Form(...),
    warehouse_code: str = Form(...),
    product_id: List[int] = Form(...),
    qty: List[float] = Form(...),
    unit_price: List[float] = Form(...),
):
    new_items = [
        {"product_id": pid, "qty": q, "unit_price": up}
        for pid, q, up in zip(product_id, qty, unit_price)
    ]
    ok, err = update_return_invoice(int(invoice_id), int(client_id), warehouse_code, new_items)
    if not ok:
        return RedirectResponse(
            url=f"/invoices/return/{invoice_id}/edit?msg={err}", status_code=303
        )
    return RedirectResponse(url="/invoices?tab=return&msg=invoice_updated", status_code=303)


@app.get("/history", response_class=HTMLResponse)
def history_get(request: Request, q: str = ""):
    events = list_history(q=q, limit=500)
    return _render(request, "history.html", {"events": events, "search_q": q})


# ---------------- return ----------------

@app.get("/return", response_class=HTMLResponse)
def return_get(request: Request, msg: str = ""):
    clients = list_clients()
    open_inv = return_invoice_get_open()
    inv_items: list = []
    inv_total: float = 0.0
    inv_qty: float = 0.0
    if open_inv:
        inv_items = return_invoice_get_items(open_inv["id"])
        inv_total = calc_document_total(inv_items, "unit_price")
        inv_qty = sum(float(i["qty"]) for i in inv_items)
    return _render(
        request,
        "return.html",
        {
            "message": msg,
            "clients": clients,
            "open_inv": open_inv,
            "inv_items": inv_items,
            "inv_total": inv_total,
            "inv_qty": inv_qty,
        },
    )


@app.post("/return/start")
def return_start(
    client_id: int = Form(...),
    warehouse_code: str = Form(...),
    note: str = Form(""),
):
    ok, err, inv_id = return_invoice_start(int(client_id), warehouse_code, note)
    if not ok:
        return RedirectResponse(url=f"/return?msg={err}", status_code=303)
    return RedirectResponse(url="/return?msg=return_started", status_code=303)


@app.post("/return/cancel")
def return_cancel(invoice_id: int = Form(...)):
    ok, err = return_invoice_cancel(int(invoice_id))
    msg = "return_cancelled" if ok else f"cancel_error:{err}"
    return RedirectResponse(url=f"/return?msg={msg}", status_code=303)


@app.post("/return/item/add")
def return_item_add_post(
    invoice_id: int = Form(...),
    product_id: Optional[int] = Form(None),
    qty: float = Form(...),
    unit_price: float = Form(...),
):
    if not product_id or product_id <= 0:
        return RedirectResponse(url="/return?msg=select_product", status_code=303)
    ok, err = return_item_add(int(invoice_id), int(product_id), float(qty), float(unit_price))
    msg = "item_added" if ok else f"add_error:{err}"
    return RedirectResponse(url=f"/return?msg={msg}", status_code=303)


@app.post("/return/item/update")
def return_item_update_post(
    item_id: int = Form(...),
    qty: float = Form(...),
    unit_price: float = Form(...),
):
    ok, err = return_item_update(int(item_id), float(qty), float(unit_price))
    msg = "item_updated" if ok else f"update_error:{err}"
    return RedirectResponse(url=f"/return?msg={msg}", status_code=303)


@app.post("/return/item/delete")
def return_item_delete_post(item_id: int = Form(...)):
    ok, err = return_item_delete(int(item_id))
    msg = "item_removed" if ok else f"delete_error:{err}"
    return RedirectResponse(url=f"/return?msg={msg}", status_code=303)


@app.post("/return/finish")
def return_finish(invoice_id: int = Form(...)):
    ok, err = return_invoice_finish(int(invoice_id))
    if not ok:
        return RedirectResponse(url=f"/return?msg=finish_error:{err}", status_code=303)
    inv = return_invoice_get(int(invoice_id))
    make_backup()
    return RedirectResponse(url=f"/return/xlsx/view?n={invoice_id}", status_code=303)


@app.get("/return/xlsx")
def return_xlsx(n: int):
    inv = return_invoice_get(int(n))
    if not inv:
        return RedirectResponse(url="/return?msg=invoice_not_found", status_code=303)
    items = return_invoice_get_items(int(n))
    data = generate_return_xlsx_bytes(inv, items)
    filename = f"return_{inv['number']:06d}.xlsx"
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/return/xlsx/view", response_class=HTMLResponse)
def return_xlsx_view(request: Request, n: int):
    inv = return_invoice_get(int(n))
    if not inv:
        return RedirectResponse(url="/return?msg=invoice_not_found", status_code=303)
    items = return_invoice_get_items(int(n))
    inv_total = calc_document_total(items, "unit_price")
    return _render(
        request,
        "return_xlsx_view.html",
        {"invoice": inv, "items": items, "inv_total": inv_total},
    )


# ──────────────────────────────────────────────────────────────────────────────
# Help page
# ──────────────────────────────────────────────────────────────────────────────

@app.get("/help", response_class=HTMLResponse)
def help_page(request: Request):
    return _render(request, "help.html", {})


# ──────────────────────────────────────────────────────────────────────────────
# Unlock (site-lock) routes
# ──────────────────────────────────────────────────────────────────────────────

@app.get("/unlock", response_class=HTMLResponse)
def unlock_get(request: Request, next: str = "/"):
    lang = _get_ui_lang(request)
    theme = _get_ui_theme(request)
    return templates.TemplateResponse(
        request,
        "unlock.html",
        {
            "next": next,
            "error": False,
            "ui_lang": lang,
            "ui_theme": theme,
            "t": get_translations(lang),
        },
    )


@app.post("/unlock")
def unlock_post(
    request: Request,
    response: Response,
    password: str = Form(...),
    next: str = Form("/"),
):
    stored_hash = get_setting("site_lock_hash", "")
    if stored_hash and _verify_password(password, stored_hash):
        token = secrets.token_urlsafe(32)
        hours = _session_hours()
        expires_at = (datetime.now(timezone.utc) + timedelta(hours=hours)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        create_session(token, expires_at)
        redir = RedirectResponse(url=next or "/", status_code=303)
        _set_session_cookie(redir, token)
        return redir
    lang = _get_ui_lang(request)
    theme = _get_ui_theme(request)
    return templates.TemplateResponse(
        request,
        "unlock.html",
        {
            "next": next,
            "error": True,
            "ui_lang": lang,
            "ui_theme": theme,
            "t": get_translations(lang),
        },
        status_code=401,
    )


@app.get("/logout")
def logout(request: Request):
    token = request.cookies.get("site_session", "")
    if token:
        delete_session(token)
    redir = RedirectResponse(url="/unlock", status_code=303)
    redir.delete_cookie("site_session")
    return redir


# ──────────────────────────────────────────────────────────────────────────────
# UI preference cookie setters
# ──────────────────────────────────────────────────────────────────────────────

@app.post("/set-lang")
def set_lang(lang: str = Form(...), next: str = Form("/")):
    if lang not in SUPPORTED_LANGS:
        lang = "en"
    redir = RedirectResponse(url=next or "/", status_code=303)
    redir.set_cookie("ui_lang", lang, max_age=365 * 86400, samesite="lax")
    return redir


@app.post("/set-theme")
def set_theme(theme: str = Form(...), next: str = Form("/")):
    if theme not in ("light", "dark", "system"):
        theme = "system"
    redir = RedirectResponse(url=next or "/", status_code=303)
    redir.set_cookie("ui_theme", theme, max_age=365 * 86400, samesite="lax")
    return redir


# ──────────────────────────────────────────────────────────────────────────────
# Admin / Settings
# ──────────────────────────────────────────────────────────────────────────────

@app.get("/admin/settings", response_class=HTMLResponse)
def admin_settings_get(request: Request, saved: str = "", msg: str = ""):
    return _render(
        request,
        "admin_settings.html",
        {
            "saved": saved,
            "msg": msg,
            "lock_enabled": get_setting("site_lock_enabled", "0") == "1",
            "session_hours": get_setting("site_lock_session_hours", "24"),
            "default_lang": get_setting("default_lang", "en"),
            "default_theme": get_setting("default_theme", "system"),
            "bg_enabled": get_setting("bg_enabled", "1") == "1",
            "bg_size": get_setting("bg_size", "cover"),
            "bg_overlay": get_setting("bg_overlay", "25"),
            "markup_presets": get_sale_markup_presets(),
            "default_markup": get_sale_default_markup(),
            "allowed_markups": _ALLOWED_MARKUPS,
        },
    )


@app.post("/admin/settings/sitelock")
def admin_settings_sitelock(
    site_lock_enabled: str = Form("0"),
    new_password: str = Form(""),
    session_hours: str = Form("24"),
    logout_all: str = Form("0"),
):
    set_setting("site_lock_enabled", "1" if site_lock_enabled == "1" else "0")
    try:
        hours = max(1, int(session_hours))
    except (ValueError, TypeError):
        hours = 24
    set_setting("site_lock_session_hours", str(hours))
    if new_password.strip():
        set_setting("site_lock_hash", _hash_password(new_password.strip()))
    if logout_all == "1":
        delete_all_sessions()
    return RedirectResponse(url="/admin/settings?saved=1", status_code=303)


@app.post("/admin/settings/lang")
def admin_settings_lang(default_lang: str = Form("en")):
    if default_lang not in SUPPORTED_LANGS:
        default_lang = "en"
    set_setting("default_lang", default_lang)
    return RedirectResponse(url="/admin/settings?saved=1", status_code=303)


@app.post("/admin/settings/theme")
def admin_settings_theme(default_theme: str = Form("system")):
    if default_theme not in ("light", "dark", "system"):
        default_theme = "system"
    set_setting("default_theme", default_theme)
    return RedirectResponse(url="/admin/settings?saved=1", status_code=303)


@app.post("/admin/settings/markup")
async def admin_settings_markup(request: Request):
    form = await request.form()
    active = sorted(
        {p for p in _ALLOWED_MARKUPS if form.get(f"markup_{p}") == "1"}
    )
    if not active:
        return RedirectResponse(
            url="/admin/settings?msg=markup_error_empty", status_code=303
        )
    try:
        default = int(form.get("default_markup", "0"))
    except (ValueError, TypeError):
        default = 0
    if default not in active:
        return RedirectResponse(
            url="/admin/settings?msg=markup_error_default", status_code=303
        )
    set_setting("sale_markup_presets", ",".join(str(p) for p in active))
    set_setting("sale_default_markup", str(default))
    return RedirectResponse(url="/admin/settings?saved=1", status_code=303)


@app.post("/admin/settings/background")
async def admin_settings_background(
    bg_enabled: str = Form("0"),
    bg_size: str = Form("cover"),
    bg_overlay: str = Form("25"),
    bg_file: UploadFile = File(None),
):
    set_setting("bg_enabled", "1" if bg_enabled == "1" else "0")
    if bg_size not in ("cover", "contain"):
        bg_size = "cover"
    set_setting("bg_size", bg_size)
    try:
        overlay = max(0, min(100, int(bg_overlay)))
    except (ValueError, TypeError):
        overlay = 25
    set_setting("bg_overlay", str(overlay))
    if bg_file and bg_file.filename:
        suffix = Path(bg_file.filename).suffix.lower()
        if suffix in _ALLOWED_BG_UPLOAD_EXTS:
            dest = STATIC_DIR / "bg.jpg"
            with dest.open("wb") as fout:
                shutil.copyfileobj(bg_file.file, fout)
    return RedirectResponse(url="/admin/settings?saved=1", status_code=303)


@app.post("/admin/settings/price-background")
async def admin_settings_price_background(
    price_bg_file: UploadFile = File(None),
):
    if price_bg_file and price_bg_file.filename:
        suffix = Path(price_bg_file.filename).suffix.lower()
        if suffix in _ALLOWED_BG_UPLOAD_EXTS:
            dest = STATIC_DIR / "price-bg.jpg"
            with dest.open("wb") as fout:
                shutil.copyfileobj(price_bg_file.file, fout)
    return RedirectResponse(url="/admin/settings?saved=1", status_code=303)


# ──────────────────────────────────────────────────────────────────────────────
# Backup / Restore endpoints
# ──────────────────────────────────────────────────────────────────────────────

@app.post("/admin/backup/create")
def admin_backup_create(request: Request):
    admin_check = _require_admin_session(request)
    if admin_check:
        return admin_check
    zip_path = make_backup()
    p = Path(zip_path)
    return FileResponse(str(p), filename=p.name, media_type="application/zip")


@app.post("/admin/backup/restore")
async def admin_backup_restore(
    request: Request,
    backup_file: UploadFile = File(...),
    confirm: str = Form(""),
):
    admin_check = _require_admin_session(request)
    if admin_check:
        return admin_check
    if confirm != "1":
        return RedirectResponse(
            url="/admin/settings?msg=restore_error:confirmation+required",
            status_code=303,
        )

    # Save uploaded file to a temp location
    try:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir) / "uploaded.zip"
            with tmp_path.open("wb") as fout:
                shutil.copyfileobj(backup_file.file, fout)

            # Validate it is a valid zip with stock.db
            if not zipfile.is_zipfile(str(tmp_path)):
                return RedirectResponse(
                    url="/admin/settings?msg=restore_error:not+a+valid+zip",
                    status_code=303,
                )

            with zipfile.ZipFile(str(tmp_path), "r") as zf:
                names = zf.namelist()
                if "stock.db" not in names:
                    return RedirectResponse(
                        url="/admin/settings?msg=restore_error:zip+missing+stock.db",
                        status_code=303,
                    )

                # Zip Slip protection: ensure no member escapes the extract dir
                extract_dir = Path(tmp_dir) / "extracted"
                extract_dir.mkdir()
                for member in zf.infolist():
                    member_path = (extract_dir / member.filename).resolve()
                    if not str(member_path).startswith(str(extract_dir.resolve())):
                        return RedirectResponse(
                            url="/admin/settings?msg=restore_error:unsafe+zip+path",
                            status_code=303,
                        )

                # Create a safety backup before overwriting anything
                make_backup()

                # Extract to temp dir
                zf.extractall(str(extract_dir))

            # Restore DB
            src_db = extract_dir / "stock.db"
            DB_PATH.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(src_db), str(DB_PATH))

            # Restore invoices (if present in zip)
            src_invoices = extract_dir / "invoices"
            if src_invoices.exists():
                INVOICES_DIR.mkdir(parents=True, exist_ok=True)
                for pdf in src_invoices.glob("*.pdf"):
                    shutil.copy2(str(pdf), str(INVOICES_DIR / pdf.name))

    except (zipfile.BadZipFile, OSError, ValueError) as exc:
        err = quote(str(exc)[:200], safe="")
        return RedirectResponse(
            url=f"/admin/settings?msg=restore_error:{err}",
            status_code=303,
        )

    return RedirectResponse(url="/admin/settings?msg=restored_ok", status_code=303)


# ──────────────────────────────────────────────────────────────────────────────
# Pocket Price – token auth helper
# ──────────────────────────────────────────────────────────────────────────────

def _get_price_token(request: Request) -> Optional[str]:
    """Extract price token from X-Price-Token header or Authorization: Bearer ..."""
    token = request.headers.get("x-price-token", "").strip()
    if not token:
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            token = auth[7:].strip()
    return token or None


def _get_price_device_id(request: Request) -> Optional[str]:
    """Extract device UUID from X-Price-Device header."""
    device_id = request.headers.get("x-price-device", "").strip()
    return device_id or None


def _require_price_token(request: Request):
    """Validate price token and enforce device binding; return token row or None."""
    token = _get_price_token(request)
    if not token:
        return None
    row = validate_price_token(token)
    if not row:
        return None
    device_id = _get_price_device_id(request)
    bound_device = row.get("device_id")
    if bound_device:
        # Token is already bound – reject if device doesn't match
        if not device_id or device_id != bound_device:
            return "not_paired"
    else:
        # First use – bind token to this device (if device_id provided)
        if device_id:
            bind_price_token_device(row["id"], device_id)
            row = dict(row)
            row["device_id"] = device_id
    touch_price_token(row["id"])
    return row


# ──────────────────────────────────────────────────────────────────────────────
# Pocket Price – public API endpoints
# ──────────────────────────────────────────────────────────────────────────────

@app.get("/api/price/search")
def api_price_search(request: Request, q: str = ""):
    row = _require_price_token(request)
    if row is None:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    if row == "not_paired":
        return JSONResponse({"error": "not_paired"}, status_code=401)
    mode = (row.get("mode") or "SIMPLE").upper()
    show_qty = bool(row.get("show_qty", 0))
    show_buy_price = bool(row.get("show_buy_price", 0))
    results = search_products_for_price(q.strip(), limit=30, mode=mode, show_qty=show_qty, show_buy_price=show_buy_price)
    return JSONResponse({"results": results, "mode": mode})


@app.get("/api/price/barcode")
def api_price_barcode(request: Request, code: str = ""):
    row = _require_price_token(request)
    if row is None:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    if row == "not_paired":
        return JSONResponse({"error": "not_paired"}, status_code=401)
    mode = (row.get("mode") or "SIMPLE").upper()
    show_qty = bool(row.get("show_qty", 0))
    show_buy_price = bool(row.get("show_buy_price", 0))
    results = search_products_for_price(code.strip(), limit=1, mode=mode, show_qty=show_qty, show_buy_price=show_buy_price)
    if not results:
        return JSONResponse({"error": "not_found"}, status_code=404)
    return JSONResponse({"product": results[0], "mode": mode})


@app.get("/api/price/token-info")
def api_price_token_info(request: Request):
    row = _require_price_token(request)
    if row is None:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    if row == "not_paired":
        return JSONResponse({"error": "not_paired"}, status_code=401)
    return JSONResponse({"mode": row.get("mode", "SIMPLE"), "show_qty": bool(row.get("show_qty", 0)), "show_buy_price": bool(row.get("show_buy_price", 0))})


# ──────────────────────────────────────────────────────────────────────────────
# Pocket Price – admin token management
# ──────────────────────────────────────────────────────────────────────────────

@app.get("/admin/price-tokens", response_class=HTMLResponse)
def admin_price_tokens_get(request: Request, msg: str = "", new_token: str = ""):
    admin_check = _require_admin_session(request)
    if admin_check:
        return admin_check
    tokens = list_price_tokens()
    return _render(
        request,
        "admin_price_tokens.html",
        {"tokens": tokens, "msg": msg, "new_token": new_token},
    )


@app.post("/admin/price-tokens/create")
def admin_price_tokens_create(request: Request, label: str = Form(""), mode: str = Form("SIMPLE")):
    admin_check = _require_admin_session(request)
    if admin_check:
        return admin_check
    try:
        plain, _row = create_price_token(label, mode)
        return RedirectResponse(
            url=f"/admin/price-tokens?new_token={quote(plain, safe='')}",
            status_code=303,
        )
    except Exception as exc:
        return RedirectResponse(
            url=f"/admin/price-tokens?msg=error:{quote(str(exc), safe='')}",
            status_code=303,
        )


@app.post("/admin/price-tokens/set-mode")
def admin_price_tokens_set_mode(request: Request, token_id: int = Form(...), mode: str = Form("SIMPLE")):
    admin_check = _require_admin_session(request)
    if admin_check:
        return admin_check
    set_price_token_mode(int(token_id), mode)
    return RedirectResponse(url="/admin/price-tokens", status_code=303)


@app.post("/admin/price-tokens/toggle-qty")
def admin_price_tokens_toggle_qty(request: Request, token_id: int = Form(...), show_qty: int = Form(0)):
    admin_check = _require_admin_session(request)
    if admin_check:
        return admin_check
    set_price_token_show_qty(int(token_id), bool(show_qty))
    return RedirectResponse(url="/admin/price-tokens", status_code=303)


@app.post("/admin/price-tokens/toggle-buy-price")
def admin_price_tokens_toggle_buy_price(
    request: Request,
    token_id: int = Form(...),
    show_buy_price: int = Form(0),
):
    admin_check = _require_admin_session(request)
    if admin_check:
        return admin_check
    set_price_token_show_buy_price(int(token_id), bool(show_buy_price))
    return RedirectResponse(url="/admin/price-tokens", status_code=303)


@app.post("/admin/price-tokens/revoke")
def admin_price_tokens_revoke(request: Request, token_id: int = Form(...)):
    admin_check = _require_admin_session(request)
    if admin_check:
        return admin_check
    ok, err = revoke_price_token(int(token_id))
    msg = "revoked" if ok else f"error:{quote(err, safe='')}"
    return RedirectResponse(url=f"/admin/price-tokens?msg={msg}", status_code=303)


@app.post("/admin/price-tokens/delete")
def admin_price_tokens_delete(request: Request, token_id: int = Form(...)):
    admin_check = _require_admin_session(request)
    if admin_check:
        return admin_check
    ok, err = delete_price_token(int(token_id))
    msg = "deleted" if ok else f"error:{quote(err, safe='')}"
    return RedirectResponse(url=f"/admin/price-tokens?msg={msg}", status_code=303)


# ──────────────────────────────────────────────────────────────────────────────
# Pocket Catalog – catalog token helpers
# ──────────────────────────────────────────────────────────────────────────────

def _get_catalog_token(request: Request) -> Optional[str]:
    """Extract catalog token from X-Catalog-Token header or Authorization: Bearer."""
    token = request.headers.get("x-catalog-token", "").strip()
    if not token:
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            token = auth[7:].strip()
    return token or None


def _get_catalog_device_id(request: Request) -> Optional[str]:
    """Extract device UUID from X-Catalog-Device header."""
    device_id = request.headers.get("x-catalog-device", "").strip()
    return device_id or None


def _require_catalog_token(request: Request):
    """Validate catalog token and enforce device binding; return token row or None."""
    token = _get_catalog_token(request)
    if not token:
        return None
    row = validate_catalog_token(token)
    if not row:
        return None
    device_id = _get_catalog_device_id(request)
    bound_device = row.get("device_id")
    if bound_device:
        if not device_id or device_id != bound_device:
            return "not_paired"
    else:
        if device_id:
            bind_catalog_token_device(row["id"], device_id)
            row = dict(row)
            row["device_id"] = device_id
    touch_catalog_token(row["id"])
    return row


# ──────────────────────────────────────────────────────────────────────────────
# Pocket Catalog – public API endpoints
# ──────────────────────────────────────────────────────────────────────────────

@app.get("/api/catalog/barcode")
def api_catalog_barcode(request: Request, code: str = ""):
    row = _require_catalog_token(request)
    if row is None or row == "not_paired":
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    code = code.strip()
    if not code:
        return JSONResponse({"error": "barcode_empty"}, status_code=400)
    product = get_product_by_barcode_for_scan(code)
    if product:
        return JSONResponse({"product": product})
    return JSONResponse({"error": "not_found"}, status_code=404)


@app.post("/api/catalog/create")
async def api_catalog_create(request: Request):
    row = _require_catalog_token(request)
    if row is None or row == "not_paired":
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)
    try:
        ct = request.headers.get("content-type", "")
        if "application/json" in ct:
            body = await request.json()
        else:
            form = await request.form()
            body = dict(form)
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    barcode = str(body.get("barcode", "")).strip()
    brand = str(body.get("brand", "")).strip()
    model = str(body.get("model", "")).strip()
    name = str(body.get("name", "")).strip()
    raw_price = body.get("purchase_price", "0")

    if not barcode:
        return JSONResponse({"ok": False, "error": "barcode_empty"}, status_code=400)
    if not brand:
        return JSONResponse({"ok": False, "error": "brand_required"}, status_code=400)
    if not model:
        return JSONResponse({"ok": False, "error": "model_required"}, status_code=400)
    if not name:
        return JSONResponse({"ok": False, "error": "name_required"}, status_code=400)

    # Reject if barcode already exists
    existing = get_product_by_barcode_for_scan(barcode)
    if existing:
        return JSONResponse({"ok": False, "error": "barcode_exists"}, status_code=400)

    try:
        price = round(float(raw_price or 0), 2)
    except (ValueError, TypeError):
        return JSONResponse({"ok": False, "error": "invalid_price"}, status_code=400)

    ok, err, product_id = create_product_with_barcode(brand, model, name, price, barcode)
    if not ok:
        return JSONResponse({"ok": False, "error": err}, status_code=400)
    product = get_product_by_barcode_for_scan(barcode)
    return JSONResponse({"ok": True, "product": product})


@app.post("/api/catalog/update-price")
async def api_catalog_update_price(request: Request):
    row = _require_catalog_token(request)
    if row is None or row == "not_paired":
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)
    try:
        ct = request.headers.get("content-type", "")
        if "application/json" in ct:
            body = await request.json()
        else:
            form = await request.form()
            body = dict(form)
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    barcode = str(body.get("barcode", "")).strip()
    if not barcode:
        return JSONResponse({"ok": False, "error": "barcode_empty"}, status_code=400)

    existing = get_product_by_barcode_for_scan(barcode)
    if not existing:
        return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)

    raw_price = body.get("purchase_price", "")
    if raw_price not in (None, ""):
        try:
            price = round(float(raw_price), 2)
        except (ValueError, TypeError):
            return JSONResponse({"ok": False, "error": "invalid_price"}, status_code=400)
        ok, err = update_product_purchase_price(existing["id"], price)
        if not ok:
            return JSONResponse({"ok": False, "error": err}, status_code=500)
        existing["purchase_price"] = price

    return JSONResponse({"ok": True, "product": existing})


# ──────────────────────────────────────────────────────────────────────────────
# Pocket Catalog – admin token management
# ──────────────────────────────────────────────────────────────────────────────

@app.get("/admin/catalog-tokens", response_class=HTMLResponse)
def admin_catalog_tokens_get(request: Request, msg: str = "", new_token: str = ""):
    admin_check = _require_admin_session(request)
    if admin_check:
        return admin_check
    tokens = list_catalog_tokens()
    return _render(
        request,
        "admin_catalog_tokens.html",
        {"tokens": tokens, "msg": msg, "new_token": new_token},
    )


@app.post("/admin/catalog-tokens/create")
def admin_catalog_tokens_create(request: Request, label: str = Form("")):
    admin_check = _require_admin_session(request)
    if admin_check:
        return admin_check
    try:
        plain, _row = create_catalog_token(label)
        return RedirectResponse(
            url=f"/admin/catalog-tokens?new_token={quote(plain, safe='')}",
            status_code=303,
        )
    except Exception as exc:
        return RedirectResponse(
            url=f"/admin/catalog-tokens?msg=error:{quote(str(exc), safe='')}",
            status_code=303,
        )


@app.post("/admin/catalog-tokens/revoke")
def admin_catalog_tokens_revoke(request: Request, token_id: int = Form(...)):
    admin_check = _require_admin_session(request)
    if admin_check:
        return admin_check
    ok, err = revoke_catalog_token(int(token_id))
    msg = "revoked" if ok else f"error:{quote(err, safe='')}"
    return RedirectResponse(url=f"/admin/catalog-tokens?msg={msg}", status_code=303)


@app.post("/admin/catalog-tokens/delete")
def admin_catalog_tokens_delete(request: Request, token_id: int = Form(...)):
    admin_check = _require_admin_session(request)
    if admin_check:
        return admin_check
    ok, err = delete_catalog_token(int(token_id))
    msg = "deleted" if ok else f"error:{quote(err, safe='')}"
    return RedirectResponse(url=f"/admin/catalog-tokens?msg={msg}", status_code=303)


# ──────────────────────────────────────────────────────────────────────────────
# Pocket Catalog – PWA page
# ──────────────────────────────────────────────────────────────────────────────

@app.get("/catalog", response_class=HTMLResponse)
def catalog_page(request: Request):
    brands = list_brands()
    prefix_map = {b: list_brand_model_prefixes(b) for b in brands}
    return _render(request, "catalog.html", {"brands": brands, "prefix_map": prefix_map})

@app.get("/price", response_class=HTMLResponse)
def price_page(request: Request):
    lang = _get_ui_lang(request)
    theme = _get_ui_theme(request)
    return templates.TemplateResponse(
        request,
        "price.html",
        {
            "ui_lang": lang,
            "ui_theme": theme,
            "price_bg_version": _static_asset_version("price-bg.jpg"),
            "t": get_translations(lang),
        },
    )


@app.get("/price/manifest.webmanifest")
def price_manifest():
    manifest = {
        "name": "Pocket Price",
        "short_name": "Price",
        "start_url": "/price",
        "display": "standalone",
        "background_color": "#0a0f28",
        "theme_color": "#0d47a1",
        "icons": [
            {"src": "/static/icon-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "/static/icon-512.png", "sizes": "512x512", "type": "image/png"},
        ],
    }
    return JSONResponse(manifest, media_type="application/manifest+json")


@app.get("/price/sw.js")
def price_sw():
    sw_code = r"""
const CACHE = 'pocket-price-v2';
const SHELL = ['/price'];

self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE).then(c => Promise.all(SHELL.map(url => fetch(url).then(r => c.put(url, r.clone())).catch(() => null)))).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);
  // Network-first for API calls and shell
  if (url.pathname.startsWith('/api/price')) {
    e.respondWith(fetch(e.request).catch(() => new Response('{"error":"offline"}', {headers:{'Content-Type':'application/json'}})));
  } else if (SHELL.includes(url.pathname)) {
    e.respondWith(
      fetch(e.request)
        .then(r => {
          const copy = r.clone();
          caches.open(CACHE).then(c => c.put(e.request, copy));
          return r;
        })
        .catch(() => caches.match(e.request).then(r => r || caches.match('/price')))
    );
  }
});
"""
    return Response(content=sw_code, media_type="application/javascript")

from __future__ import annotations

import hashlib
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
    # sale by id
    get_open_cart,
    cart_start_by_id,
    cart_add_by_id,
    cart_show_by_id,
    cart_finish_by_id,
    cart_add_by_cart_id,
    cart_show_by_cart_id,
    cart_finish_by_cart_id_shop1416,
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
    # NEW: suppliers list for UI suggestions
    list_receive_suppliers,
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
    get_total_stock_value,
    # invoice editing
    get_sale_invoice_full,
    get_sale_invoice_items_full,
    update_sale_invoice,
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
from app.services.backup import make_backup, INVOICES_DIR
from app.i18n import get_translations, SUPPORTED_LANGS
from app.db.sqlite import (
    DB_PATH,
    get_setting,
    set_setting,
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
    search_products_for_price,
    get_product_by_barcode,
)


BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(title="Stock Bot Web")

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ──────────────────────────────────────────────────────────────────────────────
# Site-lock helpers
# ──────────────────────────────────────────────────────────────────────────────

_HASH_ITERS = 260_000  # PBKDF2 iteration count


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

_LOCK_BYPASS_PREFIXES = ("/static", "/unlock", "/api/", "/price")


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
        "nav_total_debt_usd": get_total_clients_debt(),
        "nav_stock_value_usd": get_total_stock_value(),
    }
    base.update(ctx)
    return templates.TemplateResponse(name, base)


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
def products(request: Request, show_archived: int = 0):
    rows = list_products(include_archived=bool(show_archived))
    brands = list_brands()
    return _render(request, "products.html", {"products": rows, "brands": brands, "show_archived": show_archived})


@app.post("/products/add")
def products_add(
    brand: str = Form(...),
    model: str = Form(...),
    name: str = Form(...),
    wh_price: float = Form(...),
    source: str = Form("CHINA"),
    warehouse: str = Form("TM_DEPO"),
    qty: float = Form(...),
):
    try:
        product_id, created = add_or_get_product_id(brand, model, name, float(wh_price))
        ok, err = receive_stock_by_product_id(warehouse, product_id, float(qty), source=source)
        if not ok:
            return RedirectResponse(url=f"/products?msg=received:{err}", status_code=303)

        msg = "created+received" if created else "received (existing product)"
        return RedirectResponse(url=f"/products?msg={msg}", status_code=303)
    except Exception as e:
        # вместо черного экрана
        return RedirectResponse(url=f"/products?msg=error:{e}", status_code=303)


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


# ---------------- stock ----------------

@app.get("/stock", response_class=HTMLResponse)
def stock_get(request: Request, warehouse: str = "", q: str = "", msg: str = "", show_archived: int = 0):
    rows = get_stock(warehouse if warehouse else None, q if q else None, include_archived=bool(show_archived))
    return _render(
        request,
        "stock.html",
        {
            "rows": rows,
            "selected_warehouse": (warehouse or "").strip().upper(),
            "search_q": q or "",
            "msg": msg,
            "show_archived": show_archived,
        },
    )


@app.post("/stock/product/wh_price")
def stock_update_wh_price(
    product_id: int = Form(...),
    wh_price: float = Form(...),
    warehouse: str = Form(""),
    q: str = Form(""),
    show_archived: int = Form(0),
):
    ok, err = update_product_wh_price(product_id, wh_price)
    msg = "wh_price_updated" if ok else f"error:{err}"
    return RedirectResponse(url=f"/stock?warehouse={warehouse}&q={q}&msg={msg}&show_archived={show_archived}", status_code=303)


@app.post("/stock/product/{product_id}/archive")
def stock_product_archive(product_id: int, warehouse: str = Form(""), q: str = Form(""), show_archived: int = Form(0)):
    ok, err = set_product_archived(product_id, 1)
    msg = "archived" if ok else f"error:{err}"
    return RedirectResponse(url=f"/stock?warehouse={warehouse}&q={q}&msg={msg}&show_archived={show_archived}", status_code=303)


@app.post("/stock/product/{product_id}/unarchive")
def stock_product_unarchive(product_id: int, warehouse: str = Form(""), q: str = Form(""), show_archived: int = Form(0)):
    ok, err = set_product_archived(product_id, 0)
    msg = "unarchived" if ok else f"error:{err}"
    return RedirectResponse(url=f"/stock?warehouse={warehouse}&q={q}&msg={msg}&show_archived={show_archived}", status_code=303)


@app.get("/products/{product_id}/history", response_class=HTMLResponse)
def product_history_get(request: Request, product_id: int):
    prods = list_products(include_archived=True)
    prod = next((p for p in prods if p["id"] == product_id), None)
    if not prod:
        return RedirectResponse(url="/stock?msg=product_not_found", status_code=303)
    events = list_history_by_product(product_id)
    return _render(request, "product_history.html", {"product": prod, "events": events})


@app.get("/stock/xlsx")
def stock_xlsx(warehouse: str = "", q: str = "", show_archived: int = 0):
    rows = get_stock(warehouse if warehouse else None, q if q else None, include_archived=bool(show_archived))
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
    suppliers = list_receive_suppliers()

    inv_items: list = []
    inv_total: float = 0.0
    inv_qty: float = 0.0
    if open_inv:
        inv_items = receive_invoice_get_items(open_inv["id"])
        inv_total = sum(float(i["total"]) for i in inv_items)
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
    supplier: str = Form(""),
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
    ok, err, inv_id = receive_invoice_start(supplier, destination_warehouse, note)
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
    inv_total = sum(float(i["total"]) for i in items)
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
def clients_get(request: Request, msg: str = "", show_archived: int = 0):
    clients = list_clients_with_balance(include_archived=bool(show_archived))
    return _render(request, "clients.html", {"clients": clients, "message": msg, "show_archived": show_archived})


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



@app.get("/sale", response_class=HTMLResponse)
def sale_get(request: Request, msg: str = ""):
    clients = list_clients()
    open_cart = get_open_cart()
    cart_items: list = []
    cart_total: float = 0.0
    cart_qty: float = 0.0
    if open_cart:
        _, cart_items = get_cart_items_list(open_cart["cart_id"])
        cart_total = sum(float(i["total"]) for i in cart_items)
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

    pdf_path = generate_invoice_pdf(invoice, items)
    backup_path = make_backup()

    return RedirectResponse(
        url=f"/sale/done?pdf={pdf_path}&backup={backup_path}&n={invoice['number']}",
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
    cart_total = sum(float(i["total"]) for i in items)
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
def download(path: str):
    # MVP: доверяем path. Потом обязательно ограничим директории!
    p = Path(path)
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


@app.post("/brands/add")
def brands_add(name: str = Form(...)):
    ok, err = add_brand(name)
    msg = "OK" if ok else err
    return RedirectResponse(url=f"/brands?msg={msg}", status_code=303)


# ---------------- invoices ----------------

@app.get("/invoices", response_class=HTMLResponse)
def invoices_get(request: Request, tab: str = "sale"):
    sale_invoices = list_sale_invoices_done()
    receive_invoices = list_receive_invoices_done()
    return_invoices = list_return_invoices_done()
    return _render(
        request,
        "invoices.html",
        {
            "sale_invoices": sale_invoices,
            "receive_invoices": receive_invoices,
            "return_invoices": return_invoices,
            "active_tab": tab,
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
    product_id: List[int] = Form(...),
    qty: List[float] = Form(...),
    unit_price: List[float] = Form(...),
):
    new_items = [
        {"product_id": pid, "qty": q, "unit_price": up}
        for pid, q, up in zip(product_id, qty, unit_price)
    ]
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


@app.get("/invoices/receive/{invoice_id}/edit", response_class=HTMLResponse)
def invoice_receive_edit_get(request: Request, invoice_id: int, msg: str = ""):
    invoice = receive_invoice_get(int(invoice_id))
    if not invoice:
        return RedirectResponse(url="/invoices?tab=receive", status_code=303)
    items = get_receive_invoice_items_for_edit(int(invoice_id))
    suppliers = list_receive_suppliers()
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
    ok, err = update_receive_invoice(int(invoice_id), supplier, destination_warehouse, new_items)
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
        inv_total = sum(float(i["total"]) for i in inv_items)
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
    product_id: int = Form(...),
    qty: float = Form(...),
    unit_price: float = Form(...),
):
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
    inv_total = sum(float(i["total"]) for i in items)
    return _render(
        request,
        "return_xlsx_view.html",
        {"invoice": inv, "items": items, "inv_total": inv_total},
    )


# ──────────────────────────────────────────────────────────────────────────────
# Unlock (site-lock) routes
# ──────────────────────────────────────────────────────────────────────────────

@app.get("/unlock", response_class=HTMLResponse)
def unlock_get(request: Request, next: str = "/"):
    lang = _get_ui_lang(request)
    theme = _get_ui_theme(request)
    return templates.TemplateResponse(
        "unlock.html",
        {
            "request": request,
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
        "unlock.html",
        {
            "request": request,
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
        if suffix in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
            dest = STATIC_DIR / "bg.jpg"
            with dest.open("wb") as fout:
                shutil.copyfileobj(bg_file.file, fout)
    return RedirectResponse(url="/admin/settings?saved=1", status_code=303)


# ──────────────────────────────────────────────────────────────────────────────
# Backup / Restore endpoints
# ──────────────────────────────────────────────────────────────────────────────

@app.post("/admin/backup/create")
def admin_backup_create():
    zip_path = make_backup()
    p = Path(zip_path)
    return FileResponse(str(p), filename=p.name, media_type="application/zip")


@app.post("/admin/backup/restore")
async def admin_backup_restore(
    backup_file: UploadFile = File(...),
    confirm: str = Form(""),
):
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


def _require_price_token(request: Request):
    """Validate price token; return token row or raise 401 JSONResponse."""
    token = _get_price_token(request)
    if not token:
        return None
    row = validate_price_token(token)
    if row:
        touch_price_token(row["id"])
    return row


# ──────────────────────────────────────────────────────────────────────────────
# Pocket Price – public API endpoints
# ──────────────────────────────────────────────────────────────────────────────

@app.get("/api/price/search")
def api_price_search(request: Request, q: str = ""):
    row = _require_price_token(request)
    if not row:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    mode = row.get("mode", "SIMPLE")
    results = search_products_for_price(q, limit=30, mode=mode)
    return JSONResponse({"results": results})


@app.get("/api/price/barcode")
def api_price_barcode(request: Request, code: str = ""):
    row = _require_price_token(request)
    if not row:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    mode = row.get("mode", "SIMPLE")
    product = get_product_by_barcode(code, mode=mode)
    if not product:
        return JSONResponse({"error": "not_found"}, status_code=404)
    return JSONResponse({"product": product})


@app.get("/api/price/token-info")
def api_price_token_info(request: Request):
    row = _require_price_token(request)
    if not row:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    return JSONResponse({"mode": row.get("mode", "SIMPLE")})


# ──────────────────────────────────────────────────────────────────────────────
# Pocket Price – admin token management
# ──────────────────────────────────────────────────────────────────────────────

@app.get("/admin/price-tokens", response_class=HTMLResponse)
def admin_price_tokens_get(request: Request, msg: str = "", new_token: str = ""):
    tokens = list_price_tokens()
    return _render(
        request,
        "admin_price_tokens.html",
        {"tokens": tokens, "msg": msg, "new_token": new_token},
    )


@app.post("/admin/price-tokens/create")
def admin_price_tokens_create(label: str = Form(""), mode: str = Form("SIMPLE")):
    plain, _row = create_price_token(label, mode=mode)
    return RedirectResponse(
        url=f"/admin/price-tokens?new_token={quote(plain, safe='')}",
        status_code=303,
    )


@app.post("/admin/price-tokens/revoke")
def admin_price_tokens_revoke(token_id: int = Form(...)):
    ok, err = revoke_price_token(int(token_id))
    msg = "revoked" if ok else f"error:{quote(err, safe='')}"
    return RedirectResponse(url=f"/admin/price-tokens?msg={msg}", status_code=303)


@app.post("/admin/price-tokens/delete")
def admin_price_tokens_delete(token_id: int = Form(...)):
    ok, err = delete_price_token(int(token_id))
    msg = "deleted" if ok else f"error:{quote(err, safe='')}"
    return RedirectResponse(url=f"/admin/price-tokens?msg={msg}", status_code=303)


# ──────────────────────────────────────────────────────────────────────────────
# Pocket Price – PWA page
# ──────────────────────────────────────────────────────────────────────────────

@app.get("/price", response_class=HTMLResponse)
def price_page(request: Request):
    lang = _get_ui_lang(request)
    theme = _get_ui_theme(request)
    return templates.TemplateResponse(
        "price.html",
        {
            "request": request,
            "ui_lang": lang,
            "ui_theme": theme,
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
        "background_color": "#212529",
        "theme_color": "#212529",
        "icons": [
            {"src": "/static/icon-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "/static/icon-512.png", "sizes": "512x512", "type": "image/png"},
        ],
    }
    return JSONResponse(manifest, media_type="application/manifest+json")


@app.get("/price/sw.js")
def price_sw():
    sw_code = r"""
const CACHE = 'pocket-price-v1';
const SHELL = ['/price'];

self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting())
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
  // Network-first for API calls; cache-first for shell
  if (url.pathname.startsWith('/api/price')) {
    e.respondWith(fetch(e.request).catch(() => new Response('{"error":"offline"}', {headers:{'Content-Type':'application/json'}})));
  } else if (SHELL.includes(url.pathname)) {
    e.respondWith(
      caches.match(e.request).then(r => r || fetch(e.request))
    );
  }
});
"""
    return Response(content=sw_code, media_type="application/javascript")

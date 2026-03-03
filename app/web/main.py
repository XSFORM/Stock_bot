from __future__ import annotations

from pathlib import Path
from datetime import date
from typing import Any, Optional

from fastapi import FastAPI, Form, Request, Response
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
    # NEW: suppliers list for UI suggestions
    list_receive_suppliers,
    # NEW: warehouse management
    list_warehouses,
    add_warehouse,
    # invoices listing
    list_sale_invoices_done,
    list_receive_invoices_done,
    list_history,
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
)
from app.services.invoice_pdf import generate_invoice_pdf
from app.services.invoice_xlsx import generate_invoice_xlsx, generate_invoice_xlsx_bytes
from app.services.receive_xlsx import generate_receive_xlsx_bytes
from app.services.return_xlsx import generate_return_xlsx_bytes
from app.services.stock_xlsx import generate_stock_xlsx_bytes
from app.services.backup import make_backup


BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(title="Stock Bot Web")

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.on_event("startup")
def _startup() -> None:
    init_db()


def _render(request: Request, name: str, ctx: dict[str, Any]) -> HTMLResponse:
    wh_list = list_warehouses()
    wh_codes = [w["code"] for w in wh_list]
    wh_labels = {w["code"]: w["title"] for w in wh_list}
    base = {
        "request": request,
        "warehouses": wh_codes,
        "sources": sorted(RECEIVE_SOURCES.keys()),
        "source_labels": RECEIVE_SOURCES,
        "warehouse_labels": wh_labels,
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
def stock_get(request: Request, warehouse: str = "", q: str = ""):
    rows = get_stock(warehouse if warehouse else None, q if q else None)
    return _render(
        request,
        "stock.html",
        {
            "rows": rows,
            "selected_warehouse": (warehouse or "").strip().upper(),
            "search_q": q or "",
        },
    )


@app.get("/stock/xlsx")
def stock_xlsx(warehouse: str = "", q: str = ""):
    rows = get_stock(warehouse if warehouse else None, q if q else None)
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
):
    if wh_price <= 0:
        return RedirectResponse(url="/receive?msg=new_product_error:wh_price_required", status_code=303)
    ok, err, product_id = add_product_simple(brand, model, name, barcode, product_note, wh_price)
    if not ok:
        return RedirectResponse(url=f"/receive?msg=new_product_error:{err}", status_code=303)
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
    msg = "adjustment_ok" if ok else f"adjustment_error:{err}"
    return RedirectResponse(url=f"/clients?msg={msg}&show_archived={show_archived}", status_code=303)


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

# ---------------- history ----------------

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

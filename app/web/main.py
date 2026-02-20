from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse
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
    # sale by id
    cart_start_by_id,
    cart_add_by_id,
    cart_show_by_id,
    cart_finish_by_id,
    cart_add_by_cart_id,
    cart_show_by_cart_id,
    cart_finish_by_cart_id_shop1416,
)
from app.services.invoice_pdf import generate_invoice_pdf
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
    base = {
        "request": request,
        "warehouses": sorted(WAREHOUSES.keys()),
        "sources": sorted(RECEIVE_SOURCES.keys()),
        "source_labels": RECEIVE_SOURCES,
        "warehouse_labels": WAREHOUSES,
    }
    base.update(ctx)
    return templates.TemplateResponse(name, base)


@app.get("/api/brand-prefixes")
def api_brand_prefixes(brand: str):
    prefixes = list_brand_model_prefixes(brand)
    # front will display with dash: tf -> "tf-"
    return JSONResponse({"brand": brand, "prefixes": prefixes})
    
@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return _render(request, "index.html", {})


# ---------------- products ----------------

@app.get("/products", response_class=HTMLResponse)
def products(request: Request):
    rows = list_products()
    brands = list_brands()
    return _render(request, "products.html", {"products": rows, "brands": brands})


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


# ---------------- stock ----------------

@app.get("/stock", response_class=HTMLResponse)
def stock_get(request: Request, warehouse: str = "", q: str = ""):
    rows = get_stock(warehouse if warehouse else None, q if q else None)
    return _render(
        request,
        "stock.html",
        {
            "rows": rows,
            "warehouses": list(WAREHOUSES.keys()),
            "selected_warehouse": (warehouse or "").strip().upper(),
            "search_q": q or "",
        },
    )


# ---------------- receive ----------------

@app.get("/receive", response_class=HTMLResponse)
def receive_get(request: Request):
    return _render(request, "receive.html", {"ok": None, "message": ""})


@app.post("/receive")
def receive_post(
    warehouse: str = Form(...),
    source: str = Form(...),
    brand: str = Form(...),
    model: str = Form(...),
    qty: float = Form(...),
):
    ok, err = receive_stock(warehouse, brand, model, float(qty), source=source)
    msg = "OK" if ok else err
    return RedirectResponse(url=f"/receive?msg={msg}", status_code=303)


# ---------------- move ----------------

@app.get("/move", response_class=HTMLResponse)
def move_get(request: Request, msg: str = ""):
    return _render(request, "move.html", {"message": msg})


@app.post("/move")
def move_post(
    src: str = Form(...),
    dst: str = Form(...),
    brand: str = Form(...),
    model: str = Form(...),
    qty: float = Form(...),
):
    ok, err = move_stock(src, dst, brand, model, float(qty))
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
def clients_get(request: Request, msg: str = ""):
    clients = list_clients()
    return _render(request, "clients.html", {"clients": clients, "message": msg})


@app.post("/clients/add")
def clients_add(
    name: str = Form(...),
    phone: str = Form(""),
    note: str = Form(""),
):
    ok, err = add_client(name, phone, note)
    msg = "OK" if ok else err
    return RedirectResponse(url=f"/clients?msg={msg}", status_code=303)    
    
    
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


# ---------------- sale (cart) ----------------

@app.get("/sale", response_class=HTMLResponse)
def sale_get(request: Request, msg: str = "", cart_id: str = ""):
    clients = list_clients()
    cid = int(cart_id) if str(cart_id).isdigit() else None
    return _render(
        request,
        "sale.html",
        {"message": msg, "clients": clients, "cart_id": cid},
    )


@app.post("/sale/start")
def sale_start(client_id: int = Form(...)):
    cart_id = cart_start_by_id(int(client_id))
    return RedirectResponse(url=f"/sale?msg=cart_started&cart_id={cart_id}", status_code=303)


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
    return RedirectResponse(url=f"/sale?msg=add:{msg}&cart_id={int(cart_id)}", status_code=303)


@app.post("/sale/show")
def sale_show(cart_id: int = Form(...)):
    ok, text = cart_show_by_cart_id(int(cart_id))
    if not ok:
        return RedirectResponse(url=f"/sale?msg=show:{text}&cart_id={int(cart_id)}", status_code=303)
    return RedirectResponse(url=f"/sale?msg=cart:{text}&cart_id={int(cart_id)}", status_code=303)


@app.post("/sale/finish")
def sale_finish(cart_id: int = Form(...)):
    ok, err, invoice, items = cart_finish_by_cart_id_shop1416(int(cart_id))
    if not ok:
        return RedirectResponse(url=f"/sale?msg=finish:{err}&cart_id={int(cart_id)}", status_code=303)

    pdf_path = generate_invoice_pdf(invoice, items)
    backup_path = make_backup()

    return RedirectResponse(
        url=f"/sale/done?pdf={pdf_path}&backup={backup_path}&n={invoice['number']}",
        status_code=303,
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
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.constants import WAREHOUSES
from app.db.sqlite import (
    init_db,
    list_products,
    add_product,
    get_stock,
    receive_stock,
    move_stock,
    move_all,
    cart_start,
    cart_add,
    cart_show,
    cart_finish,
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
    }
    base.update(ctx)
    return templates.TemplateResponse(name, base)


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return _render(request, "index.html", {})


# ---------------- products ----------------

@app.get("/products", response_class=HTMLResponse)
def products(request: Request):
    rows = list_products()
    return _render(request, "products.html", {"products": rows})


@app.post("/products/add")
def products_add(
    brand: str = Form(...),
    model: str = Form(...),
    name: str = Form(...),
    wh_price: float = Form(...),
):
    add_product(brand, model, name, float(wh_price))
    return RedirectResponse(url="/products", status_code=303)


# ---------------- stock ----------------

@app.get("/stock", response_class=HTMLResponse)
def stock(request: Request, warehouse: Optional[str] = None):
    rows = get_stock(warehouse)
    return _render(
        request,
        "stock.html",
        {
            "rows": rows,
            "selected_warehouse": (warehouse or "").upper(),
        },
    )


# ---------------- receive ----------------

@app.get("/receive", response_class=HTMLResponse)
def receive_get(request: Request):
    return _render(request, "receive.html", {"ok": None, "message": ""})


@app.post("/receive")
def receive_post(
    warehouse: str = Form(...),
    brand: str = Form(...),
    model: str = Form(...),
    qty: float = Form(...),
):
    ok, err = receive_stock(warehouse, brand, model, float(qty))
    msg = "OK" if ok else err
    url = f"/receive?msg={msg}"
    return RedirectResponse(url=url, status_code=303)


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


# ---------------- sale (cart) ----------------

@app.get("/sale", response_class=HTMLResponse)
def sale_get(request: Request, msg: str = ""):
    # простая страница: ввод клиента, добавление позиций, просмотр корзины, завершение
    return _render(request, "sale.html", {"message": msg})


@app.post("/sale/start")
def sale_start(client: str = Form(...)):
    cart_start(client.strip())
    return RedirectResponse(url=f"/sale?msg=cart_started:{client}", status_code=303)


@app.post("/sale/add")
def sale_add(
    client: str = Form(...),
    brand: str = Form(...),
    model: str = Form(...),
    qty: float = Form(...),
    price_mode: str = Form("wh"),
    custom_price: Optional[float] = Form(None),
):
    ok, err = cart_add(client.strip(), brand, model, float(qty), price_mode, custom_price)
    msg = "OK" if ok else err
    return RedirectResponse(url=f"/sale?msg=add:{msg}", status_code=303)


@app.post("/sale/show")
def sale_show(client: str = Form(...)):
    ok, text = cart_show(client.strip())
    if not ok:
        return RedirectResponse(url=f"/sale?msg=show:{text}", status_code=303)
    # покажем корзину через query (быстро), в будущем сделаем красивее
    return RedirectResponse(url=f"/sale?msg=cart:{text}", status_code=303)


@app.post("/sale/finish")
def sale_finish(client: str = Form(...)):
    ok, err, invoice, items = cart_finish(client.strip())
    if not ok:
        return RedirectResponse(url=f"/sale?msg=finish:{err}", status_code=303)

    pdf_path = generate_invoice_pdf(invoice, items)
    backup_path = make_backup()

    # редирект на страницу с ссылками на скачивание
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
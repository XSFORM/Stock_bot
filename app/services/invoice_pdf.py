from __future__ import annotations

from pathlib import Path
from typing import Any

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas


OUT_DIR = Path("/opt/stock_bot/invoices")


def generate_invoice_pdf(invoice: dict[str, Any], items: list[dict[str, Any]]) -> str:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    number = invoice["number"]
    filename = OUT_DIR / f"invoice_{number:06d}.pdf"

    c = canvas.Canvas(str(filename), pagesize=A4)
    width, height = A4

    y = height - 50
    c.setFont("Helvetica-Bold", 14)
    c.drawString(50, y, f"INVOICE #{number:06d}")
    y -= 25

    dt = invoice.get("created_at") or invoice.get("date") or ""
    client = invoice.get("client") or ""

    c.setFont("Helvetica", 11)
    c.drawString(50, y, f"Client: {client}")
    y -= 16
    c.drawString(50, y, f"Date: {dt}")
    y -= 25

    c.setFont("Helvetica-Bold", 11)
    c.drawString(50, y, "Items:")
    y -= 18

    c.setFont("Helvetica", 10)
    for it in items:
        if it.get("free_line"):
            label = str(it.get("free_name") or it.get("name") or "")
        else:
            name_part = it.get("name", "")
            label = f"{it['brand']} {it['model']}" + (f" — {name_part}" if name_part else "") + " — "
        line = f"{label}{it['qty']} x {float(it['unit_price']):.2f}$ = {float(it['total']):.2f}$"
        c.drawString(50, y, line)
        y -= 14
        if y < 80:
            c.showPage()
            y = height - 50
            c.setFont("Helvetica", 10)

    y -= 10
    c.setFont("Helvetica-Bold", 12)
    total = invoice.get("total") or 0
    currency = invoice.get("currency") or ""
    c.drawString(50, y, f"TOTAL: {float(total):.2f} {currency}")

    c.save()
    return str(filename)

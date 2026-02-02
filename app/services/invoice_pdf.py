from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas


def build_invoice_pdf(
    invoice_no: int,
    client_name: str,
    items: List[Dict[str, Any]],
    total: float,
    currency: str = "USD",
) -> str:
    base_dir = Path(__file__).resolve().parents[1]  # .../app
    invoices_dir = base_dir / "data" / "invoices"
    invoices_dir.mkdir(parents=True, exist_ok=True)

    filename = f"invoice_{invoice_no}.pdf"
    path = invoices_dir / filename

    c = canvas.Canvas(str(path), pagesize=A4)
    w, h = A4

    y = h - 50
    c.setFont("Helvetica-Bold", 14)
    c.drawString(40, y, f"INVOICE #{invoice_no}")
    y -= 20

    c.setFont("Helvetica", 11)
    c.drawString(40, y, f"Client: {client_name}")
    y -= 16
    c.drawString(40, y, f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    y -= 30

    c.setFont("Helvetica-Bold", 10)
    c.drawString(40, y, "Item")
    c.drawString(260, y, "Qty")
    c.drawString(320, y, "Price")
    c.drawString(420, y, "Total")
    y -= 12
    c.line(40, y, w - 40, y)
    y -= 16

    c.setFont("Helvetica", 10)
    for it in items:
        name = f"{it['brand']} {it['model']}"
        c.drawString(40, y, name[:34])
        c.drawRightString(300, y, f"{it['qty']:.0f}")
        c.drawRightString(390, y, f"{it['unit_price']:.2f}")
        c.drawRightString(w - 40, y, f"{it['line_total']:.2f}")
        y -= 16
        if y < 80:
            c.showPage()
            y = h - 60
            c.setFont("Helvetica", 10)

    y -= 10
    c.line(40, y, w - 40, y)
    y -= 20

    c.setFont("Helvetica-Bold", 12)
    c.drawRightString(w - 40, y, f"TOTAL: {total:.2f} {currency}")

    c.showPage()
    c.save()
    return str(path)

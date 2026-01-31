from __future__ import annotations

import os
import sqlite3
from datetime import datetime
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from app.config import settings

def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row
    return conn

def generate_invoice_pdf(invoice_id: int) -> str:
    os.makedirs(settings.export_dir, exist_ok=True)

    conn = _connect()
    try:
        inv = conn.execute(
            """
            SELECT i.id, i.created_at, i.total, i.currency, c.name as client_name
            FROM invoices i
            JOIN clients c ON c.id = i.client_id
            WHERE i.id = ?
            """,
            (invoice_id,),
        ).fetchone()
        items = conn.execute(
            """
            SELECT brand, model, name, qty, price, line_total
            FROM invoice_items
            WHERE invoice_id = ?
            ORDER BY brand, model
            """,
            (invoice_id,),
        ).fetchall()
    finally:
        conn.close()

    filename = f"invoice_{invoice_id}.pdf"
    path = os.path.join(settings.export_dir, filename)

    c = canvas.Canvas(path, pagesize=A4)
    w, h = A4

    y = h - 50
    c.setFont("Helvetica-Bold", 14)
    c.drawString(40, y, f"INVOICE #{inv['id']}")
    y -= 20

    c.setFont("Helvetica", 11)
    c.drawString(40, y, f"Client: {inv['client_name']}")
    y -= 16
    c.drawString(40, y, f"Date: {inv['created_at']}")
    y -= 16
    c.drawString(40, y, f"Currency: {inv['currency']}")
    y -= 24

    # header
    c.setFont("Helvetica-Bold", 10)
    c.drawString(40, y, "Item")
    c.drawString(310, y, "Qty")
    c.drawString(360, y, "Price")
    c.drawString(440, y, "Total")
    y -= 10
    c.line(40, y, 550, y)
    y -= 16

    c.setFont("Helvetica", 10)
    for it in items:
        item_name = f"{it['brand']} {it['model']} - {it['name']}"
        c.drawString(40, y, item_name[:45])
        c.drawRightString(340, y, f"{float(it['qty']):.2f}")
        c.drawRightString(420, y, f"{float(it['price']):.2f}")
        c.drawRightString(550, y, f"{float(it['line_total']):.2f}")
        y -= 14
        if y < 80:
            c.showPage()
            y = h - 50
            c.setFont("Helvetica", 10)

    y -= 10
    c.line(40, y, 550, y)
    y -= 18
    c.setFont("Helvetica-Bold", 12)
    c.drawRightString(550, y, f"TOTAL: {float(inv['total']):.2f} {inv['currency']}")

    c.save()
    return path

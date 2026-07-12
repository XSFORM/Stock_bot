"""
Sale invoice PDF generator (server / web edition).

The PDF is built to look like the styled XLSX invoice:
  - "INVOICE #NNNNNN" title at top, centered
  - Optional company stamp/logo PNG in the top-right corner
  - "Client:" and "Date:" lines below the title
  - A bordered table with a blue header row and a light-blue TOTAL row
    (TOTAL label in the Model column, qty sum in Qty, amount in Total)

Font: DejaVu Sans is used when available (full Cyrillic / Turkmen support);
falls back to Helvetica otherwise.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer,
)

from app.utils.money import calc_document_total, calc_line_total, round_money


OUT_DIR = Path("/opt/stock_bot/invoices")
STAMP_PATH = Path("/opt/stock_bot/stamp.png")
STAMP_W_MM = 55  # mm
STAMP_H_MM = 22  # mm


# ── Font registration (Cyrillic / Turkmen support) ───────────────────────────

_DEJAVU_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
]
_DEJAVU_BOLD = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
]
_DEJAVU_BOLDIT = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-BoldOblique.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-BoldOblique.ttf",
]


def _first_existing(paths):
    for p in paths:
        if Path(p).exists():
            return p
    return None


def _register_fonts():
    base = _first_existing(_DEJAVU_CANDIDATES)
    bold = _first_existing(_DEJAVU_BOLD)
    bolit = _first_existing(_DEJAVU_BOLDIT)
    if base and bold and bolit:
        try:
            pdfmetrics.registerFont(TTFont("DejaVu", base))
            pdfmetrics.registerFont(TTFont("DejaVu-Bold", bold))
            pdfmetrics.registerFont(TTFont("DejaVu-BoldOblique", bolit))
            return "DejaVu", "DejaVu-Bold", "DejaVu-BoldOblique"
        except Exception:
            pass
    # fallback
    return "Helvetica", "Helvetica-Bold", "Helvetica-BoldOblique"


FONT, FONT_BOLD, FONT_BOLDIT = _register_fonts()


# ── Colours matching invoice_xlsx.py ────────────────────────────────────────

HEADER_FILL = colors.HexColor("#4472C4")  # blue header
TOTAL_FILL  = colors.HexColor("#D9EEF7")  # light blue total row
BORDER      = colors.HexColor("#000000")  # solid black, like in XLSX


# ── Stamp drawing (top-right of every page) ──────────────────────────────────

def _draw_stamp(canvas, doc):
    if not STAMP_PATH.exists():
        return
    try:
        canvas.saveState()
        page_w, page_h = A4
        margin = 8 * mm
        w = STAMP_W_MM * mm
        h = STAMP_H_MM * mm
        x = page_w - margin - w
        y = page_h - margin - h
        canvas.drawImage(
            str(STAMP_PATH), x, y, width=w, height=h,
            preserveAspectRatio=True, mask="auto",
        )
        canvas.restoreState()
    except Exception:
        # Stamp is optional - never fail PDF generation because of it
        pass


# ── Main entry point ─────────────────────────────────────────────────────────

def generate_invoice_pdf(invoice: dict[str, Any],
                         items: list[dict[str, Any]]) -> str:
    """Render a sale invoice to PDF and return the absolute path."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    number = int(invoice["number"])
    filename = OUT_DIR / f"invoice_{number:06d}.pdf"

    doc = SimpleDocTemplate(
        str(filename),
        pagesize=A4,
        leftMargin=10 * mm,
        rightMargin=10 * mm,
        topMargin=10 * mm,
        bottomMargin=15 * mm,
        title=f"Invoice {number:06d}",
    )

    # Styles
    title_style = ParagraphStyle(
        "title", fontName=FONT_BOLD, fontSize=14,
        alignment=1, spaceAfter=2 * mm,  # 1 = center
    )
    info_style = ParagraphStyle(
        "info", fontName=FONT, fontSize=10,
        leading=12, spaceAfter=0,
    )

    story = []

    # 1. Title (centered)
    story.append(Paragraph(f"INVOICE #{number:06d}", title_style))

    # 2. Client + Date (left-aligned, single column - stamp sits on top-right via canvas)
    client = str(invoice.get("client") or "")
    dt = str(invoice.get("created_at", invoice.get("date", "")))[:16].replace("T", " ")
    story.append(Paragraph(f"<b>Client:</b> {_esc(client)}", info_style))
    story.append(Paragraph(f"<b>Date:</b> {_esc(dt)}", info_style))
    story.append(Spacer(1, 4 * mm))

    # 3. Items table
    data = [["#", "Model", "Name", "Barcode", "Qty", "Unit Price", "Total"]]
    items_qty = 0
    for idx, item in enumerate(items, start=1):
        if item.get("free_line"):
            model_val = str(item.get("free_name") or item.get("name") or "")
            name_val = ""
        else:
            model_val = f"{item.get('brand','')} {item.get('model','')}".strip()
            name_val = str(item.get("name", "") or "")
        qty = float(item.get("qty", 0) or 0)
        unit_price = float(item.get("unit_price", 0) or 0)
        line_total = calc_line_total(unit_price, qty)
        items_qty += int(qty)
        data.append([
            str(idx),
            model_val,
            name_val,
            str(item.get("barcode") or ""),
            _fmt_qty(qty),
            f"{round_money(unit_price):.2f}",
            f"{line_total:.2f}",
        ])

    invoice_total = calc_document_total(items, "unit_price")
    # Total row: TOTAL label in column 1 (Model), qty in column 4 (Qty),
    # amount in column 6 (Total). Other cells empty but still styled.
    data.append(["", "TOTAL", "", "", str(items_qty), "", f"{invoice_total:.2f}"])

    col_widths = [
        10 * mm,   # #
        35 * mm,   # Model
        50 * mm,   # Name
        32 * mm,   # Barcode
        14 * mm,   # Qty
        22 * mm,   # Unit Price
        22 * mm,   # Total
    ]

    n_rows = len(data)
    last = n_rows - 1

    style = TableStyle([
        # All cells: grid + base font + padding
        ("GRID",        (0, 0), (-1, -1), 0.5, BORDER),
        ("FONTNAME",    (0, 0), (-1, -1), FONT),
        ("FONTSIZE",    (0, 0), (-1, -1), 9),
        ("VALIGN",      (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING",(0, 0), (-1, -1), 4),
        ("TOPPADDING",  (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 3),
        # Header row
        ("BACKGROUND",  (0, 0), (-1, 0), HEADER_FILL),
        ("TEXTCOLOR",   (0, 0), (-1, 0), colors.white),
        ("FONTNAME",    (0, 0), (-1, 0), FONT_BOLD),
        ("FONTSIZE",    (0, 0), (-1, 0), 10),
        ("ALIGN",       (0, 0), (-1, 0), "CENTER"),
        # Data row alignment (exclude header [row 0] and total [row last])
        ("ALIGN",       (0, 1), (0, last - 1), "CENTER"),   # #
        ("ALIGN",       (4, 1), (4, last - 1), "CENTER"),   # Qty
        ("ALIGN",       (5, 1), (6, last - 1), "RIGHT"),    # Unit Price + Total
        # TOTAL row
        ("BACKGROUND",  (0, last), (-1, last), TOTAL_FILL),
        ("FONTNAME",    (1, last), (1, last), FONT_BOLDIT),
        ("FONTNAME",    (4, last), (4, last), FONT_BOLD),
        ("FONTNAME",    (6, last), (6, last), FONT_BOLD),
        ("FONTSIZE",    (0, last), (-1, last), 10),
        ("ALIGN",       (1, last), (1, last), "LEFT"),
        ("ALIGN",       (4, last), (4, last), "CENTER"),
        ("ALIGN",       (6, last), (6, last), "RIGHT"),
    ])

    table = Table(data, colWidths=col_widths, repeatRows=1)
    table.setStyle(style)
    story.append(table)

    # Stamp is drawn ONLY on page 1 — on continuation pages it would overlap the table.
    doc.build(story, onFirstPage=_draw_stamp)
    return str(filename)


# ── helpers ──────────────────────────────────────────────────────────────────

def _esc(text: str) -> str:
    """HTML-escape for Paragraph (it accepts a small XML subset)."""
    return (
        str(text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _fmt_qty(qty: float) -> str:
    """Render qty without trailing zeros: 2.0 -> '2', 1.5 -> '1.5'."""
    if qty == int(qty):
        return str(int(qty))
    return f"{qty:g}"

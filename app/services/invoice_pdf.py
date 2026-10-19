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


# ── Localised labels (en / ru / tm) ──────────────────────────────────────────
# Minimal: just the strings used by the printable sale invoice. Pass `lang`
# into generate_invoice_pdf() to pick the set; unknown codes fall back to en.
_PDF_STRINGS = {
    "en": {
        "invoice":    "INVOICE",
        "client":     "Client",
        "date":       "Date",
        "col_num":    "#",
        "col_model":  "Model",
        "col_name":   "Name",
        "col_barcode":"Barcode",
        "col_qty":    "Qty",
        "col_price":  "Unit Price",
        "col_total":  "Total",
        "subtotal":   "Subtotal",
        "discount":   "Discount",
        "total":      "TOTAL",
        "seller":     "Seller",
        "buyer":      "Buyer",
    },
    "ru": {
        "invoice":    "НАКЛАДНАЯ",
        "client":     "Клиент",
        "date":       "Дата",
        "col_num":    "№",
        "col_model":  "Модель",
        "col_name":   "Название",
        "col_barcode":"Штрих-код",
        "col_qty":    "Кол-во",
        "col_price":  "Цена",
        "col_total":  "Сумма",
        "subtotal":   "Подытог",
        "discount":   "Скидка",
        "total":      "ИТОГО",
        "seller":     "Продавец",
        "buyer":      "Покупатель",
    },
    "tm": {
        "invoice":    "HASAP-FAKTURA",
        "client":     "Müşderi",
        "date":       "Sene",
        "col_num":    "№",
        "col_model":  "Model",
        "col_name":   "Ady",
        "col_barcode":"Ştrih-kod",
        "col_qty":    "Sany",
        "col_price":  "Baha",
        "col_total":  "Jemi",
        "subtotal":   "Aralyk jem",
        "discount":   "Arzanladyş",
        "total":      "JEMI",
        "seller":     "Satyjy",
        "buyer":      "Alyjy",
    },
}


def _strings(lang: str) -> dict[str, str]:
    """Return the label set for the given UI language, falling back to en."""
    return _PDF_STRINGS.get((lang or "").lower(), _PDF_STRINGS["en"])


# ── Font registration (Cyrillic / Turkmen support) ───────────────────────────

# Font lookup paths. The bundled copy under app/services/fonts/ is tried
# FIRST so a fresh deploy on any OS works out of the box — Linux boxes
# without `fonts-dejavu`, Windows boxes without DejaVu installed system-
# wide. System paths are only a fallback.
_FONTS_DIR = str(Path(__file__).parent / "fonts")

_DEJAVU_CANDIDATES = [
    f"{_FONTS_DIR}/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    r"C:\Windows\Fonts\DejaVuSans.ttf",
]
_DEJAVU_BOLD = [
    f"{_FONTS_DIR}/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    r"C:\Windows\Fonts\DejaVuSans-Bold.ttf",
]
_DEJAVU_BOLDIT = [
    f"{_FONTS_DIR}/DejaVuSans-BoldOblique.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-BoldOblique.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-BoldOblique.ttf",
    r"C:\Windows\Fonts\DejaVuSans-BoldOblique.ttf",
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
                         items: list[dict[str, Any]],
                         lang: str = "en") -> str:
    """Render a sale invoice to PDF and return the absolute path.

    `lang` localises the labels (header, table columns, total block,
    signature lines) — pass "en" / "ru" / "tm". Numbers and names
    (client, products, barcodes) stay as-is. Unknown lang → en.
    """
    t = _strings(lang)
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
    story.append(Paragraph(f"{t['invoice']} #{number:06d}", title_style))

    # 2. Client + Date (left-aligned, single column - stamp sits on top-right via canvas)
    client = str(invoice.get("client") or "")
    dt = str(invoice.get("created_at", invoice.get("date", "")))[:16].replace("T", " ")
    story.append(Paragraph(f"<b>{t['client']}:</b> {_esc(client)}", info_style))
    story.append(Paragraph(f"<b>{t['date']}:</b> {_esc(dt)}", info_style))
    story.append(Spacer(1, 4 * mm))

    # 3. Items table
    data = [[t["col_num"], t["col_model"], t["col_name"], t["col_barcode"],
             t["col_qty"], t["col_price"], t["col_total"]]]
    items_qty = 0
    for idx, item in enumerate(items, start=1):
        if item.get("free_line"):
            # Phase 10: free line has its own brand/model/name/barcode columns.
            brand_part = (item.get("free_brand") or "").strip()
            model_part = (item.get("free_model") or "").strip()
            model_val   = (brand_part + " " + model_part).strip()
            name_val    = str(item.get("free_name")    or "")
            barcode_val = str(item.get("free_barcode") or "")
        else:
            model_val   = f"{item.get('brand','')} {item.get('model','')}".strip()
            name_val    = str(item.get("name", "") or "")
            barcode_val = str(item.get("barcode") or "")
        qty = float(item.get("qty", 0) or 0)
        unit_price = float(item.get("unit_price", 0) or 0)
        line_total = calc_line_total(unit_price, qty)
        items_qty += int(qty)
        data.append([
            str(idx),
            model_val,
            name_val,
            barcode_val,
            _fmt_qty(qty),
            f"{round_money(unit_price):.2f}",
            f"{line_total:.2f}",
        ])

    # Phase 11: discount support. Up to three total rows now:
    #   Subtotal / Discount X% / TOTAL. Legacy invoices (pct == 0) still
    #   render as the single-row TOTAL from before.
    subtotal_val = calc_document_total(items, "unit_price")
    discount_pct = float(invoice.get("discount_pct") or 0)
    discount_amount = round(subtotal_val * discount_pct / 100.0, 2) if discount_pct > 0 else 0.0
    grand_total = round(subtotal_val - discount_amount, 2)

    if discount_pct > 0:
        data.append(["", t["subtotal"],                        "", "", str(items_qty),
                     "", f"{subtotal_val:.2f}"])
        data.append(["", f"{t['discount']} {discount_pct:g}%", "", "", "",
                     "", f"-{discount_amount:.2f}"])
        data.append(["", t["total"],                           "", "", "",
                     "", f"{grand_total:.2f}"])
    else:
        data.append(["", t["total"], "", "", str(items_qty), "", f"{grand_total:.2f}"])

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
    # Phase 11: when discount is present we have THREE total rows at the
    # bottom (Subtotal/Discount/TOTAL) instead of one — style them all.
    n_total_rows = 3 if discount_pct > 0 else 1
    first_total = last - (n_total_rows - 1)

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
        # Data row alignment (exclude header [row 0] and all total rows)
        ("ALIGN",       (0, 1), (0, first_total - 1), "CENTER"),   # #
        ("ALIGN",       (4, 1), (4, first_total - 1), "CENTER"),   # Qty
        ("ALIGN",       (5, 1), (6, first_total - 1), "RIGHT"),    # Unit Price + Total
        # All total rows (Subtotal / Discount / TOTAL) — same background & alignment.
        ("BACKGROUND",  (0, first_total), (-1, last), TOTAL_FILL),
        ("FONTNAME",    (1, first_total), (1, last), FONT_BOLDIT),
        ("FONTNAME",    (4, first_total), (4, last), FONT_BOLD),
        ("FONTNAME",    (6, first_total), (6, last), FONT_BOLD),
        ("FONTSIZE",    (0, first_total), (-1, last), 10),
        ("ALIGN",       (1, first_total), (1, last), "LEFT"),
        ("ALIGN",       (4, first_total), (4, last), "CENTER"),
        ("ALIGN",       (6, first_total), (6, last), "RIGHT"),
    ])

    table = Table(data, colWidths=col_widths, repeatRows=1)
    table.setStyle(style)
    story.append(table)

    # ── Signature block ─────────────────────────────────────────────────────
    # Two side-by-side columns: Seller on the left, Buyer on the right.
    # English-only labels — bold Cyrillic doesn't render reliably on servers
    # that only have Latin-subset variants of DejaVu Sans Bold installed.
    story.append(Spacer(1, 15 * mm))

    sig_style = ParagraphStyle(
        "sig", fontName=FONT, fontSize=10, leading=14,
    )
    # Overlay the seller's stored handwritten signature over the line, if any.
    # Buyer side always stays as a blank line for the client to sign manually.
    # Scale by HEIGHT so a square-ish scan doesn't stretch and overlap rows.
    from app.services.signature_asset import get_signature_path, signature_fit
    _sig_png = get_signature_path()
    _sig_size = signature_fit(160, 50)  # pt — fits a 50mm×17mm box max
    if _sig_png is not None and _sig_size is not None:
        _sw, _sh = _sig_size
        seller_html = (
            f'{t["seller"]}: '
            f'<img src="{_sig_png}" width="{_sw}" height="{_sh}" valign="middle"/>'
        )
    else:
        seller_html = f"{t['seller']}: ______________________________"
    sig_cell_seller = Paragraph(seller_html, sig_style)
    sig_cell_buyer  = Paragraph(f"{t['buyer']}: ______________________________",  sig_style)

    sig_table = Table(
        [[sig_cell_seller, sig_cell_buyer]],
        colWidths=[87 * mm, 87 * mm],
    )
    sig_table.setStyle(TableStyle([
        ("VALIGN",       (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING",  (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING",   (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 0),
    ]))
    story.append(sig_table)

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

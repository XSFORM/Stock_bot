"""Phase 13 — stock inventory PDF (portrait A4).

Mirrors the look of the sale-invoice PDF (DejaVu font for ru/tm/en,
blue header, light-blue total row). Columns are configurable the same
way as stock_xlsx: income + any subset of markup_pcts can be on/off.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Iterable

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer,
)


# ── Font registration (reuses the invoice-PDF lookup chain) ─────────────────
# Same bundled-first / system-fallback pattern as invoice_pdf.py so the two
# PDFs render identically on any machine.
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


def _first_existing(paths):
    for p in paths:
        if Path(p).exists():
            return p
    return None


def _register_fonts():
    base = _first_existing(_DEJAVU_CANDIDATES)
    bold = _first_existing(_DEJAVU_BOLD)
    if base and bold:
        try:
            pdfmetrics.registerFont(TTFont("DejaVuS", base))
            pdfmetrics.registerFont(TTFont("DejaVuS-Bold", bold))
            return "DejaVuS", "DejaVuS-Bold"
        except Exception:
            pass
    return "Helvetica", "Helvetica-Bold"


FONT, FONT_BOLD = _register_fonts()

HEADER_FILL = colors.HexColor("#4472C4")
TOTAL_FILL  = colors.HexColor("#D9EEF7")
BORDER      = colors.HexColor("#000000")


_STRINGS = {
    # Export labels for clients. Markup columns print as the neutral
    # word "Price" — clients don't need to know whether a given price
    # was computed from +15 % or +25 % off the operator's purchase price.
    "en": {"title":"Stock Inventory", "num":"#", "brand":"Brand",
           "model":"Model", "name":"Name", "income":"Income", "price":"Price",
           "qty":"Qty", "total":"TOTAL", "generated":"Generated"},
    "ru": {"title":"Остатки склада", "num":"№", "brand":"Бренд",
           "model":"Модель", "name":"Название", "income":"Приход", "price":"Цена",
           "qty":"Кол-во", "total":"ИТОГО", "generated":"Сформировано"},
    "tm": {"title":"Ammar galyndylary", "num":"№", "brand":"Brend",
           "model":"Model", "name":"Ady", "income":"Girdeji", "price":"Baha",
           "qty":"Sany", "total":"JEMI", "generated":"Döredildi"},
}


def _strings(lang: str) -> dict[str, str]:
    return _STRINGS.get((lang or "en").lower(), _STRINGS["en"])


def generate_stock_pdf_bytes(
    rows: list[dict[str, Any]],
    warehouse_label: str,
    *,
    show_income: bool = True,
    markup_pcts: Iterable[int] = (),
    hide_zero: bool = False,
    lang: str = "en",
) -> bytes:
    """Return A4-portrait stock PDF as bytes."""
    import io
    t = _strings(lang)
    data_rows = [r for r in rows
                 if not (hide_zero and float(r.get("qty") or 0) == 0)]
    markup_pcts = list(markup_pcts)

    headers = [t["num"], t["brand"], t["model"], t["name"]]
    if show_income:
        headers.append(t["income"])
    # Clients see "Цена" / "Price" / "Baha", not the markup percentage.
    for pct in markup_pcts:
        headers.append(t["price"])
    headers.append(t["qty"])
    n_cols = len(headers)

    # Column widths — divide remaining space proportionally so it fits A4.
    # A4 portrait usable width ≈ 190 mm after 10 mm margins.
    # Fixed widths for #, Qty; everything else shares the rest.
    fixed = {0: 8 * mm, n_cols - 1: 14 * mm}  # # and Qty
    price_cols = sum(1 for _ in [None] * (1 if show_income else 0)) + len(markup_pcts)
    text_cols = 3  # brand, model, name
    remaining = 190 * mm - sum(fixed.values()) - price_cols * 18 * mm
    name_w  = max(50 * mm, remaining * 0.55)
    brand_w = max(22 * mm, (remaining - name_w) / 2)
    model_w = max(22 * mm, (remaining - name_w - brand_w))

    col_widths = []
    for i in range(n_cols):
        if i in fixed:
            col_widths.append(fixed[i])
        elif i == 1:
            col_widths.append(brand_w)
        elif i == 2:
            col_widths.append(model_w)
        elif i == 3:
            col_widths.append(name_w)
        else:
            col_widths.append(18 * mm)

    doc_buf = io.BytesIO()
    doc = SimpleDocTemplate(
        doc_buf, pagesize=A4,
        leftMargin=10 * mm, rightMargin=10 * mm,
        topMargin=10 * mm, bottomMargin=12 * mm,
        title=f"Stock — {warehouse_label}",
    )

    story = []
    title_style = ParagraphStyle(
        "title", fontName=FONT_BOLD, fontSize=14,
        alignment=1, spaceAfter=1 * mm,
    )
    sub_style = ParagraphStyle(
        "sub", fontName=FONT, fontSize=9,
        alignment=1, textColor=colors.grey, spaceAfter=4 * mm,
    )
    story.append(Paragraph(f"{t['title']} — {warehouse_label}", title_style))
    story.append(Paragraph(
        f"{t['generated']}: {date.today().isoformat()}", sub_style,
    ))

    data = [headers]
    for i, r in enumerate(data_rows, start=1):
        wh_price = float(r.get("wh_price") or 0)
        row = [str(i), str(r.get("brand") or ""),
               str(r.get("model") or ""), str(r.get("name") or "")]
        if show_income:
            row.append(f"{wh_price:.2f}")
        for pct in markup_pcts:
            row.append(f"{wh_price * (1 + pct / 100.0):.2f}")
        row.append(str(int(r.get("qty") or 0)))
        data.append(row)

    # Total row
    total_qty = sum(int(r.get("qty") or 0) for r in data_rows)
    total_row = [""] * n_cols
    total_row[1] = t["total"]
    total_row[-1] = str(total_qty)
    data.append(total_row)

    last = len(data) - 1
    style = TableStyle([
        ("GRID",        (0, 0), (-1, -1), 0.5, BORDER),
        ("FONTNAME",    (0, 0), (-1, -1), FONT),
        ("FONTSIZE",    (0, 0), (-1, -1), 8),
        ("VALIGN",      (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING",(0, 0), (-1, -1), 3),
        ("TOPPADDING",  (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 2),
        # Header
        ("BACKGROUND",  (0, 0), (-1, 0), HEADER_FILL),
        ("TEXTCOLOR",   (0, 0), (-1, 0), colors.white),
        ("FONTNAME",    (0, 0), (-1, 0), FONT_BOLD),
        ("FONTSIZE",    (0, 0), (-1, 0), 9),
        ("ALIGN",       (0, 0), (-1, 0), "CENTER"),
        # Numeric columns right-aligned on data rows (price blocks + qty)
        ("ALIGN",       (0, 1), (0, last - 1), "CENTER"),
        ("ALIGN",       (4, 1), (-1, last - 1), "RIGHT"),
        # Total row
        ("BACKGROUND",  (0, last), (-1, last), TOTAL_FILL),
        ("FONTNAME",    (1, last), (1, last), FONT_BOLD),
        ("FONTNAME",    (-1, last), (-1, last), FONT_BOLD),
        ("ALIGN",       (-1, last), (-1, last), "RIGHT"),
    ])
    table = Table(data, colWidths=col_widths, repeatRows=1)
    table.setStyle(style)
    story.append(table)

    doc.build(story)
    return doc_buf.getvalue()

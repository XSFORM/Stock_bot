"""
Client-history export (PDF / XLSX / JPEG).

The intended audience is the client themselves — often elderly bazaar
customers who grew up on paper ledgers with just two columns: what they
bought (+) and what they paid (−). So the output is deliberately stripped
of everything they do not need to see: no invoice numbers, no action
buttons, no technical type badges — just date, a plain-Russian/Turkmen
word for the operation, a signed amount, and the running debt after.

Three formats share one data pipeline:
  * PDF   — printable A4, DejaVu font so Cyrillic / Turkmen render.
  * XLSX  — same table in a spreadsheet shape.
  * JPEG  — a single tall image for sending in IMO / WhatsApp / Telegram.

Only the last N (default 15) events are rendered unless the caller asks
for a date-range or a larger limit; a long history on paper is exactly the
kind of clutter we're trying to avoid for this audience.
"""

from __future__ import annotations

import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from PIL import Image, ImageDraw, ImageFont

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)


# ── Output directory ────────────────────────────────────────────────────────
#
# Honour INVOICES_DIR when present (desktop edition sets it to the user's
# writable data dir), otherwise fall back to /opt/stock_bot/invoices on the
# server, and finally to the system temp dir — guaranteed writable on any
# OS so the service never crashes while generating a client's history.

def _out_dir() -> Path:
    env = os.getenv("INVOICES_DIR", "").strip()
    if env:
        p = Path(env) / "client_history"
    else:
        legacy = Path("/opt/stock_bot/invoices/client_history")
        try:
            legacy.mkdir(parents=True, exist_ok=True)
            p = legacy
        except OSError:
            p = Path(tempfile.gettempdir()) / "stock_bot_client_history"
    p.mkdir(parents=True, exist_ok=True)
    return p


# ── Localisation (ru / en / tm) ─────────────────────────────────────────────

_LABELS = {
    "ru": {
        "title":       "История клиента",
        "client":      "Клиент",
        "phone":       "Телефон",
        "period":      "Период",
        "balance":     "Остаток долга",
        "balance_now": "Общий долг",
        "col_date":    "Дата",
        "col_op":      "Операция",
        "col_amount":  "Сумма",
        "col_balance": "Остался должен",
        "col_note":    "Заметка",
        "op_invoice":  "Товар взял",
        "op_payment":  "Заплатил",
        "op_return":   "Возврат",
        "op_debt_add": "Долг",
        "no_events":   "Нет движений за выбранный период",
        "seller":      "Продавец",
        "signature":   "Подпись клиента",
        "generated":   "Создано",
        "advance":     "предоплата",
        "owes":        "долг",
        "period_last_n": "последние {n} записей",
    },
    "en": {
        "title":       "Client history",
        "client":      "Client",
        "phone":       "Phone",
        "period":      "Period",
        "balance":     "Outstanding debt",
        "balance_now": "Total debt",
        "col_date":    "Date",
        "col_op":      "Operation",
        "col_amount":  "Amount",
        "col_balance": "Debt remaining",
        "col_note":    "Note",
        "op_invoice":  "Took goods",
        "op_payment":  "Paid",
        "op_return":   "Return",
        "op_debt_add": "Debt",
        "no_events":   "No movements in the selected period",
        "seller":      "Seller",
        "signature":   "Client signature",
        "generated":   "Generated",
        "advance":     "advance",
        "owes":        "owes",
        "period_last_n": "last {n} records",
    },
    "tm": {
        "title":       "Müşderi taryhy",
        "client":      "Müşderi",
        "phone":       "Telefon",
        "period":      "Döwür",
        "balance":     "Galan bergi",
        "balance_now": "Umumy bergi",
        "col_date":    "Sene",
        "col_op":      "Hereket",
        "col_amount":  "Mukdary",
        "col_balance": "Galan bergi",
        "col_note":    "Bellik",
        "op_invoice":  "Haryt aldy",
        "op_payment":  "Töledi",
        "op_return":   "Yza gaýtardy",
        "op_debt_add": "Bergi",
        "no_events":   "Saýlanan döwürde hereket ýok",
        "seller":      "Satyjy",
        "signature":   "Müşderiniň goly",
        "generated":   "Döredildi",
        "advance":     "öňünden",
        "owes":        "bergi",
        "period_last_n": "iň soňky {n} ýazgy",
    },
}


def _t(lang: str) -> dict[str, str]:
    return _LABELS.get((lang or "").lower(), _LABELS["ru"])


# ── Font paths (shared with invoice_pdf for cross-OS resilience) ────────────

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


def _first_existing(paths: Iterable[str]) -> str | None:
    for p in paths:
        if Path(p).exists():
            return p
    return None


_FONT_REGULAR_PATH = _first_existing(_DEJAVU_CANDIDATES)
_FONT_BOLD_PATH = _first_existing(_DEJAVU_BOLD)


def _register_pdf_fonts() -> tuple[str, str]:
    if _FONT_REGULAR_PATH and _FONT_BOLD_PATH:
        try:
            pdfmetrics.registerFont(TTFont("CH-Regular", _FONT_REGULAR_PATH))
            pdfmetrics.registerFont(TTFont("CH-Bold", _FONT_BOLD_PATH))
            return "CH-Regular", "CH-Bold"
        except Exception:
            pass
    return "Helvetica", "Helvetica-Bold"


_FONT, _FONT_BOLD = _register_pdf_fonts()


# ── Shared event → row mapping ──────────────────────────────────────────────

def _classify(ev: dict[str, Any], lang: str) -> tuple[str, float, str]:
    """Return (op_label, signed_delta, sign_char) for a history event.

    signed_delta is the signed change to the client's debt (positive =
    debt grew, negative = debt shrank). sign_char is "+" or "−" for the
    amount display, picked so the elderly client immediately sees whether
    this row moved them deeper in or out of debt.
    """
    t = _t(lang)
    kind = str(ev.get("kind") or "")
    amount = float(ev.get("amount") or 0)
    if kind == "INVOICE":
        return t["op_invoice"], amount, "+"
    if kind == "RETURN":
        return t["op_return"], -amount, "−"
    if kind == "LEDGER":
        if amount > 0:
            # Positive ledger entries are payments → debt down.
            return t["op_payment"], -amount, "−"
        if amount < 0:
            # Negative ledger entries are manual debt additions → debt up.
            return t["op_debt_add"], -amount, "+"
        return t["op_payment"], 0.0, " "
    return str(kind), amount, "+"


def _fmt_date(dt: str | None) -> str:
    s = str(dt or "")
    # events arrive as ISO-ish "YYYY-MM-DD HH:MM:SS" (or with T) — reduce
    # to "YYYY-MM-DD" since a notebook never has seconds in it.
    return s[:10].replace("T", "")


def _ru_plural(n: int, one: str, few: str, many: str) -> str:
    """Pick the right Russian noun form for a count (1 / 2–4 / 5+ rule)."""
    nn = abs(int(n))
    if 10 <= nn % 100 <= 20:
        return many
    tail = nn % 10
    if tail == 1:
        return one
    if 2 <= tail <= 4:
        return few
    return many


def _fmt_last_n(n: int, lang: str) -> str:
    """Localised "last N records" with correct grammar per language."""
    if (lang or "").lower() == "ru":
        word = _ru_plural(n, "запись", "записи", "записей")
        prefix = "последняя" if _ru_plural(n, "a", "b", "c") == "a" else "последние"
        return f"{prefix} {n} {word}"
    if (lang or "").lower() == "tm":
        return f"iň soňky {n} ýazgy"
    word = "record" if n == 1 else "records"
    return f"last {n} {word}"


def _fmt_period(
    date_from: str | None,
    date_to: str | None,
    lang: str,
    shown_count: int | None = None,
) -> str:
    """Human-readable period line.

    If dates are picked we print them literally. If neither is picked but
    the caller passed the row count we show "last N records" instead of
    a lonely dash — the elderly reader gets a clear statement of how
    much of the ledger is on this page.
    """
    f = (date_from or "").strip()
    tto = (date_to or "").strip()
    if f and tto:
        return f"{f} — {tto}"
    if f:
        return f"≥ {f}"
    if tto:
        return f"≤ {tto}"
    if shown_count is not None and shown_count > 0:
        return _fmt_last_n(shown_count, lang)
    return "—"


def _clean_note(note: str | None) -> str:
    s = str(note or "").strip()
    # Strip the "· Telegram-бот" marker that the HTML view renders as a
    # separate badge — in a printable file it would be noise.
    s = s.replace(" · Telegram-бот", "").replace("Telegram-бот", "").strip()
    return s


def _filename_stem(client: dict[str, Any]) -> str:
    cid = int(client.get("id") or 0)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"client_{cid:04d}_history_{stamp}"


# ── PDF ─────────────────────────────────────────────────────────────────────

_HEADER_FILL = colors.HexColor("#4472C4")
_HEADER_TEXT = colors.white
_PLUS_FILL   = colors.HexColor("#FDECEA")
_MINUS_FILL  = colors.HexColor("#E8F5E9")
_BORDER      = colors.HexColor("#000000")


# ── Optional company stamp (reuses the invoice stamp image) ─────────────────
#
# On the server the stamp lives at /opt/stock_bot/stamp.png (same path as
# invoice_pdf.STAMP_PATH); on desktop it's absent, so we silently skip it.
# STOCK_BOT_STAMP_PATH lets the admin override the location.

_STAMP_PATH = Path(os.getenv("STOCK_BOT_STAMP_PATH", "/opt/stock_bot/stamp.png"))
_STAMP_PDF_W_MM = 50
_STAMP_PDF_H_MM = 20
_STAMP_JPG_W_PX = 180        # width when pasted into the JPEG
_STAMP_XLSX_W_PX = 180       # width when embedded in the XLSX


def _draw_stamp_pdf(canvas, doc) -> None:
    """SimpleDocTemplate onFirstPage callback — draws the stamp top-right."""
    if not _STAMP_PATH.exists():
        return
    try:
        canvas.saveState()
        page_w, page_h = A4
        margin = 10 * mm
        w = _STAMP_PDF_W_MM * mm
        h = _STAMP_PDF_H_MM * mm
        x = page_w - margin - w
        y = page_h - margin - h
        canvas.drawImage(
            str(_STAMP_PATH), x, y, width=w, height=h,
            preserveAspectRatio=True, mask="auto",
        )
        canvas.restoreState()
    except Exception:
        # Stamp is optional — never fail generation because of it.
        pass


def generate_client_history_pdf(
    client: dict[str, Any],
    events: list[dict[str, Any]],
    final_balance: float,
    date_from: str | None = None,
    date_to: str | None = None,
    lang: str = "ru",
) -> str:
    """Render a simplified client-history PDF and return its absolute path."""

    t = _t(lang)
    filename = _out_dir() / f"{_filename_stem(client)}.pdf"

    doc = SimpleDocTemplate(
        str(filename),
        pagesize=A4,
        leftMargin=14 * mm,
        rightMargin=14 * mm,
        topMargin=12 * mm,
        bottomMargin=14 * mm,
        title=f"Client history {client.get('id')}",
    )

    title_style = ParagraphStyle(
        "ch_title", fontName=_FONT_BOLD, fontSize=16, alignment=1, spaceAfter=4 * mm,
    )
    info_style = ParagraphStyle(
        "ch_info", fontName=_FONT, fontSize=11, leading=14, spaceAfter=0,
    )
    small_style = ParagraphStyle(
        "ch_small", fontName=_FONT, fontSize=9, textColor=colors.grey, alignment=2,
    )

    story: list[Any] = []
    story.append(Paragraph(t["title"], title_style))

    client_name = str(client.get("name") or "")
    phone = str(client.get("phone") or "").strip()
    story.append(Paragraph(f"<b>{t['client']}:</b> {_esc(client_name)}", info_style))
    if phone:
        story.append(Paragraph(f"<b>{t['phone']}:</b> {_esc(phone)}", info_style))
    period_str = _fmt_period(date_from, date_to, lang, shown_count=len(events))
    story.append(Paragraph(f"<b>{t['period']}:</b> {_esc(period_str)}", info_style))
    story.append(Spacer(1, 4 * mm))

    header = [t["col_date"], t["col_op"], t["col_amount"], t["col_balance"], t["col_note"]]
    data: list[list[str]] = [header]
    row_fills: list[tuple[int, Any]] = []

    if not events:
        data.append([t["no_events"], "", "", "", ""])
    else:
        for i, ev in enumerate(events, start=1):
            op_label, delta, sign = _classify(ev, lang)
            amt = abs(delta)
            bal = float(ev.get("balance_after") or 0)
            data.append([
                _fmt_date(ev.get("dt") or ev.get("created_at")),
                op_label,
                f"{sign}{amt:.2f}",
                f"{bal:.2f}",
                _clean_note(ev.get("note")),
            ])
            if delta > 0:
                row_fills.append((i, _PLUS_FILL))
            elif delta < 0:
                row_fills.append((i, _MINUS_FILL))

    table = Table(
        data,
        colWidths=[26 * mm, 42 * mm, 28 * mm, 34 * mm, 52 * mm],
        repeatRows=1,
    )
    ts = TableStyle([
        ("FONT",       (0, 0), (-1, -1), _FONT, 11),
        ("FONT",       (0, 0), (-1, 0),  _FONT_BOLD, 11),
        ("BACKGROUND", (0, 0), (-1, 0),  _HEADER_FILL),
        ("TEXTCOLOR",  (0, 0), (-1, 0),  _HEADER_TEXT),
        ("ALIGN",      (0, 0), (-1, 0),  "CENTER"),
        ("ALIGN",      (2, 1), (3, -1),  "RIGHT"),
        ("VALIGN",     (0, 0), (-1, -1), "MIDDLE"),
        ("GRID",       (0, 0), (-1, -1), 0.4, _BORDER),
        ("LEFTPADDING",(0, 0), (-1, -1), 4),
        ("RIGHTPADDING",(0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 5),
    ])
    for row_idx, fill in row_fills:
        ts.add("BACKGROUND", (0, row_idx), (-1, row_idx), fill)
    table.setStyle(ts)

    story.append(table)
    story.append(Spacer(1, 6 * mm))

    # Final balance line — large, so the client sees it at a glance.
    story.append(Paragraph(
        f"<b>{t['balance_now']}:</b> {final_balance:.2f} USD",
        ParagraphStyle("ch_total", fontName=_FONT_BOLD, fontSize=13, spaceAfter=6 * mm),
    ))

    story.append(Spacer(1, 10 * mm))
    # Overlay the admin's stored signature over the seller line, if any.
    # Scale by HEIGHT so a tall / square scan doesn't overlap other rows.
    from app.services.signature_asset import get_signature_path, signature_fit
    _sig_png = get_signature_path()
    _sig_size = signature_fit(160, 50)
    sig_style_para = ParagraphStyle("ch_sig", fontName=_FONT, fontSize=10, leading=14)
    if _sig_png is not None and _sig_size is not None:
        _sw, _sh = _sig_size
        seller_html = (
            f'{t["seller"]}: '
            f'<img src="{_sig_png}" width="{_sw}" height="{_sh}" valign="middle"/>'
        )
    else:
        seller_html = f"{t['seller']}: __________________"
    sig_tbl = Table(
        [[Paragraph(seller_html, sig_style_para),
          Paragraph(f"{t['signature']}: __________________", sig_style_para)]],
        colWidths=[90 * mm, 90 * mm],
    )
    sig_tbl.setStyle(TableStyle([
        ("VALIGN",       (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING",  (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    story.append(sig_tbl)

    story.append(Spacer(1, 4 * mm))
    story.append(Paragraph(
        f"{t['generated']}: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        small_style,
    ))

    # Stamp on page 1 only — on continuation pages it would overlap the table.
    doc.build(story, onFirstPage=_draw_stamp_pdf)
    return str(filename)


def _esc(s: str) -> str:
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


# ── XLSX ────────────────────────────────────────────────────────────────────

def generate_client_history_xlsx(
    client: dict[str, Any],
    events: list[dict[str, Any]],
    final_balance: float,
    date_from: str | None = None,
    date_to: str | None = None,
    lang: str = "ru",
) -> str:
    """Render a simplified client-history XLSX and return its absolute path."""

    t = _t(lang)
    filename = _out_dir() / f"{_filename_stem(client)}.xlsx"

    wb = Workbook()
    ws = wb.active
    ws.title = "History"

    bold = Font(name="DejaVu Sans", bold=True, size=11)
    header_font = Font(name="DejaVu Sans", bold=True, color="FFFFFF", size=11)
    body_font = Font(name="DejaVu Sans", size=11)
    big_font = Font(name="DejaVu Sans", bold=True, size=13)
    header_fill = PatternFill("solid", fgColor="4472C4")
    plus_fill = PatternFill("solid", fgColor="FDECEA")
    minus_fill = PatternFill("solid", fgColor="E8F5E9")
    border = Border(
        left=Side(style="thin", color="BFBFBF"),
        right=Side(style="thin", color="BFBFBF"),
        top=Side(style="thin", color="BFBFBF"),
        bottom=Side(style="thin", color="BFBFBF"),
    )
    center = Alignment(horizontal="center", vertical="center")
    right = Alignment(horizontal="right", vertical="center")
    left = Alignment(horizontal="left", vertical="center", wrap_text=True)

    r = 1
    ws.cell(row=r, column=1, value=t["title"]).font = big_font
    ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=5)
    r += 2

    ws.cell(row=r, column=1, value=f"{t['client']}:").font = bold
    ws.cell(row=r, column=2, value=str(client.get("name") or "")).font = body_font
    r += 1
    phone = str(client.get("phone") or "").strip()
    if phone:
        ws.cell(row=r, column=1, value=f"{t['phone']}:").font = bold
        ws.cell(row=r, column=2, value=phone).font = body_font
        r += 1
    ws.cell(row=r, column=1, value=f"{t['period']}:").font = bold
    ws.cell(row=r, column=2,
            value=_fmt_period(date_from, date_to, lang, shown_count=len(events))).font = body_font
    r += 2

    header_row = r
    for col, title in enumerate(
        [t["col_date"], t["col_op"], t["col_amount"], t["col_balance"], t["col_note"]],
        start=1,
    ):
        c = ws.cell(row=header_row, column=col, value=title)
        c.font = header_font
        c.fill = header_fill
        c.alignment = center
        c.border = border
    r += 1

    if not events:
        c = ws.cell(row=r, column=1, value=t["no_events"])
        c.font = body_font
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=5)
        r += 1
    else:
        for ev in events:
            op_label, delta, sign = _classify(ev, lang)
            amt = abs(delta)
            bal = float(ev.get("balance_after") or 0)
            row_fill = plus_fill if delta > 0 else (minus_fill if delta < 0 else None)
            cells = [
                _fmt_date(ev.get("dt") or ev.get("created_at")),
                op_label,
                f"{sign}{amt:.2f}",
                round(bal, 2),
                _clean_note(ev.get("note")),
            ]
            for col, val in enumerate(cells, start=1):
                c = ws.cell(row=r, column=col, value=val)
                c.font = body_font
                c.border = border
                if row_fill is not None:
                    c.fill = row_fill
                if col in (3, 4):
                    c.alignment = right
                    if col == 4:
                        c.number_format = '0.00'
                elif col == 5:
                    c.alignment = left
                else:
                    c.alignment = Alignment(vertical="center")
            r += 1

    r += 1
    ws.cell(row=r, column=1, value=f"{t['balance_now']}:").font = big_font
    tot_cell = ws.cell(row=r, column=2, value=round(final_balance, 2))
    tot_cell.font = big_font
    tot_cell.number_format = '0.00'
    tot_cell.alignment = right
    r += 2

    sig_text_row = r
    ws.cell(row=r, column=1, value=f"{t['seller']}: ______________________").font = body_font
    ws.cell(row=r, column=3, value=f"{t['signature']}: ______________________").font = body_font
    r += 2
    ws.cell(row=r, column=1,
            value=f"{t['generated']}: {datetime.now().strftime('%Y-%m-%d %H:%M')}").font = body_font

    # Columns: narrow date / wider op / amount / balance / note
    widths = [14, 22, 14, 18, 32]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[chr(ord("A") + i - 1)].width = w

    # Optional seller-signature overlay — pasted on the Seller row (col A).
    try:
        from app.services.signature_asset import get_signature_path, signature_fit
        _sig_png = get_signature_path()
        _sig_size = signature_fit(200, 55)
        if _sig_png is not None and _sig_size is not None:
            from openpyxl.drawing.image import Image as OpenpyxlImage
            _sw, _sh = _sig_size
            ws.row_dimensions[sig_text_row].height = 36
            sig_img = OpenpyxlImage(str(_sig_png))
            sig_img.width = _sw
            sig_img.height = _sh
            sig_img.anchor = f"A{sig_text_row}"
            ws.add_image(sig_img)
    except Exception:
        pass

    # Optional stamp in the top-right (column E, row 1) — mirrors sale XLSX.
    if _STAMP_PATH.exists():
        try:
            from openpyxl.drawing.image import Image as OpenpyxlImage
            with Image.open(_STAMP_PATH) as _probe:
                ratio = _probe.height / max(1, _probe.width)
            xlimg = OpenpyxlImage(str(_STAMP_PATH))
            xlimg.width = _STAMP_XLSX_W_PX
            xlimg.height = int(_STAMP_XLSX_W_PX * ratio)
            xlimg.anchor = "E1"
            ws.add_image(xlimg)
        except Exception:
            pass

    wb.save(str(filename))
    return str(filename)


# ── JPEG ────────────────────────────────────────────────────────────────────
#
# A single tall PNG → JPEG. Hand-drawn with Pillow because reportlab's PDF
# does not round-trip to a raster image without an extra system tool
# (poppler for pdf2image, Ghostscript for Wand). Pillow is already a
# transitive dep via openpyxl's image support and ships cross-platform.

_JPG_WIDTH = 900          # px, suitable for messenger previews
_JPG_LEFT_PAD = 30
_JPG_TOP_PAD = 30
_JPG_ROW_PAD_Y = 8
_JPG_ROW_HEIGHT = 44
_JPG_HEADER_HEIGHT = 50

_JPG_COL_W = [110, 220, 130, 150, 230]  # date / op / amount / balance / note
assert sum(_JPG_COL_W) == _JPG_WIDTH - 2 * _JPG_LEFT_PAD, "column widths must sum to usable area"


def _pil_font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    path = _FONT_BOLD_PATH if bold else _FONT_REGULAR_PATH
    if path:
        try:
            return ImageFont.truetype(path, size=size)
        except Exception:
            pass
    return ImageFont.load_default()


def _measure(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> tuple[int, int]:
    try:
        l, t, r, b = draw.textbbox((0, 0), text, font=font)
        return r - l, b - t
    except AttributeError:
        return draw.textsize(text, font=font)  # type: ignore[attr-defined]


def _truncate(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_w: int) -> str:
    if not text:
        return ""
    w, _ = _measure(draw, text, font)
    if w <= max_w:
        return text
    ell = "…"
    out = text
    while out and _measure(draw, out + ell, font)[0] > max_w:
        out = out[:-1]
    return (out + ell) if out else ell


def generate_client_history_jpeg(
    client: dict[str, Any],
    events: list[dict[str, Any]],
    final_balance: float,
    date_from: str | None = None,
    date_to: str | None = None,
    lang: str = "ru",
) -> str:
    """Render a single tall JPEG and return its absolute path."""

    t = _t(lang)
    filename = _out_dir() / f"{_filename_stem(client)}.jpg"

    f_title = _pil_font(22, bold=True)
    f_info = _pil_font(16)
    f_info_bold = _pil_font(16, bold=True)
    f_head = _pil_font(15, bold=True)
    f_body = _pil_font(15)
    f_body_bold = _pil_font(15, bold=True)
    f_small = _pil_font(12)

    # Measure height upfront so the final image is tight.
    tmp = Image.new("RGB", (_JPG_WIDTH, 10), "white")
    dm = ImageDraw.Draw(tmp)
    _, title_h = _measure(dm, t["title"], f_title)
    _, info_h = _measure(dm, "A", f_info)

    phone = str(client.get("phone") or "").strip()
    info_lines = 2 + (1 if phone else 0)  # client, [phone], period
    info_block = info_lines * (info_h + 6)

    rows_count = max(1, len(events))
    body_block = _JPG_HEADER_HEIGHT + rows_count * _JPG_ROW_HEIGHT

    # Signature block budget: 50px top gap + 56px for a tall signature image
    # (plus text height) so a square-ish scan doesn't push the generated-at
    # line off the canvas.
    total_h = (
        _JPG_TOP_PAD
        + title_h + 14
        + info_block
        + 12
        + body_block
        + 24
        + info_h + 8   # final balance line
        + 50           # gap before signature
        + 72           # signature image max height
        + 24
        + info_h       # generated line
        + _JPG_TOP_PAD
    )

    img = Image.new("RGB", (_JPG_WIDTH, int(total_h)), "white")
    d = ImageDraw.Draw(img)

    # Title
    y = _JPG_TOP_PAD
    tw, _ = _measure(d, t["title"], f_title)
    d.text(((_JPG_WIDTH - tw) // 2, y), t["title"], font=f_title, fill="#1a1a1a")
    y += title_h + 14

    # Info block (left-aligned)
    def _draw_info(label: str, value: str) -> int:
        nonlocal y
        d.text((_JPG_LEFT_PAD, y), f"{label}:", font=f_info_bold, fill="#333")
        lw, _ = _measure(d, f"{label}:", f_info_bold)
        d.text((_JPG_LEFT_PAD + lw + 10, y), value, font=f_info, fill="#333")
        y += info_h + 6
        return y

    _draw_info(t["client"], str(client.get("name") or ""))
    if phone:
        _draw_info(t["phone"], phone)
    _draw_info(t["period"], _fmt_period(date_from, date_to, lang, shown_count=len(events)))
    y += 12

    # Table header
    x_cols = [_JPG_LEFT_PAD]
    for w in _JPG_COL_W:
        x_cols.append(x_cols[-1] + w)
    inner_right = x_cols[-1]

    d.rectangle(
        [(_JPG_LEFT_PAD, y), (inner_right, y + _JPG_HEADER_HEIGHT)],
        fill="#4472C4", outline="#000",
    )
    headers = [t["col_date"], t["col_op"], t["col_amount"], t["col_balance"], t["col_note"]]
    for i, h in enumerate(headers):
        hw, hh = _measure(d, h, f_head)
        cx = x_cols[i] + (_JPG_COL_W[i] - hw) // 2
        cy = y + (_JPG_HEADER_HEIGHT - hh) // 2
        d.text((cx, cy), h, font=f_head, fill="white")
    y += _JPG_HEADER_HEIGHT

    # Rows
    if not events:
        d.rectangle(
            [(_JPG_LEFT_PAD, y), (inner_right, y + _JPG_ROW_HEIGHT)],
            fill="white", outline="#000",
        )
        msg = t["no_events"]
        mw, mh = _measure(d, msg, f_body)
        d.text(
            ((_JPG_WIDTH - mw) // 2, y + (_JPG_ROW_HEIGHT - mh) // 2),
            msg, font=f_body, fill="#666",
        )
        y += _JPG_ROW_HEIGHT
    else:
        for ev in events:
            op_label, delta, sign = _classify(ev, lang)
            amt = abs(delta)
            bal = float(ev.get("balance_after") or 0)
            fill = "#FDECEA" if delta > 0 else ("#E8F5E9" if delta < 0 else "white")
            d.rectangle(
                [(_JPG_LEFT_PAD, y), (inner_right, y + _JPG_ROW_HEIGHT)],
                fill=fill, outline="#000",
            )

            cells = [
                (_fmt_date(ev.get("dt") or ev.get("created_at")), f_body, "center", "#1a1a1a"),
                (op_label, f_body, "left", "#1a1a1a"),
                (f"{sign}{amt:.2f}", f_body_bold, "right",
                 "#B00020" if delta > 0 else ("#1B5E20" if delta < 0 else "#333")),
                (f"{bal:.2f}", f_body, "right", "#1a1a1a"),
                (_clean_note(ev.get("note")) or "—", f_small, "left", "#555"),
            ]
            for i, (val, font, align, color) in enumerate(cells):
                col_left = x_cols[i] + 8
                col_right = x_cols[i + 1] - 8
                col_w = col_right - col_left
                text = _truncate(d, str(val), font, col_w)
                tw, th = _measure(d, text, font)
                if align == "center":
                    cx = (col_left + col_right - tw) // 2
                elif align == "right":
                    cx = col_right - tw
                else:
                    cx = col_left
                cy = y + (_JPG_ROW_HEIGHT - th) // 2
                d.text((cx, cy), text, font=font, fill=color)
            y += _JPG_ROW_HEIGHT

    y += 24
    tot = f"{t['balance_now']}: {final_balance:.2f} USD"
    d.text((_JPG_LEFT_PAD, y), tot, font=f_info_bold, fill="#1a1a1a")
    y += info_h + 8

    # Signature line — paste the admin's signature over the seller underscores
    # when uploaded; the client half stays blank for a manual signature.
    # Extra top gap gives a square scan room without clipping balance line.
    y += 50
    seller_label = f"{t['seller']}: "
    client_label = f"    {t['signature']}: ______________"
    sl_w, sl_h = _measure(d, seller_label, f_info)
    sig_underline = "______________"
    su_w, _ = _measure(d, sig_underline, f_info)

    d.text((_JPG_LEFT_PAD, y), seller_label, font=f_info, fill="#333")
    from app.services.signature_asset import get_signature_path
    _sig_png = get_signature_path()
    if _sig_png is not None:
        try:
            with Image.open(_sig_png) as _sig_src:
                _sig = _sig_src.convert("RGBA")
            # Fit into a 240×72 box, preserving aspect — so wide scans use
            # width freely, square scans stay inside the height budget.
            max_w, max_h = 240, 72
            scale = min(max_w / _sig.width, max_h / _sig.height)
            sig_w = max(1, int(_sig.width * scale))
            sig_h = max(1, int(_sig.height * scale))
            _sig = _sig.resize((sig_w, sig_h), Image.LANCZOS)
            # Sit visually on the baseline where underscores would be.
            px = _JPG_LEFT_PAD + sl_w
            py = y + sl_h - sig_h + 6
            img.paste(_sig, (px, max(0, py)), _sig)
            tail_x = _JPG_LEFT_PAD + sl_w + sig_w + 12
        except Exception:
            d.text((_JPG_LEFT_PAD + sl_w, y), sig_underline, font=f_info, fill="#333")
            tail_x = _JPG_LEFT_PAD + sl_w + su_w
    else:
        d.text((_JPG_LEFT_PAD + sl_w, y), sig_underline, font=f_info, fill="#333")
        tail_x = _JPG_LEFT_PAD + sl_w + su_w
    d.text((tail_x, y), client_label, font=f_info, fill="#333")
    y += info_h + 24

    gen = f"{t['generated']}: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    gw, _ = _measure(d, gen, f_small)
    d.text((_JPG_WIDTH - _JPG_LEFT_PAD - gw, y), gen, font=f_small, fill="#888")

    # Optional stamp pasted in the top-right corner, scaled to a width
    # that fits beside the centered title without overlapping it.
    if _STAMP_PATH.exists():
        try:
            with Image.open(_STAMP_PATH) as stamp_src:
                stamp = stamp_src.convert("RGBA")
            sw = _STAMP_JPG_W_PX
            sh = max(1, int(stamp.height * (sw / stamp.width)))
            stamp = stamp.resize((sw, sh), Image.LANCZOS)
            px = _JPG_WIDTH - _JPG_LEFT_PAD - sw
            py = _JPG_TOP_PAD
            img.paste(stamp, (px, py), stamp)
        except Exception:
            pass

    img.save(str(filename), format="JPEG", quality=90, optimize=True)
    return str(filename)

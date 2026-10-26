"""
Sale invoice JPEG renderer.

Produces a tall single image of a sale invoice so the admin can share it
through IMO / WhatsApp / Telegram without the recipient needing a PDF
reader. Mirrors the layout of the printable PDF but at screen scale.

Honours `lang` for row labels, draws the uploaded stamp (top-right) and
signature (seller line) when available, and renders the full retail
50/50 profit block + "actually given" details for `is_retail=1` invoices.
"""

from __future__ import annotations

import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from PIL import Image, ImageDraw, ImageFont


# ── Output / assets (reuse the same convention as other invoice renderers) ─

def _out_dir() -> Path:
    env = os.getenv("INVOICES_DIR", "").strip()
    base = Path(env) if env else Path("/opt/stock_bot/invoices")
    try:
        base.mkdir(parents=True, exist_ok=True)
        return base
    except OSError:
        p = Path(tempfile.gettempdir()) / "stock_bot_invoices"
        p.mkdir(parents=True, exist_ok=True)
        return p


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


def _first_existing(paths) -> Optional[str]:
    for p in paths:
        if Path(p).exists():
            return p
    return None


_FONT_REGULAR_PATH = _first_existing(_DEJAVU_CANDIDATES)
_FONT_BOLD_PATH = _first_existing(_DEJAVU_BOLD)


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


def _truncate(draw: ImageDraw.ImageDraw, text: str,
              font: ImageFont.ImageFont, max_w: int) -> str:
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


# ── i18n ────────────────────────────────────────────────────────────────

_LABELS = {
    "ru": {
        "invoice": "НАКЛАДНАЯ", "client": "Клиент", "date": "Дата",
        "col_num": "№", "col_model": "Модель", "col_name": "Название",
        "col_qty": "Кол-во", "col_price": "Цена", "col_total": "Сумма",
        "total": "ИТОГО",
        "r_report": "отчёт о прибыли",
        "r_cost": "Приход (себестоимость)", "r_sold": "Продано",
        "r_shop": "Прибыль магазина", "r_pshop": "Партнёру с магазина",
        "r_pext": "Партнёру с опт-клиентов", "r_ptotal": "Доля партнёра",
        "r_sfx": "всего", "r_adj": "Корректировка",
        "r_payout": "К выдаче партнёру",
        "a_plan": "По расчёту", "a_give": "Отдано фактически",
        "a_over": "Переплачено — разница", "a_under": "Недодано — разница",
        "a_hint": "Эта разница автоматически переносится в корректировку следующей недели.",
        "seller": "Продавец", "buyer": "Покупатель",
        "generated": "Создано",
    },
    "en": {
        "invoice": "INVOICE", "client": "Client", "date": "Date",
        "col_num": "#", "col_model": "Model", "col_name": "Name",
        "col_qty": "Qty", "col_price": "Price", "col_total": "Total",
        "total": "TOTAL",
        "r_report": "profit report",
        "r_cost": "Cost (purchase)", "r_sold": "Sold",
        "r_shop": "Shop profit", "r_pshop": "Partner from shop",
        "r_pext": "Partner from opt clients", "r_ptotal": "Partner share",
        "r_sfx": "total", "r_adj": "Adjustment",
        "r_payout": "To pay partner",
        "a_plan": "By calculation", "a_give": "Actually given",
        "a_over": "Overpaid — difference", "a_under": "Underpaid — difference",
        "a_hint": "This difference auto-carries into next week's partner adjustment.",
        "seller": "Seller", "buyer": "Buyer",
        "generated": "Generated",
    },
    "tm": {
        "invoice": "HASAP-FAKTURA", "client": "Müşderi", "date": "Sene",
        "col_num": "№", "col_model": "Model", "col_name": "Ady",
        "col_qty": "Sany", "col_price": "Baha", "col_total": "Jemi",
        "total": "JEMI",
        "r_report": "peýda hasabaty",
        "r_cost": "Satyn alyş bahasy", "r_sold": "Satyldy",
        "r_shop": "Dükanyň peýdasy", "r_pshop": "Dükandan hyzmatdaşa",
        "r_pext": "Opt müşderilerden hyzmatdaşa", "r_ptotal": "Hyzmatdaşyň paýy",
        "r_sfx": "jemi", "r_adj": "Düzediş",
        "r_payout": "Hyzmatdaşa töleg",
        "a_plan": "Hasap boýunça", "a_give": "Hakyky berlen",
        "a_over": "Artyk — tapawut", "a_under": "Kem — tapawut",
        "a_hint": "Bu tapawut indiki hepdäniň düzedişine awtomatik geçýär.",
        "seller": "Satyjy", "buyer": "Alyjy",
        "generated": "Döredildi",
    },
}


def _t(lang: str) -> dict[str, str]:
    return _LABELS.get((lang or "").lower(), _LABELS["ru"])


# ── Layout constants ────────────────────────────────────────────────────

_W           = 1000
_LEFT_PAD    = 30
_TOP_PAD     = 30
_HEADER_H    = 50
_ROW_H       = 36
_RETAIL_H    = 28
_SIG_H       = 90
# Column widths (sum must == _W - 2*_LEFT_PAD = 940)
_COLS        = [50, 180, 300, 80, 160, 170]  # #, model, name, qty, price, total


def _render_invoice_jpeg(
    invoice: dict[str, Any],
    items: list[dict[str, Any]],
    *,
    lang: str = "ru",
) -> bytes:
    t = _t(lang)

    # ── Precompute totals ───────────────────────────────────────────────
    cart_total = round(sum(float(it.get("qty") or 0) * float(it.get("unit_price") or 0)
                           for it in items), 2)
    discount_pct = float(invoice.get("discount_pct") or 0)
    discount_amount = round(cart_total * discount_pct / 100.0, 2) if discount_pct > 0 else 0.0
    grand_total = round(cart_total - discount_amount, 2)

    # Retail info if applicable
    is_retail = bool(invoice.get("is_retail"))
    partner_name = str(invoice.get("retail_partner_name") or "").strip() or "Партнёр"
    partner_pct = float(invoice.get("retail_partner_pct") or 50)
    shop_name = str(invoice.get("retail_shop_name") or "").strip() or "Магазин"
    external_full = float(invoice.get("retail_external_profit") or 0)
    partner_adj = float(invoice.get("retail_partner_adjustment") or 0)
    partner_adj_note = str(invoice.get("retail_partner_adjustment_note") or "").strip()
    actual_payout = invoice.get("retail_actual_payout")
    actual_val: Optional[float] = None
    if actual_payout is not None:
        try:
            actual_val = float(actual_payout)
        except (ValueError, TypeError):
            actual_val = None
    actual_note = str(invoice.get("retail_actual_payout_note") or "").strip()

    cost_sum = round(sum(
        float(it.get("cost_price") or 0) * float(it.get("qty") or 0) for it in items
    ), 2)
    shop_profit  = round(grand_total - cost_sum, 2)
    shop_partner = round(shop_profit * partner_pct / 100.0, 2)
    ext_partner  = round(external_full * partner_pct / 100.0, 2)
    partner_total = round(shop_partner + ext_partner, 2)
    partner_payout = round(partner_total + partner_adj, 2)
    planned_payout = partner_payout if partner_adj != 0 else partner_total
    actual_diff = round((actual_val - planned_payout), 2) if actual_val is not None else None

    # ── Height estimation ───────────────────────────────────────────────
    f_title = _pil_font(26, bold=True)
    f_info = _pil_font(15)
    f_head = _pil_font(14, bold=True)
    f_body = _pil_font(14)
    f_small = _pil_font(12)
    f_bold = _pil_font(14, bold=True)

    tmp = Image.new("RGB", (_W, 10), "white")
    dm = ImageDraw.Draw(tmp)
    _, title_h = _measure(dm, t["invoice"], f_title)
    _, info_h = _measure(dm, "A", f_info)

    # Lines: title + 2 info lines + header + per-item rows + totals + signature
    retail_rows = 0
    if is_retail:
        retail_rows = 1  # heading
        retail_rows += 2  # cost + sold
        retail_rows += 1  # shop profit
        retail_rows += 1  # partner from shop
        if ext_partner != 0:
            retail_rows += 1
        retail_rows += 1  # partner total
        if partner_adj != 0:
            retail_rows += 2  # adj + payout
        if actual_val is not None:
            retail_rows += 3  # plan + given + diff

    discount_rows = 0
    if discount_pct > 0:
        discount_rows = 3  # subtotal + discount + grand

    total_h = (
        _TOP_PAD
        + title_h + 10
        + info_h * 2 + 12
        + _HEADER_H
        + _ROW_H * len(items)
        + _ROW_H                     # ИТОГО
        + _ROW_H * discount_rows     # Subtotal/Discount/Grand (if discount)
        + 20
        + (_RETAIL_H * retail_rows + 20 if retail_rows > 0 else 0)
        + _SIG_H
        + _TOP_PAD
    )
    img = Image.new("RGB", (_W, int(total_h)), "white")
    d = ImageDraw.Draw(img)

    # ── Stamp first (top-right) so we can push the table below its bottom.
    stamp_bottom = _TOP_PAD
    try:
        from app.services.stamp_asset import get_stamp_path
        stamp_png = get_stamp_path()
        if stamp_png is not None:
            with Image.open(stamp_png) as stamp_src:
                stamp = stamp_src.convert("RGBA")
            # Fit into 220×140 box, preserving aspect.
            max_w, max_h = 220, 140
            scale = min(max_w / stamp.width, max_h / stamp.height)
            sw = max(1, int(stamp.width * scale))
            sh = max(1, int(stamp.height * scale))
            stamp = stamp.resize((sw, sh), Image.LANCZOS)
            px = _W - _LEFT_PAD - sw
            py = _TOP_PAD
            img.paste(stamp, (px, py), stamp)
            stamp_bottom = py + sh
    except Exception:
        pass

    # ── Title ────────────────────────────────────────────────────────────
    y = _TOP_PAD
    title = f"{t['invoice']} #{int(invoice['number']):06d}"
    tw, _ = _measure(d, title, f_title)
    d.text(((_W - tw) // 2, y), title, font=f_title, fill="#1a1a1a")
    y += title_h + 10

    # ── Client + Date ────────────────────────────────────────────────────
    client = str(invoice.get("client") or "")
    date_s = str(invoice.get("created_at", invoice.get("date", "")))[:16].replace("T", " ")
    d.text((_LEFT_PAD, y), f"{t['client']}: {client}", font=f_info, fill="#333")
    y += info_h + 4
    d.text((_LEFT_PAD, y), f"{t['date']}: {date_s}", font=f_info, fill="#333")
    y += info_h + 12

    # Push table below the stamp if it reaches lower than current text y.
    y = max(y, stamp_bottom + 10)

    # ── Items table header ───────────────────────────────────────────────
    x_cols = [_LEFT_PAD]
    for w in _COLS:
        x_cols.append(x_cols[-1] + w)
    inner_right = x_cols[-1]

    d.rectangle([(_LEFT_PAD, y), (inner_right, y + _HEADER_H)],
                fill="#4472C4", outline="#000")
    headers = [t["col_num"], t["col_model"], t["col_name"],
               t["col_qty"], t["col_price"], t["col_total"]]
    for i, h in enumerate(headers):
        hw, hh = _measure(d, h, f_head)
        cx = x_cols[i] + (_COLS[i] - hw) // 2
        cy = y + (_HEADER_H - hh) // 2
        d.text((cx, cy), h, font=f_head, fill="white")
    y += _HEADER_H

    # ── Item rows ────────────────────────────────────────────────────────
    for idx, it in enumerate(items, start=1):
        fill = "#f8f9fa" if idx % 2 == 0 else "white"
        d.rectangle([(_LEFT_PAD, y), (inner_right, y + _ROW_H)],
                    fill=fill, outline="#ccc")
        qty = float(it.get("qty") or 0)
        unit_price = float(it.get("unit_price") or 0)
        line_total = qty * unit_price
        if it.get("free_line"):
            brand_part = (it.get("free_brand") or "").strip()
            model_part = (it.get("free_model") or "").strip()
            model_val = (brand_part + " " + model_part).strip() or "—"
            name_val = str(it.get("free_name") or "")
        else:
            model_val = f"{it.get('brand','')} {it.get('model','')}".strip()
            name_val = str(it.get("name", "") or "")
        cells = [
            (str(idx), "center"),
            (model_val, "left"),
            (name_val, "left"),
            (f"{qty:g}", "center"),
            (f"{unit_price:.2f}", "right"),
            (f"{line_total:.2f}", "right"),
        ]
        for i, (val, align) in enumerate(cells):
            col_left = x_cols[i] + 6
            col_right = x_cols[i + 1] - 6
            col_w = col_right - col_left
            text = _truncate(d, str(val), f_body, col_w)
            tw, th = _measure(d, text, f_body)
            if align == "center":
                cx = (col_left + col_right - tw) // 2
            elif align == "right":
                cx = col_right - tw
            else:
                cx = col_left
            d.text((cx, y + (_ROW_H - th) // 2), text, font=f_body, fill="#1a1a1a")
        y += _ROW_H

    # ── Discount/Total rows ──────────────────────────────────────────────
    def _total_row(label: str, value: str, bg="#D9EEF7", bold=True):
        nonlocal y
        d.rectangle([(_LEFT_PAD, y), (inner_right, y + _ROW_H)],
                    fill=bg, outline="#000")
        font = f_bold if bold else f_body
        # label at right half, value in last column
        lbl_x = x_cols[-2] - 10
        tw, th = _measure(d, label, font)
        d.text((lbl_x - tw, y + (_ROW_H - th) // 2), label, font=font, fill="#1a1a1a")
        vw, vh = _measure(d, value, font)
        d.text((x_cols[-1] - 10 - vw, y + (_ROW_H - vh) // 2),
               value, font=font, fill="#1a1a1a")
        y += _ROW_H

    if discount_pct > 0:
        _total_row(f"{t.get('subtotal','Subtotal') if False else 'Subtotal'}", f"{cart_total:.2f}", bold=False)
        _total_row(f"Discount {discount_pct:g}%", f"-{discount_amount:.2f}", bold=False)
        _total_row(t["total"], f"{grand_total:.2f}")
    else:
        _total_row(t["total"], f"{grand_total:.2f}")

    # ── Retail block ─────────────────────────────────────────────────────
    if is_retail:
        y += 20
        d.text((_LEFT_PAD, y),
               f"{shop_name} — {t['r_report']}:",
               font=f_bold, fill="#1a1a1a")
        y += _RETAIL_H

        def _r_pair(label: str, value: str, bold: bool = False,
                    color: str = "#1a1a1a", indent: int = 0,
                    divider_above: bool = False):
            nonlocal y
            if divider_above:
                d.line([(_LEFT_PAD, y), (inner_right, y)], fill="#888", width=1)
                y += 2
            font = f_bold if bold else f_body
            d.text((_LEFT_PAD + indent, y), label, font=font, fill=color)
            vw, vh = _measure(d, value, font)
            d.text((inner_right - vw, y), value, font=font, fill=color)
            y += _RETAIL_H

        _r_pair(f"{t['r_cost']}:",       f"{cost_sum:.2f}")
        _r_pair(f"{t['r_sold']}:",       f"{grand_total:.2f}")
        _r_pair(f"{t['r_shop']}:",       f"{shop_profit:.2f}", bold=True)
        _r_pair(f"{t['r_pshop']} ({int(partner_pct)}%):",
                f"{shop_partner:.2f}", indent=28, color="#b36b00")
        if ext_partner != 0:
            _r_pair(f"{t['r_pext']}:",
                    f"+{ext_partner:.2f}", indent=28, color="#b36b00")
        _r_pair(f"{t['r_ptotal']} ({partner_name}) {t['r_sfx']}:",
                f"{partner_total:.2f}", bold=True, divider_above=True)
        if partner_adj != 0:
            adj_label = t['r_adj']
            if partner_adj_note:
                adj_label += f" ({partner_adj_note})"
            color = "#d04444" if partner_adj < 0 else "#2e8b57"
            _r_pair(f"{adj_label}:", f"{partner_adj:+.2f}", color=color)
            _r_pair(f"{t['r_payout']} ({partner_name}):",
                    f"{partner_payout:.2f}", bold=True, divider_above=True)
        if actual_val is not None:
            _r_pair(f"{t['a_plan']}:", f"{planned_payout:.2f}", divider_above=True)
            give_label = t['a_give']
            if actual_note:
                give_label += f" ({actual_note})"
            _r_pair(f"{give_label}:", f"{actual_val:.2f}", bold=True)
            if actual_diff is not None:
                if actual_diff > 0:
                    diff_label, color = t['a_over'], "#2e8b57"
                elif actual_diff < 0:
                    diff_label, color = t['a_under'], "#d04444"
                else:
                    diff_label, color = t['a_give'], "#555"
                _r_pair(f"{diff_label}:", f"{actual_diff:+.2f}",
                        bold=True, color=color)
            # Hint line
            d.text((_LEFT_PAD, y),
                   f"ℹ {t['a_hint']}", font=f_small, fill="#1e88a8")
            y += info_h + 4

    # ── Signature line ───────────────────────────────────────────────────
    y += 30
    sig_text = f"{t['seller']}: ______________________    {t['buyer']}: ______________________"
    d.text((_LEFT_PAD, y), sig_text, font=f_info, fill="#333")

    # Overlay signature over the "Seller" underscores if available.
    try:
        from app.services.signature_asset import get_signature_path
        sig_png = get_signature_path()
        if sig_png is not None:
            with Image.open(sig_png) as _sig_src:
                _sig = _sig_src.convert("RGBA")
            max_w, max_h = 240, 72
            scale = min(max_w / _sig.width, max_h / _sig.height)
            sw = max(1, int(_sig.width * scale))
            sh = max(1, int(_sig.height * scale))
            _sig = _sig.resize((sw, sh), Image.LANCZOS)
            # Shift down so the signature sits ON the Seller underscore.
            px = _LEFT_PAD + 90
            py = y + 2 - sh // 2
            img.paste(_sig, (px, max(0, py)), _sig)
    except Exception:
        pass

    # Stamp was already pasted at the top (before the title) so the table
    # starts below its bottom. See the start of this function.

    import io
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90, optimize=True)
    return buf.getvalue()


def generate_invoice_jpeg(invoice: dict[str, Any],
                           items: list[dict[str, Any]],
                           lang: str = "ru") -> str:
    """Write a JPEG to disk and return its absolute path."""
    data = _render_invoice_jpeg(invoice, items, lang=lang)
    number = int(invoice["number"])
    path = _out_dir() / f"invoice_{number:06d}.jpg"
    path.write_bytes(data)
    return str(path)


def generate_invoice_jpeg_bytes(invoice: dict[str, Any],
                                 items: list[dict[str, Any]],
                                 lang: str = "ru") -> bytes:
    """Return JPEG content as bytes (for streaming responses)."""
    return _render_invoice_jpeg(invoice, items, lang=lang)

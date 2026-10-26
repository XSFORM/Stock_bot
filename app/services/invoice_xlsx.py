from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import openpyxl
from openpyxl.drawing.image import Image as XLImage
from openpyxl.styles import Alignment, Font, PatternFill, Side, Border
from app.utils.money import calc_document_total, calc_line_total, round_money


OUT_DIR = Path("/opt/stock_bot/invoices")
STAMP_PATH = Path("/opt/stock_bot/stamp.png")  # Optional PNG stamp/logo for sales invoices
STAMP_WIDTH = 220
STAMP_HEIGHT = 80

_HEADER_FILL = PatternFill("solid", fgColor="4472C4")
_HEADER_FONT = Font(bold=True, color="FFFFFF")
_TOTAL_FILL = PatternFill("solid", fgColor="D9EEF7")  # Accent 1, ~60% (light blue)
_TOTAL_FONT = Font(bold=True)
_TOTAL_FONT_ITALIC = Font(bold=True, italic=True)

_CENTER = Alignment(horizontal="center")
_RIGHT = Alignment(horizontal="right")

_THIN = Side(style="thin")
_BORDER = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)


def _make_workbook(invoice: dict[str, Any], items: list[dict[str, Any]]) -> openpyxl.Workbook:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = f"Invoice {invoice['number']:06d}"

    # --- Title block ---
    ws.merge_cells("A1:D1")
    title_cell = ws["A1"]
    title_cell.value = f"INVOICE #{invoice['number']:06d}"
    title_cell.font = Font(bold=True, size=14)
    title_cell.alignment = _CENTER

    ws.merge_cells("A2:D2")
    ws["A2"].value = f"Client: {invoice['client']}"
    ws.merge_cells("A3:D3")
    date_val = str(invoice.get("created_at", invoice.get("date", "")))[:16].replace("T", " ")
    ws["A3"].value = f"Date: {date_val}"
    ws["A4"].value = ""

    # Place optional stamp/logo PNG at /opt/stock_bot/stamp.png (if present).
    if STAMP_PATH.exists():
        stamp = XLImage(str(STAMP_PATH))
        stamp.width = STAMP_WIDTH
        stamp.height = STAMP_HEIGHT
        ws.add_image(stamp, "E1")

    # --- Header row ---
    headers = ["#", "Model", "Name", "Barcode", "Qty", "Unit Price", "Total"]
    col_widths = [5, 18, 30, 18, 8, 14, 14]
    header_row = 5
    for col_idx, (h, w) in enumerate(zip(headers, col_widths), start=1):
        cell = ws.cell(row=header_row, column=col_idx, value=h)
        cell.font = _HEADER_FONT
        cell.fill = _HEADER_FILL
        cell.alignment = _CENTER
        cell.border = _BORDER
        ws.column_dimensions[cell.column_letter].width = w

    # --- Data rows ---
    for row_num, item in enumerate(items, start=1):
        row_idx = header_row + row_num
        if item.get("free_line"):
            # Phase 10: free line now has brand/model/name/barcode in their
            # own fields → map each one to its matching invoice column.
            brand_part = (item.get("free_brand") or "").strip()
            model_part = (item.get("free_model") or "").strip()
            # Compose "Brand Model" for the Model column, consistent with
            # how stock items are shown.
            model_val = (brand_part + " " + model_part).strip()
            name_val    = (item.get("free_name")    or "").strip()
            barcode_val = (item.get("free_barcode") or "").strip()
        else:
            model_val   = f"{item['brand']} {item['model']}"
            name_val    = item["name"]
            barcode_val = item.get("barcode") or ""
        values = [
            row_num,
            model_val,
            name_val,
            barcode_val,
            float(item["qty"]),
            round_money(item["unit_price"]),
            calc_line_total(item["unit_price"], item["qty"]),
        ]
        for col_idx, val in enumerate(values, start=1):
            cell = ws.cell(row=row_idx, column=col_idx, value=val)
            cell.border = _BORDER
            if col_idx in (1, 5):
                cell.alignment = _CENTER
            elif col_idx in (6, 7):
                cell.alignment = _RIGHT

    # --- Total block ---
    # Phase 11: when invoice has a discount_pct, show three rows:
    #   Subtotal / Discount X% / Grand Total.
    # Otherwise keep the single TOTAL row to match legacy invoices.
    items_qty = int(sum(float(item["qty"]) for item in items))
    subtotal_val = calc_document_total(items, "unit_price")
    discount_pct = float(invoice.get("discount_pct") or 0)
    discount_amount = round(subtotal_val * discount_pct / 100.0, 2) if discount_pct > 0 else 0.0
    grand_total = round(subtotal_val - discount_amount, 2)

    def _total_row(row_idx: int, label: str, amount: float,
                   put_qty: bool = False, bold: bool = True,
                   italic: bool = False) -> None:
        """Render one of the Subtotal/Discount/Total rows across cols A..G."""
        font = _TOTAL_FONT_ITALIC if italic else _TOTAL_FONT if bold else None
        lbl = ws.cell(row=row_idx, column=2, value=label)
        if font: lbl.font = font
        lbl.alignment = Alignment(horizontal="left")
        if put_qty:
            qty_cell = ws.cell(row=row_idx, column=5, value=items_qty)
            if font: qty_cell.font = font
            qty_cell.alignment = _CENTER
        amt_cell = ws.cell(row=row_idx, column=7, value=amount)
        if font: amt_cell.font = font
        amt_cell.alignment = _RIGHT
        for col_idx in range(1, 8):
            cell = ws.cell(row=row_idx, column=col_idx)
            cell.border = _BORDER
            cell.fill = _TOTAL_FILL
            if col_idx in (1, 5):
                cell.alignment = _CENTER
            elif col_idx in (6, 7):
                cell.alignment = _RIGHT
            else:
                cell.alignment = Alignment(horizontal="left")
        ws.cell(row=row_idx, column=6, value="").fill = _TOTAL_FILL
        ws.cell(row=row_idx, column=6).border = _BORDER

    base_row = header_row + len(items) + 1
    if discount_pct > 0:
        _total_row(base_row,     f"Subtotal",                     subtotal_val,
                   put_qty=True, italic=True)
        _total_row(base_row + 1, f"Discount {discount_pct:g}%",   -discount_amount)
        _total_row(base_row + 2, f"TOTAL",                        grand_total)
        total_row_idx = base_row + 2
    else:
        _total_row(base_row, "TOTAL", grand_total, put_qty=True, italic=True)
        total_row_idx = base_row

    # --- Retail 50/50 profit block (only for shop invoices) ---
    extra_row = total_row_idx
    if invoice.get("is_retail"):
        partner_pct = float(invoice.get("retail_partner_pct") or 50)
        partner_name = str(invoice.get("retail_partner_name") or "").strip() or "Партнёр"
        shop_name = str(invoice.get("retail_shop_name") or "").strip() or "Магазин"
        external_full = float(invoice.get("retail_external_profit") or 0)
        partner_adj = float(invoice.get("retail_partner_adjustment") or 0)
        partner_adj_note = str(invoice.get("retail_partner_adjustment_note") or "").strip()
        total_cost = 0.0
        for it in items:
            cp = it.get("cost_price")
            if cp is None:
                continue
            total_cost += float(cp) * float(it.get("qty") or 0)
        shop_profit = round(grand_total - total_cost, 2)
        shop_partner = round(shop_profit * partner_pct / 100.0, 2)
        shop_me = round(shop_profit - shop_partner, 2)
        ext_partner = round(external_full * partner_pct / 100.0, 2)
        ext_me = round(external_full - ext_partner, 2)
        partner_total = round(shop_partner + ext_partner, 2)
        me_total = round(shop_me + ext_me, 2)
        partner_payout = round(partner_total + partner_adj, 2)

        heading_row = total_row_idx + 2
        ws.cell(row=heading_row, column=1,
                value=f"{shop_name} — отчёт о прибыли:").font = Font(bold=True)
        ws.merge_cells(start_row=heading_row, start_column=1,
                       end_row=heading_row, end_column=7)

        def _retail_pair(r: int, label: str, value: float, bold: bool = False) -> None:
            c_lbl = ws.cell(row=r, column=1, value=label)
            if bold:
                c_lbl.font = Font(bold=True)
            c_val = ws.cell(row=r, column=7, value=value)
            c_val.alignment = _RIGHT
            c_val.number_format = "0.00"
            if bold:
                c_val.font = Font(bold=True)

        r = heading_row + 1
        _retail_pair(r, "Приход (себестоимость):",             round(total_cost, 2)); r += 1
        _retail_pair(r, "Продано:",                            grand_total);           r += 1
        _retail_pair(r, "Прибыль магазина:",                   shop_profit, bold=True); r += 1
        _retail_pair(r, f"    Партнёру с магазина ({int(partner_pct)}%):", shop_partner); r += 1
        if ext_partner != 0:
            _retail_pair(r, "    Партнёру с опт-клиентов:",   ext_partner);          r += 1
        _retail_pair(r, f"Доля партнёра ({partner_name}) всего:", partner_total, bold=True); r += 1
        _retail_pair(r, "Моя доля всего:",                     me_total)
        if partner_adj != 0:
            r += 1
            adj_label = "Корректировка"
            if partner_adj_note:
                adj_label += f" ({partner_adj_note})"
            adj_label += ":"
            _retail_pair(r, adj_label,                          partner_adj);          r += 1
            _retail_pair(r, f"К выдаче партнёру ({partner_name}):", partner_payout, bold=True)
        extra_row = r

    # --- Signature block (two side-by-side signatures) ---
    # Leave 2 blank rows, then put Seller on the left half (cols A–C)
    # and Buyer on the right half (cols E–G). Merge each label into a
    # single visual cell so it prints nicely on one line. If the admin
    # has uploaded a signature PNG we overlay it on the seller row.
    sig_row = extra_row + 3
    ws.cell(row=sig_row, column=1, value="Seller: ______________________________").alignment = Alignment(horizontal="left")
    ws.merge_cells(start_row=sig_row, start_column=1, end_row=sig_row, end_column=3)
    ws.cell(row=sig_row, column=5, value="Buyer: ______________________________").alignment = Alignment(horizontal="left")
    ws.merge_cells(start_row=sig_row, start_column=5, end_row=sig_row, end_column=7)

    try:
        from app.services.signature_asset import get_signature_path, signature_fit
        _sig_png = get_signature_path()
        _sig_size = signature_fit(200, 55)  # px box; preserves aspect
        if _sig_png is not None and _sig_size is not None:
            from openpyxl.drawing.image import Image as OpenpyxlImage
            _sw, _sh = _sig_size
            # Make the seller row tall enough so the pasted signature
            # doesn't bleed into neighbouring rows when printed.
            ws.row_dimensions[sig_row].height = 36
            sig_img = OpenpyxlImage(str(_sig_png))
            sig_img.width = _sw
            sig_img.height = _sh
            sig_img.anchor = f"A{sig_row}"
            ws.add_image(sig_img)
    except Exception:
        pass

    # --- Print setup: A4, fit to 1 page wide, multi-page tall ---
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.page_setup.orientation = ws.ORIENTATION_PORTRAIT
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.page_setup.fitToPage = True
    ws.page_setup.scale = None
    ws.sheet_properties.pageSetUpPr.fitToPage = True

    # Excel "Narrow"-like margins
    ws.page_margins.left = 0.25
    ws.page_margins.right = 0.25
    ws.page_margins.top = 0.75
    ws.page_margins.bottom = 0.75
    ws.page_margins.header = 0.3
    ws.page_margins.footer = 0.3

    ws.print_area = f"A1:G{sig_row}"
    ws.print_title_rows = f"{header_row}:{header_row}"

    return wb


def generate_invoice_xlsx(invoice: dict[str, Any], items: list[dict[str, Any]]) -> str:
    """Write .xlsx to disk and return the file path."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    number = invoice["number"]
    filepath = OUT_DIR / f"invoice_{number:06d}.xlsx"
    wb = _make_workbook(invoice, items)
    wb.save(str(filepath))
    return str(filepath)


def generate_invoice_xlsx_bytes(invoice: dict[str, Any], items: list[dict[str, Any]]) -> bytes:
    """Return .xlsx content as bytes (for streaming response)."""
    wb = _make_workbook(invoice, items)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()

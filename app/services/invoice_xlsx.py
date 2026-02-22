from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill, Side, Border


OUT_DIR = Path("/opt/stock_bot/invoices")

_HEADER_FILL = PatternFill("solid", fgColor="4472C4")
_HEADER_FONT = Font(bold=True, color="FFFFFF")
_TOTAL_FONT = Font(bold=True)
_CENTER = Alignment(horizontal="center")
_RIGHT = Alignment(horizontal="right")

_THIN = Side(style="thin")
_BORDER = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)


def _make_workbook(invoice: dict[str, Any], items: list[dict[str, Any]]) -> openpyxl.Workbook:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = f"Invoice {invoice['number']:06d}"

    # --- Title block ---
    ws.merge_cells("A1:F1")
    title_cell = ws["A1"]
    title_cell.value = f"INVOICE #{invoice['number']:06d}"
    title_cell.font = Font(bold=True, size=14)
    title_cell.alignment = _CENTER

    ws.merge_cells("A2:F2")
    ws["A2"].value = f"Client: {invoice['client']}"
    ws.merge_cells("A3:F3")
    date_val = str(invoice.get("created_at", invoice.get("date", "")))[:16].replace("T", " ")
    ws["A3"].value = f"Date: {date_val}"
    ws["A4"].value = ""

    # --- Header row ---
    headers = ["#", "Model", "Name", "Qty", "Unit Price", "Total"]
    col_widths = [5, 18, 30, 8, 14, 14]
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
        values = [
            row_num,
            f"{item['brand']} {item['model']}",
            item["name"],
            float(item["qty"]),
            round(float(item["unit_price"]), 2),
            round(float(item["total"]), 2),
        ]
        for col_idx, val in enumerate(values, start=1):
            cell = ws.cell(row=row_idx, column=col_idx, value=val)
            cell.border = _BORDER
            if col_idx in (1, 4):
                cell.alignment = _CENTER
            elif col_idx in (5, 6):
                cell.alignment = _RIGHT

    # --- Total row ---
    total_row_idx = header_row + len(items) + 1
    ws.merge_cells(f"A{total_row_idx}:E{total_row_idx}")
    lbl = ws[f"A{total_row_idx}"]
    lbl.value = "TOTAL"
    lbl.font = _TOTAL_FONT
    lbl.alignment = _RIGHT
    lbl.border = _BORDER

    total_cell = ws.cell(row=total_row_idx, column=6, value=round(float(invoice["total"]), 2))
    total_cell.font = _TOTAL_FONT
    total_cell.alignment = _RIGHT
    total_cell.border = _BORDER

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

"""Phase 8 — XLSX export of an inventory invoice.

The spreadsheet gives an auditor everything the on-screen /inventory
confirmation did:
  - the invoice header (number, warehouse, note, timestamp),
  - a per-product row with system qty, actual qty, delta and the frozen
    cost_price snapshot,
  - a total line showing lines count, surplus/shortage qty and the net
    cost impact (sum of delta × cost_price).

Mirrors the structure of receive_xlsx.py so the UX feels consistent.
"""
from __future__ import annotations

import io
from typing import Any

import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill, Side, Border

_HEADER_FILL      = PatternFill("solid", fgColor="4472C4")
_HEADER_FONT      = Font(bold=True, color="FFFFFF")
_TOTAL_FILL       = PatternFill("solid", fgColor="D9EEF7")
_TOTAL_FONT       = Font(bold=True)
_TOTAL_FONT_ITAL  = Font(bold=True, italic=True)
_SURPLUS_FILL     = PatternFill("solid", fgColor="E8F5E9")  # soft green
_SHORTAGE_FILL    = PatternFill("solid", fgColor="FFEBEE")  # soft red

_CENTER = Alignment(horizontal="center")
_RIGHT  = Alignment(horizontal="right")
_LEFT   = Alignment(horizontal="left")

_THIN   = Side(style="thin")
_BORDER = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)


def _make_workbook(invoice: dict[str, Any]) -> openpyxl.Workbook:
    """`invoice` must be the dict returned by get_inventory_invoice() —
    i.e. header fields + `items` list."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = f"Inventory #{invoice['number']:06d}"

    items = invoice.get("items") or []

    # --- Title block ---
    ws.merge_cells("A1:H1")
    title_cell = ws["A1"]
    title_cell.value = f"INVENTORY INVOICE #{invoice['number']:06d}"
    title_cell.font = Font(bold=True, size=14)
    title_cell.alignment = _CENTER

    ws.merge_cells("A2:H2")
    wh = invoice.get("warehouse_code") or ""
    wh_title = invoice.get("warehouse_title") or ""
    ws["A2"].value = f"Warehouse: {wh}" + (f"  ({wh_title})" if wh_title else "")

    ws.merge_cells("A3:H3")
    date_val = str(invoice.get("created_at", ""))[:19].replace("T", " ")
    ws["A3"].value = f"Date: {date_val}"

    ws.merge_cells("A4:H4")
    ws["A4"].value = f"Note: {invoice.get('note', '')}"

    ws["A5"].value = ""

    # --- Header row ---
    headers = ["#", "Brand/Model", "Name", "Barcode",
               "System qty", "Actual qty", "Δ (delta)", "Cost impact"]
    col_widths = [5, 20, 32, 18, 12, 12, 10, 14]
    header_row = 6
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
        delta = float(item.get("delta") or 0)
        cost_price = float(item.get("cost_price") or 0)
        values = [
            row_num,
            f"{item.get('brand', '')} {item.get('model', '')}".strip(),
            item.get("name", ""),
            item.get("barcode") or "",
            float(item.get("system_qty") or 0),
            float(item.get("actual_qty") or 0),
            delta,
            round(delta * cost_price, 2),
        ]
        row_fill = _SURPLUS_FILL if delta > 0 else _SHORTAGE_FILL if delta < 0 else None
        for col_idx, val in enumerate(values, start=1):
            cell = ws.cell(row=row_idx, column=col_idx, value=val)
            cell.border = _BORDER
            if col_idx in (1, 5, 6, 7):
                cell.alignment = _CENTER
            elif col_idx == 8:
                cell.alignment = _RIGHT
            if row_fill is not None:
                cell.fill = row_fill

    # --- Total row ---
    total_row_idx = header_row + len(items) + 1
    surplus_qty    = sum(float(it["delta"]) for it in items if float(it["delta"]) > 0)
    shortage_qty   = -sum(float(it["delta"]) for it in items if float(it["delta"]) < 0)
    net_cost_delta = round(sum(float(it["delta"]) * float(it.get("cost_price") or 0) for it in items), 2)

    ws.cell(row=total_row_idx, column=2, value="TOTAL").font = _TOTAL_FONT_ITAL
    ws.cell(row=total_row_idx, column=5, value=f"surplus +{surplus_qty:g}").font = _TOTAL_FONT
    ws.cell(row=total_row_idx, column=6, value=f"shortage -{shortage_qty:g}").font = _TOTAL_FONT
    ws.cell(row=total_row_idx, column=7, value=len(items)).font = _TOTAL_FONT
    ws.cell(row=total_row_idx, column=8, value=net_cost_delta).font = _TOTAL_FONT

    for col_idx in range(1, 9):
        cell = ws.cell(row=total_row_idx, column=col_idx)
        cell.border = _BORDER
        cell.fill = _TOTAL_FILL
        if col_idx == 2:
            cell.alignment = _LEFT
        elif col_idx == 8:
            cell.alignment = _RIGHT
        else:
            cell.alignment = _CENTER

    return wb


def generate_inventory_xlsx_bytes(invoice: dict[str, Any]) -> bytes:
    """Return .xlsx content as bytes (for streaming response)."""
    wb = _make_workbook(invoice)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()

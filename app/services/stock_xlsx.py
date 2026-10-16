"""Phase 13 — stock inventory XLSX export, with per-call column toggle.

Columns ALWAYS shown: #, Brand, Model, Name, Qty.
Columns toggled on by caller: Income (wh_price) + any number of markup
presets (+15 %, +25 %, …). Toggles come from the /stock view's checkbox
state, so the generated file matches what the operator sees on screen.
"""
from __future__ import annotations

import io
from typing import Any, Iterable

import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill, Side, Border

_HEADER_FILL = PatternFill("solid", fgColor="4472C4")
_HEADER_FONT = Font(bold=True, color="FFFFFF")
_TOTAL_FILL  = PatternFill("solid", fgColor="D9EEF7")
_TOTAL_FONT  = Font(bold=True)

_CENTER = Alignment(horizontal="center")
_RIGHT  = Alignment(horizontal="right")
_THIN   = Side(style="thin")
_BORDER = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)


def generate_stock_xlsx_bytes(
    rows: list[dict[str, Any]],
    warehouse_label: str,
    *,
    show_income: bool = True,
    markup_pcts: Iterable[int] = (),
    hide_zero: bool = False,
    lang: str = "en",
) -> bytes:
    """Return a stock inventory XLSX. Caller controls which columns appear."""
    # Strings per language. Only the labels — product names stay as-is.
    # Note: for export we intentionally do NOT reveal the markup percent
    # on the price-column header (operator asked: clients don't need to
    # know it was +15% vs +25% off the purchase price). We print the
    # neutral word "Price" instead; on-screen /stock still shows the
    # exact percent so the operator can tell which column is which.
    labels = {
        "en": {"title": "Stock Inventory",
               "num": "#", "brand": "Brand", "model": "Model", "name": "Name",
               "income": "Income", "price": "Price",
               "qty": "Qty", "total": "TOTAL"},
        "ru": {"title": "Остатки склада",
               "num": "№", "brand": "Бренд", "model": "Модель", "name": "Название",
               "income": "Приход", "price": "Цена",
               "qty": "Кол-во", "total": "ИТОГО"},
        "tm": {"title": "Ammar galyndylary",
               "num": "№", "brand": "Brend", "model": "Model", "name": "Ady",
               "income": "Girdeji", "price": "Baha",
               "qty": "Sany", "total": "JEMI"},
    }
    L = labels.get((lang or "en").lower(), labels["en"])

    data_rows = [r for r in rows
                 if not (hide_zero and float(r.get("qty") or 0) == 0)]
    markup_pcts = list(markup_pcts)

    # Build the header & column widths dynamically.
    headers: list[str] = [L["num"], L["brand"], L["model"], L["name"]]
    widths:  list[int] = [5, 14, 14, 28]
    if show_income:
        headers.append(L["income"]); widths.append(10)
    # Phase 13 patch — clients shouldn't see which markup produced the
    # price. Multiple markup columns all print as the plain word "Price".
    for pct in markup_pcts:
        headers.append(L["price"]); widths.append(12)
    headers.append(L["qty"]); widths.append(8)
    n_cols = len(headers)

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Stock"

    # --- Title spans all data columns ---
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=n_cols)
    title_cell = ws.cell(row=1, column=1, value=f"{L['title']} — {warehouse_label}")
    title_cell.font = Font(bold=True, size=14)
    title_cell.alignment = _CENTER

    # --- Header row ---
    header_row = 3
    for col_idx, (h, w) in enumerate(zip(headers, widths), start=1):
        cell = ws.cell(row=header_row, column=col_idx, value=h)
        cell.font = _HEADER_FONT
        cell.fill = _HEADER_FILL
        cell.alignment = _CENTER
        cell.border = _BORDER
        ws.column_dimensions[cell.column_letter].width = w

    # --- Data rows ---
    for i, r in enumerate(data_rows, start=1):
        row_idx = header_row + i
        wh_price = float(r.get("wh_price") or 0)
        values: list[Any] = [i, r.get("brand") or "", r.get("model") or "", r.get("name") or ""]
        if show_income:
            values.append(round(wh_price, 2))
        for pct in markup_pcts:
            values.append(round(wh_price * (1 + pct / 100.0), 2))
        values.append(int(r.get("qty") or 0))

        for col_idx, val in enumerate(values, start=1):
            cell = ws.cell(row=row_idx, column=col_idx, value=val)
            cell.border = _BORDER
            # Right-align everything except brand/model/name blocks.
            if col_idx == 1:
                cell.alignment = _CENTER
            elif col_idx >= 5:  # price columns + qty
                cell.alignment = _RIGHT

    # --- Total row (just sum of qty) ---
    if data_rows:
        total_row_idx = header_row + len(data_rows) + 1
        total_qty = sum(int(r.get("qty") or 0) for r in data_rows)
        for col_idx in range(1, n_cols + 1):
            cell = ws.cell(row=total_row_idx, column=col_idx)
            cell.fill = _TOTAL_FILL
            cell.border = _BORDER
        lbl = ws.cell(row=total_row_idx, column=2, value=L["total"])
        lbl.font = _TOTAL_FONT
        qty_cell = ws.cell(row=total_row_idx, column=n_cols, value=total_qty)
        qty_cell.font = _TOTAL_FONT
        qty_cell.alignment = _RIGHT

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()

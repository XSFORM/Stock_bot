"""
Company stamp image helper.

Mirrors signature_asset.py but for the top-right stamp used on sale invoices
(PDF + XLSX) and client-history exports. The admin uploads a PNG in
/admin/settings; the file is written to the location returned by
`get_stamp_path_for_upload()` and auto-cleaned (near-white → transparent,
cropped to content) so a scanned stamp with white borders / rounded corners
loses the faint grey frame that would otherwise print around it.

The default path is /opt/stock_bot/stamp.png — the same path that
invoice_pdf.py and invoice_xlsx.py have read from since day one, so
existing stamps keep working without any migration.
"""

from __future__ import annotations

import io
import os
from pathlib import Path


_DEFAULT_STAMP_PATH = Path("/opt/stock_bot/stamp.png")


def _resolved_path() -> Path:
    override = (os.getenv("STOCK_BOT_STAMP_PATH") or "").strip()
    if override:
        return Path(override)
    return _DEFAULT_STAMP_PATH


def get_stamp_path_for_upload() -> Path:
    """Where the upload endpoint should write the file."""
    p = _resolved_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def get_stamp_path() -> Path | None:
    """Return the path if a stamp image exists, else None."""
    p = _resolved_path()
    try:
        return p if p.exists() and p.stat().st_size > 0 else None
    except OSError:
        return None


def delete_stamp() -> bool:
    """Remove the stamp file. Returns True if a file was removed."""
    p = _resolved_path()
    try:
        if p.exists():
            p.unlink()
            return True
    except OSError:
        pass
    return False


def process_uploaded_stamp(src_bytes: bytes, *, white_threshold: int = 230) -> bytes:
    """Clean an uploaded stamp PNG: make near-white pixels transparent and
    crop to the bounding box of the real ink.

    Why: scanned / exported stamps often sit on a white rectangle with a
    rounded or rectangular grey border. Overlaying that on an invoice
    prints a visible frame around the logo. Converting near-white to
    alpha=0 and cropping removes both the border and the surrounding
    whitespace so the stamp sits cleanly on the page.

    Returns the cleaned PNG as bytes. Falls back to the original bytes
    if anything goes wrong — the user still gets a working (if dirtier)
    stamp rather than a 500 error.
    """
    try:
        from PIL import Image
        with Image.open(io.BytesIO(src_bytes)) as im:
            img = im.convert("RGBA")

        px = img.load()
        w, h = img.size
        for y in range(h):
            for x in range(w):
                r, g, b, a = px[x, y]
                if r >= white_threshold and g >= white_threshold and b >= white_threshold:
                    px[x, y] = (255, 255, 255, 0)

        bbox = img.getbbox()
        if bbox:
            img = img.crop(bbox)

        # Keep file size sensible — scanned stamps can be huge, and printing
        # at >1200 px wide buys nothing visible on A4.
        if img.width > 1200:
            img.thumbnail((1200, 1200), Image.LANCZOS)

        out = io.BytesIO()
        img.save(out, "PNG", optimize=True)
        return out.getvalue()
    except Exception:
        return src_bytes

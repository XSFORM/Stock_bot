"""
Seller-signature image helper.

The admin can upload a PNG of their handwritten signature in /admin/settings;
the file is written to the location returned by `get_signature_path_for_upload()`.
All downstream renderers (sale invoice PDF/XLSX, client-history PDF/XLSX/JPEG)
call `get_signature_path()` to decide whether to overlay the image on the
"Seller: ______" line.

A missing file is the normal state on fresh installs and on the desktop
edition — rendering silently falls back to the underscored line.
"""

from __future__ import annotations

import os
from pathlib import Path


# ── Resolution rules ────────────────────────────────────────────────────────
# 1. STOCK_BOT_SIGNATURE_PATH env var (admin-overridable, used by desktop
#    config.py to put the file into the user-writable data dir).
# 2. Fallback: /opt/stock_bot/signature.png — matches the convention used
#    for the invoice stamp (STAMP_PATH in invoice_pdf.py / invoice_xlsx.py).

_DEFAULT_SIGNATURE_PATH = Path("/opt/stock_bot/signature.png")


def _resolved_path() -> Path:
    override = (os.getenv("STOCK_BOT_SIGNATURE_PATH") or "").strip()
    if override:
        return Path(override)
    return _DEFAULT_SIGNATURE_PATH


def get_signature_path_for_upload() -> Path:
    """Where the upload endpoint should write the file."""
    p = _resolved_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def get_signature_path() -> Path | None:
    """Return the path if a signature image exists, else None."""
    p = _resolved_path()
    try:
        return p if p.exists() and p.stat().st_size > 0 else None
    except OSError:
        return None


def delete_signature() -> bool:
    """Remove the signature file. Returns True if a file was removed."""
    p = _resolved_path()
    try:
        if p.exists():
            p.unlink()
            return True
    except OSError:
        pass
    return False

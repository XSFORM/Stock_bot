from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

from app.db.sqlite import DB_PATH


def _default_backup_dir() -> Path:
    data_dir = DB_PATH.parent.resolve()
    configured = os.getenv("BACKUP_DIR", "").strip()
    if configured:
        raw = Path(configured)
        candidate = raw if raw.is_absolute() else (data_dir / raw)
        try:
            resolved = candidate.resolve()
            if resolved == data_dir or data_dir in resolved.parents:
                return resolved
        except OSError:
            pass
    return data_dir / "backups"


def _default_invoices_dir() -> Path:
    return Path(os.getenv("INVOICES_DIR", str(Path(__file__).resolve().parents[2] / "invoices")))


BACKUPS_DIR = _default_backup_dir()
INVOICES_DIR = _default_invoices_dir()
# Backward-compatible alias.
BACKUP_DIR = BACKUPS_DIR


def make_backup() -> str:
    BACKUPS_DIR.mkdir(parents=True, exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    zip_path = BACKUPS_DIR / f"backup_{ts}.zip"

    with ZipFile(zip_path, "w", compression=ZIP_DEFLATED) as z:
        if DB_PATH.exists():
            z.write(DB_PATH, arcname="stock.db")
        if INVOICES_DIR.exists():
            for p in INVOICES_DIR.glob("*.pdf"):
                z.write(p, arcname=f"invoices/{p.name}")

    return str(zip_path)

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

from app.db.sqlite import DB_PATH


BACKUP_DIR = Path("/opt/stock_bot/backups")
INVOICES_DIR = Path("/opt/stock_bot/invoices")


def make_backup() -> str:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    zip_path = BACKUP_DIR / f"backup_{ts}.zip"

    with ZipFile(zip_path, "w", compression=ZIP_DEFLATED) as z:
        if DB_PATH.exists():
            z.write(DB_PATH, arcname="stock.db")
        if INVOICES_DIR.exists():
            for p in INVOICES_DIR.glob("*.pdf"):
                z.write(p, arcname=f"invoices/{p.name}")

    return str(zip_path)

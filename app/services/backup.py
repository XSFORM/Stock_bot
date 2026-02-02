from __future__ import annotations

import zipfile
from datetime import datetime
from pathlib import Path

from app.db.sqlite import DB_PATH


def make_backup_zip() -> str:
    """
    Делает ZIP: база + папка invoices (если есть).
    Возвращает путь к zip.
    """
    base_dir = Path(__file__).resolve().parents[1]  # .../app
    data_dir = base_dir / "data"
    invoices_dir = data_dir / "invoices"
    backups_dir = data_dir / "backups"
    backups_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    zip_path = backups_dir / f"backup_{ts}.zip"

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        if DB_PATH.exists():
            z.write(DB_PATH, arcname="db/stock.db")

        if invoices_dir.exists():
            for p in invoices_dir.glob("*.pdf"):
                z.write(p, arcname=f"invoices/{p.name}")

    return str(zip_path)

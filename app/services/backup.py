import os
import zipfile
from datetime import datetime

from app.config import settings

def make_backup_zip() -> str:
    os.makedirs(settings.backup_dir, exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = os.path.join(settings.backup_dir, f"backup_{ts}.zip")

    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        # db
        if os.path.exists(settings.db_path):
            z.write(settings.db_path, arcname="data/stock.db")
        # exports (pdf)
        if os.path.isdir(settings.export_dir):
            for root, _, files in os.walk(settings.export_dir):
                for f in files:
                    full = os.path.join(root, f)
                    rel = os.path.relpath(full, os.path.dirname(settings.export_dir))
                    z.write(full, arcname=f"exports/{rel}")

    return out_path

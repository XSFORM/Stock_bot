import shutil
import zipfile
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).resolve().parents[1] / "db" / "stock.db"
BACKUP_DIR = Path("/opt/stock_bot_backups")


def make_backup_zip() -> str:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    zip_path = BACKUP_DIR / f"stockbot_backup_{ts}.zip"

    if not DB_PATH.exists():
        raise FileNotFoundError(f"DB не найдена: {DB_PATH}")

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.write(DB_PATH, arcname="stock.db")

    return str(zip_path)


# Для совместимости со старым импортом
def make_backup() -> str:
    return make_backup_zip()

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[2]  # .../stock_bot
load_dotenv(dotenv_path=ROOT_DIR / ".env")


def _get_env(*keys: str, default: str | None = None) -> str | None:
    for k in keys:
        v = os.getenv(k)
        if v is not None and str(v).strip() != "":
            return v.strip()
    return default


def _get_int(*keys: str, default: int | None = None) -> int | None:
    v = _get_env(*keys, default=None)
    if v is None:
        return default
    return int(v)


def _get_path(*keys: str, default: str) -> str:
    v = _get_env(*keys, default=default)
    return str(v)


@dataclass(frozen=True)
class Settings:
    bot_token: str
    admin_id: int
    db_path: str
    export_dir: str
    backup_dir: str
    currency: str
    decimals: int


settings = Settings(
    bot_token=_get_env("BOT_TOKEN", "TELEGRAM_BOT_TOKEN", default="") or "",
    admin_id=_get_int("ADMIN_ID", "ADMIN_TG_ID", "ADMIN_TG", default=0) or 0,
    db_path=_get_path("DB_PATH", "DATABASE_PATH", default=str(ROOT_DIR / "data" / "stock.db")),
    export_dir=_get_path("EXPORT_DIR", default=str(ROOT_DIR / "exports")),
    backup_dir=_get_path("BACKUP_DIR", default=str(ROOT_DIR / "backups")),
    currency=_get_env("CURRENCY", default="USD") or "USD",
    decimals=_get_int("DECIMALS", default=2) or 2,
)

if not settings.bot_token:
    raise RuntimeError("BOT_TOKEN is empty. Set BOT_TOKEN in .env")
if not settings.admin_id:
    raise RuntimeError("ADMIN_ID is empty. Set ADMIN_ID (or ADMIN_TG_ID) in .env")

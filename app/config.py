import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()

def _get_env(name: str, default: str | None = None) -> str:
    val = os.getenv(name, default)
    if val is None or val == "":
        raise RuntimeError(f"Missing required env var: {name}")
    return val

@dataclass(frozen=True)
class Settings:
    bot_token: str
    admin_tg_id: int
    db_path: str
    export_dir: str
    backup_dir: str
    currency: str
    decimals: int

settings = Settings(
    bot_token=_get_env("BOT_TOKEN"),
    admin_tg_id=int(_get_env("ADMIN_TG_ID")),
    db_path=_get_env("DB_PATH"),
    export_dir=_get_env("EXPORT_DIR"),
    backup_dir=_get_env("BACKUP_DIR"),
    currency=_get_env("CURRENCY", "USD"),
    decimals=int(_get_env("DECIMALS", "2")),
)

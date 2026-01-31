from app.config import settings

def money(v: float) -> str:
    return f"{v:.{settings.decimals}f} {settings.currency}"

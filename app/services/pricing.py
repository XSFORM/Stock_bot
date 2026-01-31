from app.config import settings

def calc_wh10(wh_price: float) -> float:
    return round(wh_price * 1.10, settings.decimals)

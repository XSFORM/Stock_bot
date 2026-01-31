def require_positive_number(v: float, name: str = "value") -> None:
    if v <= 0:
        raise ValueError(f"{name} must be > 0")

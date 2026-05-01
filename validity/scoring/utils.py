from __future__ import annotations


def safe_div(a: float, b: float, default: float = 0.0) -> float:
    """Divide a by b, returning *default* if b is zero or division fails."""
    try:
        return a / b if b != 0 else default
    except (TypeError, ZeroDivisionError):
        return default


def clamp(x: float, min_val: float = 0.0, max_val: float = 1.0) -> float:
    """Clamp *x* to [min_val, max_val]."""
    return max(min_val, min(max_val, x))

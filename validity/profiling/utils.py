from __future__ import annotations

import re


def infer_shape(value: str) -> str:
    if value is None:
        return "NULL"

    s = str(value)
    s = re.sub(r"[A-Z]", "L", s)
    s = re.sub(r"[a-z]", "L", s)
    s = re.sub(r"[0-9]", "D", s)
    return s


def safe_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def safe_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default

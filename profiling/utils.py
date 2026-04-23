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


def normalize_char_class_ratio(text: str) -> dict[str, float]:
    if not text:
        return {
            "alpha": 0.0,
            "digit": 0.0,
            "space": 0.0,
            "special": 0.0,
        }

    total_len = len(text)
    alpha = sum(ch.isalpha() for ch in text) / total_len
    digit = sum(ch.isdigit() for ch in text) / total_len
    space = sum(ch.isspace() for ch in text) / total_len
    special = 1.0 - alpha - digit - space

    return {
        "alpha": alpha,
        "digit": digit,
        "space": space,
        "special": special,
    }
import numpy as np


def safe_div(a, b, default=0.0):
    try:
        return a / b if b != 0 else default
    except:
        return default


def clamp(x, min_val=0.0, max_val=1.0):
    return max(min_val, min(max_val, x))
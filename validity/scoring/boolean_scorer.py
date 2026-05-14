from __future__ import annotations

from validity.scoring.null_handler import score_null
from validity.scoring.utils import clamp

_TRUTHY = {"true", "1", "yes", "y", "t"}
_FALSY  = {"false", "0", "no", "n", "f"}


def _parse_bool(value) -> bool | None:
    if isinstance(value, bool):
        return value
    try:
        s = str(value).strip().lower()
        if s in _TRUTHY:
            return True
        if s in _FALSY:
            return False
    except Exception:
        pass
    return None


def score_boolean(value, profile: dict) -> dict:
    null_result = score_null(value, profile)
    if null_result is not None:
        return null_result

    true_rate = float(profile.get("true_rate", 0.5))
    features  = {"true_rate": true_rate}

    val = _parse_bool(value)
    if val is None:
        return {"score": 0.0, "reasons": [], "features": features}

    features["value"] = val

    if val is False:
        if true_rate >= 0.99:
            return {"score": 0.4, "reasons": ["unexpected_false"], "features": features}
        if true_rate >= 0.95:
            return {"score": 0.2, "reasons": ["rare_false"], "features": features}
    else:
        if true_rate <= 0.01:
            return {"score": 0.4, "reasons": ["unexpected_true"], "features": features}
        if true_rate <= 0.05:
            return {"score": 0.2, "reasons": ["rare_true"], "features": features}

    return {"score": 0.0, "reasons": [], "features": features}

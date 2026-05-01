from __future__ import annotations

import pandas as pd


def score_null(value, profile: dict) -> dict | None:
    """
    Handle null scoring uniformly across all column scorers.

    Returns a score dict if *value* is null, or ``None`` if it is not.
    This lets callers short-circuit with a simple ``if result is not None``.
    """
    try:
        is_null = bool(pd.isna(value))
    except (TypeError, ValueError):
        # pd.isna raises on some container types; treat those as non-null
        return None

    if not is_null:
        return None

    null_allowed   = bool(profile.get("null_allowed", True))
    null_rate      = float(profile.get("null_rate", 0.0))
    db_is_nullable = profile.get("db_is_nullable")

    features = {
        "null_allowed":  null_allowed,
        "null_rate":     null_rate,
        "db_is_nullable": db_is_nullable,
    }

    if not null_allowed:
        return {"score": 1.0, "reasons": ["null_not_allowed"], "features": features}

    # Null in a nullable column is usually fine — penalise only if nulls are
    # historically very rare (< 1 %), which suggests this one is unexpected.
    if null_rate < 0.01:
        return {"score": 0.15, "reasons": ["rare_null"], "features": features}

    return {"score": 0.0, "reasons": [], "features": features}

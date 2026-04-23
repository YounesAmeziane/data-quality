from __future__ import annotations

import pandas as pd


def score_null(value, profile: dict):
    """
    Handle null scoring consistently across all scorers.

    Returns:
        - dict result if value is null
        - None if value is not null
    """
    if not pd.isna(value):
        return None

    null_allowed = bool(profile.get("null_allowed", True))
    null_rate = float(profile.get("null_rate", 0.0))
    db_is_nullable = profile.get("db_is_nullable", None)

    if not null_allowed:
        return {
            "score": 1.0,
            "reasons": ["null_not_allowed"],
            "features": {
                "null_allowed": null_allowed,
                "null_rate": null_rate,
                "db_is_nullable": db_is_nullable,
            },
        }

    # nullable column: null is okay unless it's extremely rare
    if null_rate < 0.01:
        return {
            "score": 0.15,
            "reasons": ["rare_null"],
            "features": {
                "null_allowed": null_allowed,
                "null_rate": null_rate,
                "db_is_nullable": db_is_nullable,
            },
        }

    return {
        "score": 0.0,
        "reasons": [],
        "features": {
            "null_allowed": null_allowed,
            "null_rate": null_rate,
            "db_is_nullable": db_is_nullable,
        },
    }
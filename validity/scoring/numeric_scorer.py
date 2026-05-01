from __future__ import annotations

import pandas as pd

from validity.scoring.null_handler import score_null
from validity.scoring.utils import clamp

# Robust Z-score thresholds for anomaly severity
_EXTREME_Z = 12
_STRONG_Z  = 6
_MODERATE_Z = 3


def score_numeric(value, profile: dict) -> dict:
    null_result = score_null(value, profile)
    if null_result is not None:
        return null_result

    reasons: list[str] = []
    features: dict     = {}
    score              = 0.0

    parsed = pd.to_numeric(value, errors="coerce")
    if pd.isna(parsed):
        return {"score": 0.3, "reasons": ["parse_fail"], "features": {"parsed": None}}

    median = profile.get("median")
    iqr    = float(profile.get("iqr", 0.0))
    p01    = profile.get("p01")
    p99    = profile.get("p99")

    features["parsed"] = float(parsed)

    if median is None:
        return {"score": 0.0, "reasons": [], "features": features}

    # Robust Z-score (IQR-based, resistant to outliers in the profile itself)
    z = abs(parsed - median) / iqr if iqr > 0 else 0.0
    features["robust_z"] = float(z)

    within_p01_p99 = (
        p01 is not None and p99 is not None
        and float(p01) <= float(parsed) <= float(p99)
    )

    if z > _EXTREME_Z:
        if within_p01_p99:
            # Value was seen during profiling (inside p01-p99) — likely a valid sentinel
            # code in a tight-IQR column, not a true anomaly. Cap at moderate severity.
            score += 0.1
            reasons.append("moderate_outlier")
        else:
            score += 0.7
            reasons.append("extreme_outlier")
    elif z > _STRONG_Z:
        if within_p01_p99:
            score += 0.1
            reasons.append("moderate_outlier")
        else:
            score += 0.35
            reasons.append("strong_outlier")
    elif z > _MODERATE_Z:
        score += 0.1
        reasons.append("moderate_outlier")
    elif p01 is not None and p99 is not None and iqr > 0:
        # Only apply percentile guard when no Z-score signal was raised — both checks
        # fire on the same tail values, so stacking them double-counts the same anomaly.
        if float(parsed) < float(p01) or float(parsed) > float(p99):
            score += 0.1
            reasons.append("outside_p01_p99")

    return {"score": clamp(score), "reasons": reasons, "features": features}

from __future__ import annotations

import pandas as pd

from scoring.utils import clamp


def score_numeric(value, profile):
    reasons = []
    features = {}
    score = 0.0

    parsed = pd.to_numeric(value, errors="coerce")
    if pd.isna(parsed):
        # parse_fail means the column was likely mistyped, not that the row is broken
        # cap at 0.3 so a single unparseable column doesn't nuke the whole row score
        return {
            "score": 0.3,
            "reasons": ["parse_fail"],
            "features": {"parsed": None},
        }

    median = profile.get("median")
    iqr = float(profile.get("iqr", 0.0))
    p01 = profile.get("p01")
    p99 = profile.get("p99")

    features["parsed"] = float(parsed)

    if median is None:
        return {
            "score": 0.0,
            "reasons": [],
            "features": features,
        }

    if iqr > 0:
        z = abs(parsed - median) / iqr
    else:
        z = 0.0

    features["robust_z"] = float(z)

    if z > 12:
        score += 0.7
        reasons.append("extreme_outlier")
    elif z > 6:
        score += 0.35
        reasons.append("strong_outlier")
    elif z > 3:
        score += 0.1
        reasons.append("moderate_outlier")

    # skip percentile guard when IQR is near-zero — uniform distributions
    # like date dimension keys have p01/p99 that are just the min/max,
    # which flags every row near the edges as anomalous
    if p01 is not None and p99 is not None and iqr > 0:
        if parsed < p01 or parsed > p99:
            score += 0.1
            reasons.append("outside_p01_p99")

    return {
        "score": clamp(score),
        "reasons": reasons,
        "features": features,
    }
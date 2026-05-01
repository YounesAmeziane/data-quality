from __future__ import annotations

from validity.scoring.null_handler import score_null
from validity.scoring.utils import clamp


def score_categorical(value, profile):
    null_result = score_null(value, profile)
    if null_result is not None:
        return null_result

    reasons = []
    features = {}
    score = 0.0

    value = str(value)
    freq_map = profile.get("top_values", {})
    distinct_ratio = float(profile.get("distinct_ratio", 1.0))
    distinct_count = int(profile.get("distinct_count", 0))

    high_cardinality = distinct_ratio > 0.2 or distinct_count > 100

    if value not in freq_map:
        if high_cardinality:
            score += 0.1
            reasons.append("unseen_value_high_cardinality")
        else:
            score += 0.35
            reasons.append("unseen_value")
        features["seen_in_top_values"] = False
    else:
        freq = float(freq_map[value])
        features["seen_in_top_values"] = True
        features["frequency"] = freq

        if freq < 0.001:
            score += 0.25
            reasons.append("rare_value")
        elif freq < 0.01:
            score += 0.1
            reasons.append("low_frequency")

    return {
        "score": clamp(score),
        "reasons": reasons,
        "features": features,
    }
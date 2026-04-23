from __future__ import annotations

from profiling.utils import infer_shape
from scoring.utils import clamp


def score_text(value, profile):
    reasons = []
    features = {}
    score = 0.0

    if value is None:
        return {
            "score": 0.1,
            "reasons": ["null"],
            "features": {},
        }

    value = str(value)

    avg_len = float(profile.get("avg_length", 0))
    std_len = float(profile.get("std_length", 0))
    shapes = profile.get("top_shapes", {})
    logical_type = profile.get("logical_type", "structured_text")

    # Length deviation
    if std_len <= 0:
        len_dev = 0.0
    else:
        len_dev = abs(len(value) - avg_len) / std_len

    features["len_dev"] = len_dev

    if len_dev > 6:
        score += 0.25
        reasons.append("length_anomaly")
    elif len_dev > 3:
        score += 0.10
        reasons.append("length_deviation")

    # Shape anomaly
    shape = infer_shape(value)
    shape_freq = float(shapes.get(shape, 0.0))

    features["shape"] = shape
    features["shape_freq"] = shape_freq

    # identifiers / structured text can use shape more strongly
    if logical_type in {"identifier", "structured_text"}:
        if shape_freq == 0:
            score += 0.25
            reasons.append("unseen_shape")
        elif shape_freq < 0.01:
            score += 0.10
            reasons.append("rare_shape")
    else:
        # free text should barely care about shape
        if shape_freq == 0:
            score += 0.05
            reasons.append("unseen_shape_soft")

    return {
        "score": clamp(score),
        "reasons": reasons,
        "features": features,
    }
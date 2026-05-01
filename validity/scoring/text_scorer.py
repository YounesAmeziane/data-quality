from __future__ import annotations

from validity.profiling.utils import infer_shape
from validity.scoring.null_handler import score_null
from validity.scoring.utils import clamp


def score_text(value, profile):
    null_result = score_null(value, profile)
    if null_result is not None:
        return null_result

    reasons = []
    features = {}
    score = 0.0

    value = str(value)

    avg_len = float(profile.get("avg_length", 0))
    std_len = float(profile.get("std_length", 0))
    shapes = profile.get("top_shapes", {})
    logical_type = profile.get("logical_type", "structured_text")

    len_dev = abs(len(value) - avg_len) / std_len if std_len > 0 else 0.0
    features["len_dev"] = len_dev

    if len_dev > 6:
        score += 0.25
        reasons.append("length_anomaly")
    elif len_dev > 3:
        score += 0.10
        reasons.append("length_deviation")

    shape = infer_shape(value)
    shape_freq = float(shapes.get(shape, 0.0))
    distinct_ratio = float(profile.get("distinct_ratio", 0.0))
    high_cardinality = distinct_ratio > 0.5

    features["shape"] = shape
    features["shape_freq"] = shape_freq

    # check if there's a dominant shape (one shape covers >90% of values)
    top_shape_freq = max(shapes.values()) if shapes else 0.0
    has_dominant_shape = top_shape_freq >= 0.9

    if logical_type in {"identifier", "structured_text"}:
        if shape_freq == 0:
            if has_dominant_shape:
                # column has a very consistent format — deviation is highly suspicious
                score += 0.5
                reasons.append("shape_violation")
            elif high_cardinality:
                # naturally varied shapes, be lenient
                score += 0.05
                reasons.append("unseen_shape")
            else:
                # no dominant structure — an unseen shape is a soft hint, not a violation
                score += 0.1
                reasons.append("unseen_shape")
        elif shape_freq < 0.01:
            score += 0.10
            reasons.append("rare_shape")
    else:
        if shape_freq == 0:
            score += 0.05
            reasons.append("unseen_shape_soft")

    return {
        "score": clamp(score),
        "reasons": reasons,
        "features": features,
    }
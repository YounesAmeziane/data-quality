from __future__ import annotations

import pandas as pd
import numpy as np

from validity.profiling.utils import infer_shape


def score_column(series: pd.Series, profile: dict) -> pd.Series:
    """Score every value in a column at once. Returns a float Series in [0, 1]."""
    logical_type = profile.get("logical_type", "unknown")

    if logical_type == "numeric":
        return _score_numeric(series, profile)
    if logical_type == "categorical":
        return _score_categorical(series, profile)
    if logical_type in {"structured_text", "identifier", "free_text"}:
        return _score_text(series, profile)
    return _null_scores(series, profile)[0]  # datetime / boolean / unknown


# ---------------------------------------------------------------------------
# Null helper
# ---------------------------------------------------------------------------

def _null_scores(series: pd.Series, profile: dict) -> tuple[pd.Series, pd.Series]:
    """Return (scores, is_null). Non-null rows start at 0.0."""
    null_allowed = bool(profile.get("null_allowed", True))
    null_rate    = float(profile.get("null_rate", 0.0))

    try:
        is_null = series.isna()
    except Exception:
        is_null = pd.Series(False, index=series.index)

    scores = pd.Series(0.0, index=series.index)

    if not null_allowed:
        scores[is_null] = 1.0
    elif null_rate < 0.01:
        scores[is_null] = 0.15

    return scores, is_null


# ---------------------------------------------------------------------------
# Numeric
# ---------------------------------------------------------------------------

def _score_numeric(series: pd.Series, profile: dict) -> pd.Series:
    scores, is_null = _null_scores(series, profile)
    non_null = ~is_null

    parsed     = pd.to_numeric(series, errors="coerce")
    parse_fail = parsed.isna() & non_null
    valid      = non_null & ~parse_fail

    scores[parse_fail] = 0.3

    median = profile.get("median")
    iqr    = float(profile.get("iqr", 0.0))
    p01    = profile.get("p01")
    p99    = profile.get("p99")

    if median is None or not valid.any():
        return scores.clip(0, 1)

    vals = parsed[valid]
    z    = (vals - median).abs() / iqr if iqr > 0 else pd.Series(0.0, index=vals.index)

    within = pd.Series(False, index=vals.index)
    if p01 is not None and p99 is not None:
        within = (vals >= float(p01)) & (vals <= float(p99))

    col = pd.Series(0.0, index=vals.index)

    extreme  = z > 12
    strong   = (z > 6)  & (z <= 12)
    moderate = (z > 3)  & (z <= 6)
    no_z     = z <= 3

    col[extreme  &  within] = 0.1
    col[extreme  & ~within] = 0.7
    col[strong   &  within] = 0.1
    col[strong   & ~within] = 0.35
    col[moderate]           = 0.1

    if p01 is not None and p99 is not None and iqr > 0:
        outside = no_z & ((vals < float(p01)) | (vals > float(p99)))
        col[outside] = 0.1

    scores[valid] = col
    return scores.clip(0, 1)


# ---------------------------------------------------------------------------
# Categorical
# ---------------------------------------------------------------------------

def _score_categorical(series: pd.Series, profile: dict) -> pd.Series:
    scores, is_null = _null_scores(series, profile)
    non_null = ~is_null

    freq_map       = profile.get("top_values", {})
    distinct_ratio = float(profile.get("distinct_ratio", 1.0))
    distinct_count = int(profile.get("distinct_count", 0))
    high_card      = distinct_ratio > 0.2 or distinct_count > 100

    str_vals = series[non_null].astype(str)
    seen     = str_vals.isin(freq_map)

    unseen_idx = str_vals.index[~seen]
    scores[unseen_idx] = 0.1 if high_card else 0.35

    seen_vals = str_vals[seen]
    if not seen_vals.empty:
        freqs = seen_vals.map(freq_map).astype(float)
        scores[freqs.index[freqs < 0.001]]                      = 0.25
        scores[freqs.index[(freqs >= 0.001) & (freqs < 0.01)]]  = 0.1

    return scores.clip(0, 1)


# ---------------------------------------------------------------------------
# Text
# ---------------------------------------------------------------------------

def _score_text(series: pd.Series, profile: dict) -> pd.Series:
    scores, is_null = _null_scores(series, profile)
    non_null = ~is_null

    avg_len      = float(profile.get("avg_length", 0))
    std_len      = float(profile.get("std_length", 0))
    shapes_map   = profile.get("top_shapes", {})
    logical_type = profile.get("logical_type", "structured_text")
    dist_ratio   = float(profile.get("distinct_ratio", 0.0))
    high_card    = dist_ratio > 0.5

    top_freq         = max(shapes_map.values()) if shapes_map else 0.0
    has_dominant     = top_freq >= 0.9

    str_vals = series[non_null].astype(str)

    # Length deviation
    if std_len > 0:
        len_dev = (str_vals.str.len() - avg_len).abs() / std_len
        length_score = pd.Series(0.0, index=str_vals.index)
        length_score[len_dev > 6] = 0.25
        length_score[(len_dev > 3) & (len_dev <= 6)] = 0.1
        scores[str_vals.index] = (scores[str_vals.index] + length_score).clip(0, 1)

    # Shape scoring
    if logical_type in {"identifier", "structured_text"}:
        shape_series = str_vals.map(infer_shape)
        shape_freq   = shape_series.map(lambda s: float(shapes_map.get(s, 0.0)))

        unseen = shape_freq == 0
        rare   = (shape_freq > 0) & (shape_freq < 0.01)

        shape_score = pd.Series(0.0, index=str_vals.index)
        if has_dominant:
            shape_score[unseen] = 0.5
        elif high_card:
            shape_score[unseen] = 0.05
        else:
            shape_score[unseen] = 0.1
        shape_score[rare] = 0.1

        scores[str_vals.index] = (scores[str_vals.index] + shape_score).clip(0, 1)

    elif logical_type == "free_text":
        shape_series = str_vals.map(infer_shape)
        shape_freq   = shape_series.map(lambda s: float(shapes_map.get(s, 0.0)))
        unseen_score = pd.Series(0.0, index=str_vals.index)
        unseen_score[shape_freq == 0] = 0.05
        scores[str_vals.index] = (scores[str_vals.index] + unseen_score).clip(0, 1)

    return scores.clip(0, 1)

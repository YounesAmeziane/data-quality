from __future__ import annotations

import pandas as pd
import numpy as np


def score_column(series: pd.Series, profile: dict) -> pd.Series:
    """Score every value in a column at once. Returns a float Series in [0, 1]."""
    logical_type = profile.get("logical_type", "unknown")

    if logical_type == "numeric":
        return _score_numeric(series, profile)
    if logical_type == "categorical":
        return _score_categorical(series, profile)
    if logical_type in {"structured_text", "identifier", "free_text"}:
        return _score_text(series, profile)
    if logical_type == "datetime":
        return _score_datetime(series, profile)
    if logical_type == "boolean":
        return _score_boolean(series, profile)
    return _null_scores(series, profile)[0]  # unknown


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

    top_freq     = max(shapes_map.values()) if shapes_map else 0.0
    has_dominant = top_freq >= 0.9

    str_vals = series[non_null].astype(str)

    # Length deviation
    if std_len > 0:
        len_dev = (str_vals.str.len() - avg_len).abs() / std_len
        length_score = pd.Series(0.0, index=str_vals.index)
        length_score[len_dev > 6] = 0.25
        length_score[(len_dev > 3) & (len_dev <= 6)] = 0.1
        scores[str_vals.index] = (scores[str_vals.index] + length_score).clip(0, 1)

    # Shape scoring — fully vectorized via pandas str operations (no Python-level loop)
    if logical_type in {"identifier", "structured_text"}:
        shape_series = (
            str_vals.str.replace(r"[A-Za-z]", "L", regex=True)
                    .str.replace(r"[0-9]",    "D", regex=True)
        )
        shape_freq = shape_series.map(shapes_map).fillna(0.0).astype(float)

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
        shape_series = (
            str_vals.str.replace(r"[A-Za-z]", "L", regex=True)
                    .str.replace(r"[0-9]",    "D", regex=True)
        )
        shape_freq   = shape_series.map(shapes_map).fillna(0.0).astype(float)
        unseen_score = pd.Series(0.0, index=str_vals.index)
        unseen_score[shape_freq == 0] = 0.05
        scores[str_vals.index] = (scores[str_vals.index] + unseen_score).clip(0, 1)

    return scores.clip(0, 1)


# ---------------------------------------------------------------------------
# Datetime
# ---------------------------------------------------------------------------

def _score_datetime(series: pd.Series, profile: dict) -> pd.Series:
    scores, is_null = _null_scores(series, profile)
    non_null = ~is_null

    if not non_null.any():
        return scores

    future_rate  = float(profile.get("future_rate", 1.0))
    min_date_str = profile.get("min_date")
    max_date_str = profile.get("max_date")

    try:
        parsed = pd.to_datetime(series, errors="coerce", format="mixed")
    except TypeError:
        parsed = pd.to_datetime(series, errors="coerce")

    try:
        if parsed.dt.tz is not None:
            parsed = parsed.dt.tz_localize(None)
    except Exception:
        pass

    parse_fail = parsed.isna() & non_null
    scores[parse_fail] = 0.3

    valid = non_null & ~parse_fail
    if not valid.any():
        return scores.clip(0, 1)

    now  = pd.Timestamp.now(tz=None)
    vals = parsed[valid]

    # Future date — flag when historically rare
    is_future = vals > now
    if is_future.any():
        if future_rate < 0.01:
            scores[vals.index[is_future]] = 0.5
        elif future_rate < 0.05:
            scores[vals.index[is_future]] = 0.2

    # Historical range check for non-future values
    not_future_idx = vals.index[~is_future]
    if min_date_str and max_date_str and len(not_future_idx) > 0:
        try:
            min_dt = pd.Timestamp(min_date_str)
            max_dt = pd.Timestamp(max_date_str)

            if min_dt.tz is not None:
                min_dt = min_dt.tz_localize(None)
            if max_dt.tz is not None:
                max_dt = max_dt.tz_localize(None)

            range_days = max((max_dt - min_dt).days, 1)
            nf_vals    = vals.loc[not_future_idx]

            too_early = nf_vals < min_dt
            if too_early.any():
                early_days = (min_dt - nf_vals[too_early]).dt.days
                far        = early_days.index[early_days / range_days > 0.5]
                near       = early_days.index[early_days / range_days <= 0.5]
                scores[far]  = np.maximum(scores[far].values,  0.35)
                scores[near] = np.maximum(scores[near].values, 0.1)

            too_late = nf_vals > max_dt
            if too_late.any():
                late_days = (nf_vals[too_late] - max_dt).dt.days
                far       = late_days.index[late_days / range_days > 0.5]
                near      = late_days.index[late_days / range_days <= 0.5]
                scores[far]  = np.maximum(scores[far].values,  0.2)
                scores[near] = np.maximum(scores[near].values, 0.1)
        except Exception:
            pass

    return scores.clip(0, 1)


# ---------------------------------------------------------------------------
# Boolean
# ---------------------------------------------------------------------------

def _score_boolean(series: pd.Series, profile: dict) -> pd.Series:
    scores, is_null = _null_scores(series, profile)
    non_null = ~is_null

    if not non_null.any():
        return scores

    true_rate = float(profile.get("true_rate", 0.5))

    # Skip when distribution is balanced — neither value is anomalous
    if 0.05 < true_rate < 0.95:
        return scores

    _truthy = {"true", "1", "yes", "y", "t"}
    _falsy  = {"false", "0", "no", "n", "f"}

    str_lower = series[non_null].astype(str).str.strip().str.lower()
    is_true   = str_lower.isin(_truthy)
    is_false  = str_lower.isin(_falsy)

    if true_rate >= 0.99:
        scores[is_false.index[is_false]] = 0.4
    elif true_rate >= 0.95:
        scores[is_false.index[is_false]] = 0.2

    if true_rate <= 0.01:
        scores[is_true.index[is_true]] = 0.4
    elif true_rate <= 0.05:
        scores[is_true.index[is_true]] = 0.2

    return scores.clip(0, 1)

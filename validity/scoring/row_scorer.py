from __future__ import annotations

from validity.scanning.column_filter import should_scan_column
from validity.scoring.scorer import score_value


def score_row(
    row,
    column_profiles: dict,
    row_score_threshold: float = 1.0,
) -> dict:
    """
    Score a single row against pre-computed column profiles.

    The combined row score uses a probabilistic complement formula:
    ``row_score = 1 - ∏(1 - col_score_i)``.
    This means a single severe column anomaly (score ≈ 1.0) drives the row
    score close to 1.0, while many mild signals accumulate gradually.

    Returns a dict with keys:
        ``row_score``        – float in [0, 1]
        ``flagged``          – True if row_score >= threshold
        ``details``          – list of per-column score dicts (score > 0 only)
        ``skipped_columns``  – columns excluded from scoring with reason
    """
    column_scores: list[float] = []
    details: list[dict]        = []
    skipped_columns: list[dict] = []

    for col, value in row.items():
        profile = column_profiles.get(col)
        should_scan, skip_reason = should_scan_column(col, profile)

        if not should_scan:
            skipped_columns.append({"column": col, "reason": skip_reason})
            continue

        result = score_value(value, profile)
        score  = float(result.get("score", 0.0))
        column_scores.append(score)

        if score > 0:
            details.append({
                "column":   col,
                "score":    score,
                "reasons":  result.get("reasons", []),
                "features": result.get("features", {}),
            })

    # Probabilistic combination (independent-anomaly assumption).
    # Only scores >= 0.15 contribute — 0.1 signals are soft hints that are tracked
    # in details but shouldn't accumulate across many columns to trip the threshold.
    _MIN_CONTRIBUTION = 0.15
    combined = 1.0
    for s in column_scores:
        if s >= _MIN_CONTRIBUTION:
            combined *= 1.0 - clamp(s)
    row_score = 1.0 - combined

    return {
        "row_score":       row_score,
        "flagged":         row_score >= row_score_threshold,
        "details":         details,
        "skipped_columns": skipped_columns,
    }


def clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))

from __future__ import annotations

from scanning.column_filter import should_scan_column
from scoring.scorer import score_value


def score_row(row, column_profiles: dict, row_score_threshold: float = 1.0):
    column_scores = []
    details = []
    skipped_columns = []

    for col, value in row.items():
        profile = column_profiles.get(col)

        should_scan, skip_reason = should_scan_column(col, profile)

        if not should_scan:
            skipped_columns.append(
                {
                    "column": col,
                    "reason": skip_reason,
                }
            )
            continue

        result = score_value(value, profile)

        score = float(result.get("score", 0.0))
        reasons = result.get("reasons", [])
        features = result.get("features", {})

        column_scores.append(score)

        if score > 0:
            details.append(
                {
                    "column": col,
                    "score": score,
                    "reasons": reasons,
                    "features": features,
                }
            )

    combined = 1.0
    for s in column_scores:
        s = min(max(s, 0.0), 1.0)
        combined *= (1.0 - s)

    row_score = 1.0 - combined

    return {
        "row_score": row_score,
        "flagged": row_score >= row_score_threshold,
        "details": details,
        "skipped_columns": skipped_columns,
    }
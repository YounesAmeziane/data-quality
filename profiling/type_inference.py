from __future__ import annotations

import pandas as pd


def infer_logical_type(series: pd.Series, column_name: str) -> str:
    col = column_name.lower()
    non_null = series.dropna()

    if non_null.empty:
        return "categorical"

    if pd.api.types.is_bool_dtype(series):
        return "boolean"

    if pd.api.types.is_numeric_dtype(series):
        return "numeric"

    if pd.api.types.is_datetime64_any_dtype(series):
        return "datetime"

    if any(token in col for token in ["date", "time", "timestamp"]):
        parsed_dt = pd.to_datetime(non_null, errors="coerce", format="mixed")
        if parsed_dt.notna().mean() > 0.9:
            return "datetime"

    if any(token in col for token in ["email", "phone", "postal", "zip", "code"]):
        return "structured_text"

    if col.endswith("_id") or col == "id":
        return "identifier"

    parsed_num = pd.to_numeric(non_null, errors="coerce")
    numeric_rate = parsed_num.notna().mean()
    if numeric_rate > 0.95:
        return "numeric"

    s = non_null.astype(str)
    distinct_ratio = s.nunique() / max(len(s), 1)
    avg_length = s.str.len().mean()

    if distinct_ratio > 0.95 and avg_length <= 40:
        return "identifier"

    if distinct_ratio < 0.05 and avg_length <= 30:
        return "categorical"

    if avg_length > 60:
        return "free_text"

    return "structured_text"
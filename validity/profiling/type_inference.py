from __future__ import annotations

import pandas as pd

# Token sets for column-name heuristics
_DATE_TOKENS   = frozenset(["date", "time", "timestamp"])
_STRUCT_TOKENS = frozenset(["email", "phone", "postal", "zip", "code"])

# YYYYMMDD integer range (inclusive)
_YYYYMMDD_MIN = 19_000_101
_YYYYMMDD_MAX = 21_001_231


def infer_logical_type(series: pd.Series, column_name: str) -> str:
    """
    Infer a logical type for a pandas Series using dtype info and column-name
    heuristics.  Returns one of:
    ``boolean`` | ``datetime`` | ``numeric`` | ``structured_text`` |
    ``identifier`` | ``categorical`` | ``free_text``
    """
    col = column_name.lower()
    non_null = series.dropna()

    if non_null.empty:
        return "categorical"

    # --- boolean ---
    if pd.api.types.is_bool_dtype(series):
        return "boolean"

    # --- numeric (may still be a date-key) ---
    if pd.api.types.is_numeric_dtype(series):
        if any(tok in col for tok in _DATE_TOKENS):
            looks_yyyymmdd = (
                (non_null >= _YYYYMMDD_MIN) & (non_null <= _YYYYMMDD_MAX)
            ).mean() > 0.9
            if looks_yyyymmdd:
                return "datetime"
        return "numeric"

    # --- native datetime dtype ---
    if pd.api.types.is_datetime64_any_dtype(series):
        return "datetime"

    # --- string heuristics below this point ---
    # datetime by name + parse test
    if any(tok in col for tok in _DATE_TOKENS):
        if pd.to_datetime(non_null, errors="coerce", format="mixed").notna().mean() > 0.9:
            return "datetime"

    # structured text by column name
    if any(tok in col for tok in _STRUCT_TOKENS):
        return "structured_text"

    # explicit identifier by name
    if col.endswith("_id") or col == "id":
        return "identifier"

    # numeric-looking strings
    if pd.to_numeric(non_null, errors="coerce").notna().mean() > 0.95:
        return "numeric"

    s = non_null.astype(str)
    n = max(len(s), 1)
    distinct_ratio = s.nunique() / n
    avg_length = s.str.len().mean()

    if distinct_ratio > 0.95 and avg_length <= 40:
        return "identifier"

    if distinct_ratio < 0.05 and avg_length <= 30:
        return "categorical"

    if avg_length > 60:
        return "free_text"

    return "structured_text"

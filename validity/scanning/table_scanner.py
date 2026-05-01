from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from validity.profiling.db import read_table_sample
from validity.profiling.profile_loader import load_column_profiles
from validity.scoring.row_scorer import score_row


def scan_table(
    database_name: str,
    schema_name: str,
    table_name: str,
    profile_dir: str,
    sample_rows: int = 50_000,
    row_score_threshold: float = 0.7,
) -> dict[str, Any]:
    """
    Scan one table using previously generated column profiles.

    Returns a summary dict plus a list of flagged-row details.  Rows whose
    ``row_score`` is *at or above* ``row_score_threshold`` are included in
    ``flagged_rows``.
    """
    df = read_table_sample(
        database=database_name,
        schema_name=schema_name,
        table_name=table_name,
        sample_rows=sample_rows,
    )

    base_meta = {
        "database_name": database_name,
        "schema_name":   schema_name,
        "table_name":    table_name,
        "scanned_at":    datetime.now(timezone.utc).isoformat(),
        "threshold":     row_score_threshold,
    }

    if df.empty:
        return {
            **base_meta,
            "row_count_scanned":          0,
            "flagged_row_count":          0,
            "flagged_rate":               0.0,
            "skipped_column_reason_counts": {},
            "flagged_rows":               [],
        }

    column_profiles = load_column_profiles(
        output_dir=profile_dir,
        database_name=database_name,
        schema_name=schema_name,
        table_name=table_name,
    )

    flagged_rows: list[dict[str, Any]]        = []
    skipped_reason_counts: dict[str, int]     = {}

    for row_index, row in df.iterrows():
        result = score_row(
            row=row,
            column_profiles=column_profiles,
            row_score_threshold=row_score_threshold,
        )

        # Accumulate skip-reason stats (once per table, not once per row)
        for skipped in result.get("skipped_columns", []):
            reason = skipped["reason"]
            skipped_reason_counts[reason] = skipped_reason_counts.get(reason, 0) + 1

        if result["row_score"] >= row_score_threshold:
            flagged_rows.append({
                "row_index":       int(row_index),
                "row_score":       float(result["row_score"]),
                "flagged":         bool(result["flagged"]),
                "details":         result["details"],
                "skipped_columns": result.get("skipped_columns", []),
                "row_data":        _make_json_safe_dict(row.to_dict()),
            })

    row_count   = int(len(df))
    flagged_n   = int(len(flagged_rows))

    return {
        **base_meta,
        "row_count_scanned":          row_count,
        "flagged_row_count":          flagged_n,
        "flagged_rate":               flagged_n / row_count if row_count else 0.0,
        "skipped_column_reason_counts": skipped_reason_counts,
        "flagged_rows":               flagged_rows,
    }


def save_scan_results(scan_result: dict[str, Any], output_dir: str) -> str:
    os.makedirs(output_dir, exist_ok=True)

    db     = scan_result["database_name"]
    schema = scan_result["schema_name"]
    table  = scan_result["table_name"]
    path   = os.path.join(output_dir, f"{db}__{schema}__{table}__anomalies.json")

    with open(path, "w", encoding="utf-8") as f:
        json.dump(scan_result, f, indent=2, ensure_ascii=False, default=_json_default)

    return path


# ---------------------------------------------------------------------------
# JSON serialisation helpers
# ---------------------------------------------------------------------------

def _make_json_safe_dict(data: dict[str, Any]) -> dict[str, Any]:
    return {k: _make_json_safe_value(v) for k, v in data.items()}


def _make_json_safe_value(value: Any) -> Any:
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass

    if isinstance(value, pd.Timestamp):
        return value.isoformat()

    if hasattr(value, "item"):          # numpy scalars
        try:
            return value.item()
        except Exception:
            pass

    return value


def _json_default(obj: Any) -> Any:
    if isinstance(obj, pd.Timestamp):
        return obj.isoformat()
    if hasattr(obj, "item"):
        try:
            return obj.item()
        except Exception:
            pass
    return str(obj)

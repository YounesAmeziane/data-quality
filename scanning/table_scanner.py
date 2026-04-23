from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any

import pandas as pd

from profiling.db import read_table_sample
from profiling.profile_loader import load_column_profiles
from scoring.row_scorer import score_row


def scan_table(
    database_name: str,
    schema_name: str,
    table_name: str,
    profile_dir: str,
    sample_rows: int = 50000,
    row_score_threshold: float = 0.7,
) -> dict[str, Any]:
    """
    Scan one table using previously generated column profiles.
    Returns a summary plus flagged row details.
    """
    df = read_table_sample(
        database=database_name,
        schema_name=schema_name,
        table_name=table_name,
        sample_rows=sample_rows,
    )

    if df.empty:
        return {
            "database_name": database_name,
            "schema_name": schema_name,
            "table_name": table_name,
            "scanned_at": datetime.utcnow().isoformat(),
            "row_count_scanned": 0,
            "flagged_row_count": 0,
            "flagged_rate": 0.0,
            "threshold": row_score_threshold,
            "skipped_column_reason_counts": {},
            "flagged_rows": [],
        }

    column_profiles = load_column_profiles(
        output_dir=profile_dir,
        database_name=database_name,
        schema_name=schema_name,
        table_name=table_name,
    )

    flagged_rows: list[dict[str, Any]] = []
    skipped_column_reason_counts: dict[str, int] = {}

    for row_index, row in df.iterrows():
        row_result = score_row(
            row=row,
            column_profiles=column_profiles,
            row_score_threshold=row_score_threshold,
        )

        for skipped in row_result.get("skipped_columns", []):
            reason = skipped["reason"]
            skipped_column_reason_counts[reason] = (
                skipped_column_reason_counts.get(reason, 0) + 1
            )

        if row_result["row_score"] >= row_score_threshold:
            flagged_rows.append(
                {
                    "row_index": int(row_index),
                    "row_score": float(row_result["row_score"]),
                    "flagged": bool(row_result["flagged"]),
                    "details": row_result["details"],
                    "skipped_columns": row_result.get("skipped_columns", []),
                    "row_data": _make_json_safe_dict(row.to_dict()),
                }
            )

    row_count_scanned = int(len(df))
    flagged_row_count = int(len(flagged_rows))
    flagged_rate = flagged_row_count / row_count_scanned if row_count_scanned else 0.0

    return {
        "database_name": database_name,
        "schema_name": schema_name,
        "table_name": table_name,
        "scanned_at": datetime.utcnow().isoformat(),
        "row_count_scanned": row_count_scanned,
        "flagged_row_count": flagged_row_count,
        "flagged_rate": flagged_rate,
        "threshold": row_score_threshold,
        "skipped_column_reason_counts": skipped_column_reason_counts,
        "flagged_rows": flagged_rows,
    }


def save_scan_results(
    scan_result: dict[str, Any],
    output_dir: str,
) -> str:
    os.makedirs(output_dir, exist_ok=True)

    database_name = scan_result["database_name"]
    schema_name = scan_result["schema_name"]
    table_name = scan_result["table_name"]

    filename = f"{database_name}__{schema_name}__{table_name}__anomalies.json"
    path = os.path.join(output_dir, filename)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(scan_result, f, indent=2, ensure_ascii=False, default=_json_default)

    return path


def _make_json_safe_dict(data: dict[str, Any]) -> dict[str, Any]:
    return {key: _make_json_safe_value(value) for key, value in data.items()}


def _make_json_safe_value(value: Any) -> Any:
    if pd.isna(value):
        return None

    if isinstance(value, pd.Timestamp):
        return value.isoformat()

    if hasattr(value, "item"):
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
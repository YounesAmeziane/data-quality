from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

import pandas as pd
from sqlalchemy import text

from validity.profiling.db import read_table_sample
from validity.profiling.profile_loader import load_column_profiles
from validity.scoring.row_scorer import score_row


def scan_table(
    database_name: str,
    schema_name: str,
    table_name: str,
    sample_rows: int = 50_000,
    row_score_threshold: float = 0.7,
) -> dict[str, Any]:
    """
    Scan one table using column profiles stored in dbo.profiles.

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


def save_scan_results(scan_result: dict[str, Any]) -> int:
    """
    Persist scan results to the three validity tables in the metadata DB.
    Returns the job_id from validity_scan_runs.
    """
    from validity.profiling.db import get_engine

    metadata_db     = os.getenv("METADATA_DATABASE", "MetadataRepository")
    engine          = get_engine(metadata_db)
    qualified_table = f"{scan_result['schema_name']}.{scan_result['table_name']}"

    with engine.begin() as conn:
        row = conn.execute(
            text("""
                INSERT INTO dbo.validity_scan_runs
                    (db_name, table_name, scanned_at, threshold, rows_scanned, rows_flagged, flagged_rate)
                OUTPUT INSERTED.id
                VALUES (:db_name, :table_name, :scanned_at, :threshold,
                        :rows_scanned, :rows_flagged, :flagged_rate)
            """),
            {
                "db_name":      scan_result["database_name"],
                "table_name":   qualified_table,
                "scanned_at":   datetime.fromisoformat(scan_result["scanned_at"]),
                "threshold":    scan_result["threshold"],
                "rows_scanned": scan_result["row_count_scanned"],
                "rows_flagged": scan_result["flagged_row_count"],
                "flagged_rate": scan_result["flagged_rate"],
            },
        ).fetchone()
        job_id = row[0]

        for flagged_row in scan_result.get("flagged_rows", []):
            anomaly_row = conn.execute(
                text("""
                    INSERT INTO dbo.validity_anomaly_rows (job_id, row_index, row_score, row_data)
                    OUTPUT INSERTED.id
                    VALUES (:job_id, :row_index, :row_score, :row_data)
                """),
                {
                    "job_id":    job_id,
                    "row_index": flagged_row["row_index"],
                    "row_score": flagged_row["row_score"],
                    "row_data":  json.dumps(flagged_row["row_data"], ensure_ascii=False, default=_json_default),
                },
            ).fetchone()
            anomaly_row_id = anomaly_row[0]

            for detail in flagged_row.get("details", []):
                conn.execute(
                    text("""
                        INSERT INTO dbo.validity_anomaly_details
                            (anomaly_row_id, column_name, column_score, reasons)
                        VALUES (:anomaly_row_id, :column_name, :column_score, :reasons)
                    """),
                    {
                        "anomaly_row_id": anomaly_row_id,
                        "column_name":    detail["column"],
                        "column_score":   detail["score"],
                        "reasons":        ", ".join(detail.get("reasons", [])),
                    },
                )

    return job_id


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

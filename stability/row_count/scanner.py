from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

import pandas as pd
from sqlalchemy import text

from validity.profiling.db import get_engine

_STABILITY_DB       = os.getenv("JOB_QUEUE_DATABASE", "hrdm_dev")
_WINDOW             = int(os.getenv("STABILITY_WINDOW", "30"))
_Z_THRESHOLD        = float(os.getenv("STABILITY_Z_THRESHOLD", "3.0"))
_CHANGE_PCT_THRESHOLD = float(os.getenv("STABILITY_CHANGE_PCT_THRESHOLD", "0.5"))


def get_row_count(database: str, schema: str, table: str) -> int:
    """Fast approximate row count via sys.partitions (avoids full table scan)."""
    engine = get_engine(database)
    with engine.connect() as conn:
        result = conn.execute(
            text("""
                SELECT ISNULL(SUM(p.rows), 0)
                FROM sys.partitions p
                INNER JOIN sys.tables  t ON p.object_id = t.object_id
                INNER JOIN sys.schemas s ON t.schema_id = s.schema_id
                WHERE s.name = :schema AND t.name = :table
                  AND p.index_id IN (0, 1)
            """),
            {"schema": schema, "table": table},
        ).scalar()
    return int(result)


def _load_snapshots(db_name: str, table_name: str) -> list[int]:
    """Return the last _WINDOW row counts for this table, newest first."""
    engine = get_engine(_STABILITY_DB)
    with engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT TOP (:window) row_count
                FROM dbo.row_count_snapshots
                WHERE db_name    = :db_name
                  AND table_name = :table_name
                ORDER BY snapshotted_at DESC
            """),
            {"window": _WINDOW, "db_name": db_name, "table_name": table_name},
        ).fetchall()
    return [int(r[0]) for r in rows]


def _save_snapshot(db_name: str, table_name: str, row_count: int) -> None:
    engine = get_engine(_STABILITY_DB)
    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO dbo.row_count_snapshots (db_name, table_name, row_count, snapshotted_at)
                VALUES (:db_name, :table_name, :row_count, :now)
            """),
            {
                "db_name":    db_name,
                "table_name": table_name,
                "row_count":  row_count,
                "now":        datetime.now(timezone.utc),
            },
        )


def save_run_result(result: dict[str, Any], job_id: int | None = None) -> None:
    engine = get_engine(_STABILITY_DB)
    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO dbo.row_count_runs
                    (job_id, db_name, table_name, row_count, previous_count,
                     change_pct, run_at, anomaly, z_score)
                VALUES
                    (:job_id, :db_name, :table_name, :row_count, :previous_count,
                     :change_pct, :run_at, :anomaly, :z_score)
            """),
            {
                "job_id":         job_id,
                "db_name":        result["db_name"],
                "table_name":     result["table_name"],
                "row_count":      result["row_count"],
                "previous_count": result.get("previous_count"),
                "change_pct":     result.get("change_pct"),
                "run_at":         datetime.now(timezone.utc),
                "anomaly":        result["anomaly"],
                "z_score":        result.get("z_score"),
            },
        )


def check_table(database: str, schema: str, table: str) -> dict[str, Any]:
    """
    Snapshot the current row count, compare against rolling baseline, return result.
    Always saves a new snapshot before returning so the baseline stays current.
    """
    qualified = f"{schema}.{table}"
    current   = get_row_count(database, schema, table)
    history   = _load_snapshots(database, qualified)

    result: dict[str, Any] = {
        "db_name":        database,
        "table_name":     qualified,
        "row_count":      current,
        "previous_count": history[0] if history else None,
        "change_pct":     None,
        "z_score":        None,
        "anomaly":        False,
    }

    if history:
        prev = history[0]
        result["change_pct"] = (current - prev) / prev if prev else None

    if len(history) >= 3:
        s   = pd.Series(history, dtype=float)
        mean = float(s.mean())
        std  = float(s.std(ddof=1))

        if std > 0:
            z = abs(current - mean) / std
            result["z_score"] = round(z, 4)
            if z >= _Z_THRESHOLD:
                result["anomaly"] = True
        elif result["change_pct"] is not None and abs(result["change_pct"]) >= _CHANGE_PCT_THRESHOLD:
            # Count was always stable — any big jump is anomalous
            result["anomaly"] = True

    elif result["change_pct"] is not None and abs(result["change_pct"]) >= _CHANGE_PCT_THRESHOLD:
        # Fallback when baseline is thin: flag very large single-step changes
        result["anomaly"] = True

    _save_snapshot(database, qualified, current)
    return result

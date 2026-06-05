from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

import pandas as pd
from sqlalchemy import text

from validity.profiling.db import get_engine

_METADATA_DB    = os.getenv("METADATA_DATABASE", "MetadataRepository")
_MAX_DETAIL_ROWS = 10_000  # cap stored discrepancy rows to avoid runaway inserts


# ---------------------------------------------------------------------------
# Schema helpers
# ---------------------------------------------------------------------------

def _get_columns(database: str, schema: str, table: str) -> list[str]:
    engine = get_engine(database)
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT c.name
            FROM sys.columns c
            INNER JOIN sys.tables  t ON c.object_id = t.object_id
            INNER JOIN sys.schemas s ON t.schema_id = s.schema_id
            WHERE s.name = :schema AND t.name = :table
            ORDER BY c.column_id
        """), {"schema": schema, "table": table}).fetchall()
    return [r[0] for r in rows]


# ---------------------------------------------------------------------------
# Hash loading
# ---------------------------------------------------------------------------

def _build_hash_query(schema: str, table: str, join_key: str, columns: list[str]) -> str:
    hash_cols = [c for c in columns if c.lower() != join_key.lower()]
    hash_expr = " + '|' + ".join(
        f"ISNULL(CAST([{c}] AS NVARCHAR(MAX)), 'NULL')"
        for c in hash_cols
    )
    return f"""
        SELECT
            CAST([{join_key}] AS NVARCHAR(500)) AS join_key,
            CONVERT(NVARCHAR(64), HASHBYTES('SHA2_256', {hash_expr}), 2) AS row_hash
        FROM [{schema}].[{table}]
    """


def _load_hashes(database: str, schema: str, table: str, join_key: str, columns: list[str]) -> pd.DataFrame:
    engine = get_engine(database)
    query  = _build_hash_query(schema, table, join_key, columns)
    with engine.connect() as conn:
        return pd.read_sql(text(query), conn)


# ---------------------------------------------------------------------------
# Row fetching (batched to stay within SQL Server parameter limits)
# ---------------------------------------------------------------------------

def _load_rows(database: str, schema: str, table: str, join_key: str, keys: list[str]) -> pd.DataFrame:
    if not keys:
        return pd.DataFrame()

    engine     = get_engine(database)
    batch_size = 500
    frames: list[pd.DataFrame] = []

    for i in range(0, len(keys), batch_size):
        batch        = keys[i : i + batch_size]
        placeholders = ", ".join(f":key_{j}" for j in range(len(batch)))
        params       = {f"key_{j}": k for j, k in enumerate(batch)}
        query        = f"SELECT * FROM [{schema}].[{table}] WHERE CAST([{join_key}] AS NVARCHAR(500)) IN ({placeholders})"
        with engine.connect() as conn:
            frames.append(pd.read_sql(text(query), conn, params=params))

    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


# ---------------------------------------------------------------------------
# Core comparison
# ---------------------------------------------------------------------------

def compare_tables(
    source_db: str, source_schema: str, source_table: str,
    target_db: str, target_schema: str, target_table: str,
    join_key: str,
) -> dict[str, Any]:

    source_cols = _get_columns(source_db, source_schema, source_table)
    target_cols = _get_columns(target_db, target_schema, target_table)
    common_cols = [c for c in source_cols if c in set(target_cols)]

    if join_key not in common_cols:
        raise ValueError(f"Join key '{join_key}' not found in both tables.")

    source_hashes = _load_hashes(source_db, source_schema, source_table, join_key, common_cols)
    target_hashes = _load_hashes(target_db, target_schema, target_table, join_key, common_cols)

    source_set = set(source_hashes["join_key"])
    target_set = set(target_hashes["join_key"])

    missing_keys = list(source_set - target_set)
    extra_keys   = list(target_set - source_set)

    merged       = source_hashes.merge(target_hashes, on="join_key", suffixes=("_src", "_tgt"))
    modified_keys = list(merged[merged["row_hash_src"] != merged["row_hash_tgt"]]["join_key"])

    discrepancies: list[dict] = []

    for key in missing_keys:
        discrepancies.append({
            "join_key_value": key,
            "issue_type":     "missing",
            "column_name":    None,
            "source_value":   "(row exists)",
            "target_value":   "(row missing)",
        })

    for key in extra_keys:
        discrepancies.append({
            "join_key_value": key,
            "issue_type":     "extra",
            "column_name":    None,
            "source_value":   "(row missing)",
            "target_value":   "(row exists)",
        })

    # For modified rows fetch actual data and diff column by column
    if modified_keys:
        detail_keys = modified_keys[:_MAX_DETAIL_ROWS]
        src_rows = _load_rows(source_db, source_schema, source_table, join_key, detail_keys)
        tgt_rows = _load_rows(target_db, target_schema, target_table, join_key, detail_keys)

        compare_cols = [c for c in common_cols if c.lower() != join_key.lower()]

        src_rows = src_rows.set_index(join_key)
        tgt_rows = tgt_rows.set_index(join_key)

        for key in detail_keys:
            if key not in src_rows.index or key not in tgt_rows.index:
                continue
            src_row = src_rows.loc[key]
            tgt_row = tgt_rows.loc[key]

            # Handle duplicate keys — take first occurrence
            if isinstance(src_row, pd.DataFrame):
                src_row = src_row.iloc[0]
            if isinstance(tgt_row, pd.DataFrame):
                tgt_row = tgt_row.iloc[0]

            for col in compare_cols:
                src_val = src_row.get(col)
                tgt_val = tgt_row.get(col)

                def _to_str(v):
                    if v is None:
                        return None
                    try:
                        if pd.isna(v):
                            return None
                    except Exception:
                        pass
                    return str(v)

                src_str = _to_str(src_val)
                tgt_str = _to_str(tgt_val)

                if src_str != tgt_str:
                    discrepancies.append({
                        "join_key_value": key,
                        "issue_type":     "modified",
                        "column_name":    col,
                        "source_value":   src_str,
                        "target_value":   tgt_str,
                    })

    return {
        "source_db":      source_db,
        "source_table":   f"{source_schema}.{source_table}",
        "target_db":      target_db,
        "target_table":   f"{target_schema}.{target_table}",
        "join_key":       join_key,
        "source_count":   len(source_hashes),
        "target_count":   len(target_hashes),
        "missing_count":  len(missing_keys),
        "extra_count":    len(extra_keys),
        "modified_count": len(modified_keys),
        "discrepancies":  discrepancies,
    }


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def save_results(result: dict[str, Any], job_id: int | None = None) -> int:
    engine = get_engine(_METADATA_DB)

    with engine.begin() as conn:
        run_row = conn.execute(text("""
            INSERT INTO dm_dq.consistency_runs
                (job_id, source_db, source_table, target_db, target_table, join_key,
                 scanned_at, source_count, target_count, missing_count, extra_count, modified_count)
            OUTPUT INSERTED.id
            VALUES
                (:job_id, :source_db, :source_table, :target_db, :target_table, :join_key,
                 :scanned_at, :source_count, :target_count, :missing_count, :extra_count, :modified_count)
        """), {
            "job_id":         job_id,
            "source_db":      result["source_db"],
            "source_table":   result["source_table"],
            "target_db":      result["target_db"],
            "target_table":   result["target_table"],
            "join_key":       result["join_key"],
            "scanned_at":     datetime.now(timezone.utc),
            "source_count":   result["source_count"],
            "target_count":   result["target_count"],
            "missing_count":  result["missing_count"],
            "extra_count":    result["extra_count"],
            "modified_count": result["modified_count"],
        }).fetchone()

        run_id = run_row[0]

        if result["discrepancies"]:
            conn.execute(text("""
                INSERT INTO dm_dq.consistency_result
                    (run_id, job_id, join_key_value, issue_type, column_name, source_value, target_value)
                VALUES
                    (:run_id, :job_id, :join_key_value, :issue_type, :column_name, :source_value, :target_value)
            """), [
                {
                    "run_id":          run_id,
                    "job_id":          job_id,
                    "join_key_value":  d["join_key_value"],
                    "issue_type":      d["issue_type"],
                    "column_name":     d["column_name"],
                    "source_value":    d["source_value"],
                    "target_value":    d["target_value"],
                }
                for d in result["discrepancies"]
            ])

    return run_id

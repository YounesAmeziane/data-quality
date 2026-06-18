from __future__ import annotations

import json
import os
from collections import Counter
from datetime import datetime, timezone
from typing import Any

import pandas as pd
from sqlalchemy import text

from validity.profiling.db import get_engine

_METADATA_DB     = os.getenv("METADATA_DATABASE", "MetadataRepository")
_MAX_DETAIL_ROWS = 10_000


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
# Hash helpers
# ---------------------------------------------------------------------------

def _hash_expr(columns: list[str]) -> str:
    return " + '|' + ".join(
        f"ISNULL(CAST([{c}] AS NVARCHAR(MAX)), 'NULL')"
        for c in columns
    )


def _load_row_hashes(database: str, schema: str, table: str, columns: list[str]) -> list[str]:
    """Return a list of row hashes (one per row, duplicates preserved)."""
    engine = get_engine(database)
    query  = f"""
        SELECT CONVERT(NVARCHAR(64), HASHBYTES('SHA2_256', {_hash_expr(columns)}), 2) AS row_hash
        FROM [{schema}].[{table}]
    """
    with engine.connect() as conn:
        rows = conn.execute(text(query)).fetchall()
    return [r[0] for r in rows]


def _load_rows_by_hash(
    database: str, schema: str, table: str,
    columns: list[str], hash_values: set[str],
) -> pd.DataFrame:
    """Fetch rows whose full-row hash is in hash_values (SQL-side filter)."""
    if not hash_values:
        return pd.DataFrame()

    engine     = get_engine(database)
    batch_size = 500
    frames: list[pd.DataFrame] = []
    hash_list  = list(hash_values)

    for i in range(0, len(hash_list), batch_size):
        batch        = hash_list[i : i + batch_size]
        placeholders = ", ".join(f":h_{j}" for j in range(len(batch)))
        params       = {f"h_{j}": h for j, h in enumerate(batch)}
        cols_select  = ", ".join(f"[{c}]" for c in columns)
        query = f"""
            SELECT {cols_select},
                   CONVERT(NVARCHAR(64), HASHBYTES('SHA2_256', {_hash_expr(columns)}), 2) AS row_hash
            FROM [{schema}].[{table}]
            WHERE CONVERT(NVARCHAR(64), HASHBYTES('SHA2_256', {_hash_expr(columns)}), 2)
                  IN ({placeholders})
        """
        with engine.connect() as conn:
            frames.append(pd.read_sql(text(query), conn, params=params))

    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _load_keyed_hashes(
    database: str, schema: str, table: str,
    join_key: str, columns: list[str],
) -> pd.DataFrame:
    """Return (join_key, row_hash) — hash excludes the join key column."""
    hash_cols = [c for c in columns if c.lower() != join_key.lower()]
    engine    = get_engine(database)
    query     = f"""
        SELECT
            CAST([{join_key}] AS NVARCHAR(500)) AS join_key,
            CONVERT(NVARCHAR(64), HASHBYTES('SHA2_256', {_hash_expr(hash_cols)}), 2) AS row_hash
        FROM [{schema}].[{table}]
    """
    with engine.connect() as conn:
        return pd.read_sql(text(query), conn)


def _load_rows_by_key(
    database: str, schema: str, table: str,
    join_key: str, keys: list[str],
) -> pd.DataFrame:
    if not keys:
        return pd.DataFrame()
    engine     = get_engine(database)
    batch_size = 500
    frames: list[pd.DataFrame] = []
    for i in range(0, len(keys), batch_size):
        batch        = keys[i : i + batch_size]
        placeholders = ", ".join(f":k_{j}" for j in range(len(batch)))
        params       = {f"k_{j}": k for j, k in enumerate(batch)}
        query        = f"SELECT * FROM [{schema}].[{table}] WHERE CAST([{join_key}] AS NVARCHAR(500)) IN ({placeholders})"
        with engine.connect() as conn:
            frames.append(pd.read_sql(text(query), conn, params=params))
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


# ---------------------------------------------------------------------------
# Keyless comparison
# ---------------------------------------------------------------------------

def _compare_keyless(
    source_db: str, source_schema: str, source_table: str,
    target_db: str, target_schema: str, target_table: str,
    common_cols: list[str],
) -> dict[str, Any]:

    src_hashes = _load_row_hashes(source_db, source_schema, source_table, common_cols)
    tgt_hashes = _load_row_hashes(target_db, target_schema, target_table, common_cols)

    src_counter = Counter(src_hashes)
    tgt_counter = Counter(tgt_hashes)

    missing_counter = src_counter - tgt_counter  # in source, not in target
    extra_counter   = tgt_counter - src_counter  # in target, not in source

    missing_hashes = list(missing_counter.elements())
    extra_hashes   = list(extra_counter.elements())

    discrepancies: list[dict] = []

    if missing_hashes:
        sample = set(list(missing_counter.keys())[:_MAX_DETAIL_ROWS])
        rows   = _load_rows_by_hash(source_db, source_schema, source_table, common_cols, sample)
        for _, row in rows.iterrows():
            row_data = {c: str(row[c]) if row[c] is not None and not (isinstance(row[c], float) and pd.isna(row[c])) else None for c in common_cols}
            discrepancies.append({
                "join_key_value": row.get("row_hash", ""),
                "issue_type":     "missing",
                "column_name":    None,
                "source_value":   json.dumps(row_data, ensure_ascii=False),
                "target_value":   None,
            })

    if extra_hashes:
        sample = set(list(extra_counter.keys())[:_MAX_DETAIL_ROWS])
        rows   = _load_rows_by_hash(target_db, target_schema, target_table, common_cols, sample)
        for _, row in rows.iterrows():
            row_data = {c: str(row[c]) if row[c] is not None and not (isinstance(row[c], float) and pd.isna(row[c])) else None for c in common_cols}
            discrepancies.append({
                "join_key_value": row.get("row_hash", ""),
                "issue_type":     "extra",
                "column_name":    None,
                "source_value":   None,
                "target_value":   json.dumps(row_data, ensure_ascii=False),
            })

    return {
        "missing_count":  len(missing_hashes),
        "extra_count":    len(extra_hashes),
        "modified_count": 0,
        "discrepancies":  discrepancies,
    }


# ---------------------------------------------------------------------------
# Keyed comparison
# ---------------------------------------------------------------------------

def _compare_keyed(
    source_db: str, source_schema: str, source_table: str,
    target_db: str, target_schema: str, target_table: str,
    join_key: str, common_cols: list[str],
) -> dict[str, Any]:

    src_df = _load_keyed_hashes(source_db, source_schema, source_table, join_key, common_cols)
    tgt_df = _load_keyed_hashes(target_db, target_schema, target_table, join_key, common_cols)

    src_set = set(src_df["join_key"])
    tgt_set = set(tgt_df["join_key"])

    missing_keys  = list(src_set - tgt_set)
    extra_keys    = list(tgt_set - src_set)
    merged        = src_df.merge(tgt_df, on="join_key", suffixes=("_src", "_tgt"))
    modified_keys = list(merged[merged["row_hash_src"] != merged["row_hash_tgt"]]["join_key"])

    discrepancies: list[dict] = []

    for key in missing_keys:
        discrepancies.append({"join_key_value": key, "issue_type": "missing",  "column_name": None, "source_value": "(row exists)",  "target_value": "(row missing)"})
    for key in extra_keys:
        discrepancies.append({"join_key_value": key, "issue_type": "extra",    "column_name": None, "source_value": "(row missing)", "target_value": "(row exists)"})

    if modified_keys:
        detail_keys  = modified_keys[:_MAX_DETAIL_ROWS]
        src_rows     = _load_rows_by_key(source_db, source_schema, source_table, join_key, detail_keys).set_index(join_key)
        tgt_rows     = _load_rows_by_key(target_db, target_schema, target_table, join_key, detail_keys).set_index(join_key)
        compare_cols = [c for c in common_cols if c.lower() != join_key.lower()]

        for key in detail_keys:
            if key not in src_rows.index or key not in tgt_rows.index:
                continue
            src_row = src_rows.loc[key]
            tgt_row = tgt_rows.loc[key]
            if isinstance(src_row, pd.DataFrame): src_row = src_row.iloc[0]
            if isinstance(tgt_row, pd.DataFrame): tgt_row = tgt_row.iloc[0]

            for col in compare_cols:
                def _s(v):
                    if v is None: return None
                    try:
                        if pd.isna(v): return None
                    except Exception: pass
                    return str(v)
                sv, tv = _s(src_row.get(col)), _s(tgt_row.get(col))
                if sv != tv:
                    discrepancies.append({"join_key_value": key, "issue_type": "modified", "column_name": col, "source_value": sv, "target_value": tv})

    return {
        "missing_count":  len(missing_keys),
        "extra_count":    len(extra_keys),
        "modified_count": len(modified_keys),
        "discrepancies":  discrepancies,
    }


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def compare_tables(
    source_db: str, source_schema: str, source_table: str,
    target_db: str, target_schema: str, target_table: str,
    join_key: str | None = None,
) -> dict[str, Any]:

    source_cols  = _get_columns(source_db, source_schema, source_table)
    target_cols  = _get_columns(target_db, target_schema, target_table)
    common_cols  = [c for c in source_cols if c in set(target_cols)]

    if join_key and join_key not in common_cols:
        raise ValueError(f"Join key '{join_key}' not found in both tables.")

    if join_key:
        counts = {
            "source_count": len(_load_keyed_hashes(source_db, source_schema, source_table, join_key, common_cols)),
            "target_count": len(_load_keyed_hashes(target_db, target_schema, target_table, join_key, common_cols)),
        }
        detail = _compare_keyed(source_db, source_schema, source_table, target_db, target_schema, target_table, join_key, common_cols)
    else:
        src_hashes = _load_row_hashes(source_db, source_schema, source_table, common_cols)
        tgt_hashes = _load_row_hashes(target_db, target_schema, target_table, common_cols)
        counts = {"source_count": len(src_hashes), "target_count": len(tgt_hashes)}
        detail = _compare_keyless(source_db, source_schema, source_table, target_db, target_schema, target_table, common_cols)

    return {
        "source_db":    source_db,
        "source_table": f"{source_schema}.{source_table}",
        "target_db":    target_db,
        "target_table": f"{target_schema}.{target_table}",
        "join_key":     join_key,
        **counts,
        **detail,
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

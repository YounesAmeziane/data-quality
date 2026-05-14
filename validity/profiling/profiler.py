from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from sqlalchemy import text

from validity.profiling.type_inference import infer_logical_type
from validity.profiling.utils import infer_shape


@dataclass
class ColumnProfile:
    database_name: str
    schema_name: str
    table_name: str
    column_name: str
    logical_type: str
    profile: dict


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build_null_metadata(series: pd.Series, column_metadata: dict | None) -> dict:
    null_rate      = float(series.isna().mean())
    non_null_rate  = float(series.notna().mean())
    meta           = column_metadata or {}

    db_is_nullable = meta.get("db_is_nullable")
    db_data_type   = meta.get("db_data_type")
    db_max_length  = meta.get("db_max_length")
    db_precision   = meta.get("db_precision")
    db_scale       = meta.get("db_scale")

    # Prefer the SQL schema's nullability; fall back to observed data.
    null_allowed = bool(db_is_nullable) if db_is_nullable is not None else bool(null_rate > 0)

    return {
        "null_rate":     null_rate,
        "non_null_rate": non_null_rate,
        "null_allowed":  null_allowed,
        "db_is_nullable": db_is_nullable,
        "db_data_type":  db_data_type,
        "db_max_length": db_max_length,
        "db_precision":  db_precision,
        "db_scale":      db_scale,
    }


# ---------------------------------------------------------------------------
# Per-type profilers
# ---------------------------------------------------------------------------

def profile_numeric(series: pd.Series, column_metadata: dict | None = None) -> dict:
    parsed = pd.to_numeric(series, errors="coerce")
    valid  = parsed.dropna()

    base = {
        "logical_type":       "numeric",
        "row_count":          int(len(series)),
        "parse_success_rate": float(parsed.notna().mean()),
        **_build_null_metadata(series, column_metadata),
    }

    if valid.empty:
        return base

    q1 = float(valid.quantile(0.25))
    q2 = float(valid.quantile(0.50))
    q3 = float(valid.quantile(0.75))

    return {
        **base,
        "median":        q2,
        "iqr":           float(q3 - q1),
        "mad":           float(np.median(np.abs(valid - q2))),
        "p01":           float(valid.quantile(0.01)),
        "p05":           float(valid.quantile(0.05)),
        "p25":           q1,
        "p50":           q2,
        "p75":           q3,
        "p95":           float(valid.quantile(0.95)),
        "p99":           float(valid.quantile(0.99)),
        "min":           float(valid.min()),
        "max":           float(valid.max()),
        "zero_rate":     float((valid == 0).mean()),
        "negative_rate": float((valid < 0).mean()),
        "distinct_count": int(valid.nunique()),
    }


def profile_categorical(series: pd.Series, column_metadata: dict | None = None) -> dict:
    s = series.dropna().astype(str)

    base = {
        "logical_type": "categorical",
        "row_count":    int(len(series)),
        **_build_null_metadata(series, column_metadata),
    }

    if s.empty:
        return base

    value_freq = s.value_counts(normalize=True)
    probs      = value_freq.values
    # Shannon entropy (bits); small epsilon avoids log(0)
    entropy    = float(-(probs * np.log2(probs + 1e-12)).sum())

    return {
        **base,
        "distinct_count": int(s.nunique()),
        "distinct_ratio": float(s.nunique() / max(len(s), 1)),
        "entropy":        entropy,
        "top_values":     value_freq.head(100).to_dict(),
    }


def profile_text_like(
    series: pd.Series,
    logical_type: str,
    column_metadata: dict | None = None,
) -> dict:
    s = series.dropna().astype(str)

    base = {
        "logical_type": logical_type,
        "row_count":    int(len(series)),
        **_build_null_metadata(series, column_metadata),
    }

    if s.empty:
        return base

    lengths    = s.str.len()
    shapes     = s.map(infer_shape)
    top_shapes = shapes.value_counts(normalize=True).head(100).to_dict()

    return {
        **base,
        "avg_length":     float(lengths.mean()),
        "std_length":     float(lengths.std(ddof=0)) if len(lengths) > 1 else 0.0,
        "min_length":     int(lengths.min()),
        "max_length":     int(lengths.max()),
        "distinct_count": int(s.nunique()),
        "distinct_ratio": float(s.nunique() / max(len(s), 1)),
        "top_shapes":     top_shapes,
    }


def profile_datetime(series: pd.Series, column_metadata: dict | None = None) -> dict:
    parsed = pd.to_datetime(series, errors="coerce", format="mixed")
    valid  = parsed.dropna()

    base = {
        "logical_type":       "datetime",
        "row_count":          int(len(series)),
        "parse_success_rate": float(parsed.notna().mean()),
        **_build_null_metadata(series, column_metadata),
    }

    if valid.empty:
        return base

    now = pd.Timestamp.now(tz=None)  # timezone-naive, matching typical SQL data
    return {
        **base,
        "min_date":    str(valid.min()),
        "max_date":    str(valid.max()),
        "future_rate": float((valid > now).mean()),
    }


def profile_boolean(series: pd.Series, column_metadata: dict | None = None) -> dict:
    non_null = series.dropna()
    n        = len(non_null)

    return {
        "logical_type": "boolean",
        "row_count":    int(len(series)),
        **_build_null_metadata(series, column_metadata),
        "true_rate":    float((non_null == True).mean())  if n else 0.0,
        "false_rate":   float((non_null == False).mean()) if n else 0.0,
    }


# ---------------------------------------------------------------------------
# Table-level orchestration
# ---------------------------------------------------------------------------

_TEXT_TYPES = {"structured_text", "identifier", "free_text"}


def build_column_profile(
    df: pd.DataFrame,
    database_name: str,
    schema_name: str,
    table_name: str,
    column_name: str,
    table_metadata: dict[str, dict] | None = None,
) -> ColumnProfile:
    series          = df[column_name]
    logical_type    = infer_logical_type(series, column_name)
    column_metadata = (table_metadata or {}).get(column_name, {})

    if logical_type == "numeric":
        profile = profile_numeric(series, column_metadata=column_metadata)
    elif logical_type == "categorical":
        profile = profile_categorical(series, column_metadata=column_metadata)
    elif logical_type in _TEXT_TYPES:
        profile = profile_text_like(series, logical_type, column_metadata=column_metadata)
    elif logical_type == "datetime":
        profile = profile_datetime(series, column_metadata=column_metadata)
    elif logical_type == "boolean":
        profile = profile_boolean(series, column_metadata=column_metadata)
    else:
        profile = {
            "logical_type": "unknown",
            "row_count":    int(len(series)),
            **_build_null_metadata(series, column_metadata),
        }

    return ColumnProfile(
        database_name=database_name,
        schema_name=schema_name,
        table_name=table_name,
        column_name=column_name,
        logical_type=logical_type,
        profile=profile,
    )


def profile_table(
    df: pd.DataFrame,
    database_name: str,
    schema_name: str,
    table_name: str,
    table_metadata: dict[str, dict] | None = None,
) -> list[ColumnProfile]:
    return [
        build_column_profile(
            df=df,
            database_name=database_name,
            schema_name=schema_name,
            table_name=table_name,
            column_name=col,
            table_metadata=table_metadata,
        )
        for col in df.columns
    ]


def save_profiles_to_db(
    profiles: list[ColumnProfile],
    database_name: str,
    schema_name: str,
    table_name: str,
) -> None:
    from validity.profiling.db import get_engine

    metadata_db = os.getenv("METADATA_DATABASE", "MetadataRepository")
    engine = get_engine(metadata_db)

    payload = {
        "database_name": database_name,
        "schema_name":   schema_name,
        "table_name":    table_name,
        "profiled_at":   datetime.now(timezone.utc).isoformat(),
        "columns":       [asdict(p) for p in profiles],
    }

    profile_json    = json.dumps(payload, ensure_ascii=False)
    qualified_table = f"{schema_name}.{table_name}"

    # last_profile is a SQL Server timestamp/rowversion column — it is auto-managed
    # by the engine and must never appear in INSERT or UPDATE statements.
    upsert = text("""
        MERGE dbo.profiles AS target
        USING (VALUES (:db_name, :tbl_name, :profile))
            AS source (db_name, table_name, profile)
        ON  target.db_name    = source.db_name
        AND target.table_name = source.table_name
        WHEN MATCHED THEN
            UPDATE SET profile = source.profile
        WHEN NOT MATCHED THEN
            INSERT (db_name, table_name, profile)
            VALUES (source.db_name, source.table_name, source.profile);
    """)

    with engine.begin() as conn:
        conn.execute(upsert, {
            "db_name":  database_name,
            "tbl_name": qualified_table,
            "profile":  profile_json,
        })

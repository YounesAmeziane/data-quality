from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime

import numpy as np
import pandas as pd

from profiling.type_inference import infer_logical_type
from profiling.utils import infer_shape, normalize_char_class_ratio


@dataclass
class ColumnProfile:
    database_name: str
    schema_name: str
    table_name: str
    column_name: str
    logical_type: str
    profile: dict


def _build_null_metadata(series: pd.Series, column_metadata: dict | None) -> dict:
    null_rate = float(series.isna().mean())
    non_null_rate = float(series.notna().mean())

    db_is_nullable = None
    db_data_type = None
    db_max_length = None
    db_precision = None
    db_scale = None

    if column_metadata:
        db_is_nullable = column_metadata.get("db_is_nullable")
        db_data_type = column_metadata.get("db_data_type")
        db_max_length = column_metadata.get("db_max_length")
        db_precision = column_metadata.get("db_precision")
        db_scale = column_metadata.get("db_scale")

    # Source of truth: SQL nullability if available
    if db_is_nullable is None:
        null_allowed = bool(null_rate > 0)
    else:
        null_allowed = bool(db_is_nullable)

    return {
        "null_rate": null_rate,
        "non_null_rate": non_null_rate,
        "null_allowed": null_allowed,
        "db_is_nullable": db_is_nullable,
        "db_data_type": db_data_type,
        "db_max_length": db_max_length,
        "db_precision": db_precision,
        "db_scale": db_scale,
    }


def profile_numeric(series: pd.Series, column_metadata: dict | None = None) -> dict:
    parsed = pd.to_numeric(series, errors="coerce")
    valid = parsed.dropna()

    base = {
        "logical_type": "numeric",
        "row_count": int(len(series)),
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
        "median": q2,
        "iqr": float(q3 - q1),
        "mad": float(np.median(np.abs(valid - q2))),
        "p01": float(valid.quantile(0.01)),
        "p05": float(valid.quantile(0.05)),
        "p25": q1,
        "p50": q2,
        "p75": q3,
        "p95": float(valid.quantile(0.95)),
        "p99": float(valid.quantile(0.99)),
        "min": float(valid.min()),
        "max": float(valid.max()),
        "zero_rate": float((valid == 0).mean()),
        "negative_rate": float((valid < 0).mean()),
        "distinct_count": int(valid.nunique()),
    }


def profile_categorical(series: pd.Series, column_metadata: dict | None = None) -> dict:
    s = series.dropna().astype(str)

    base = {
        "logical_type": "categorical",
        "row_count": int(len(series)),
        **_build_null_metadata(series, column_metadata),
    }

    if s.empty:
        return base

    value_freq = s.value_counts(normalize=True)
    probs = value_freq.values
    entropy = float(-(probs * np.log2(probs + 1e-12)).sum())

    return {
        **base,
        "distinct_count": int(s.nunique()),
        "distinct_ratio": float(s.nunique() / max(len(s), 1)),
        "entropy": entropy,
        "top_values": value_freq.head(20).to_dict(),
    }


def profile_text_like(series: pd.Series, logical_type: str, column_metadata: dict | None = None) -> dict:
    s = series.dropna().astype(str)

    base = {
        "logical_type": logical_type,
        "row_count": int(len(series)),
        **_build_null_metadata(series, column_metadata),
    }

    if s.empty:
        return base

    lengths = s.str.len()
    shapes = s.map(infer_shape)
    top_shapes = shapes.value_counts(normalize=True).head(20).to_dict()

    full_text = "".join(s.tolist())
    char_ratio = normalize_char_class_ratio(full_text)

    return {
        **base,
        "avg_length": float(lengths.mean()),
        "std_length": float(lengths.std(ddof=0)) if len(lengths) > 1 else 0.0,
        "min_length": int(lengths.min()),
        "max_length": int(lengths.max()),
        "distinct_count": int(s.nunique()),
        "distinct_ratio": float(s.nunique() / max(len(s), 1)),
        "char_class_ratio": char_ratio,
        "top_shapes": top_shapes,
    }


def profile_datetime(series: pd.Series, column_metadata: dict | None = None) -> dict:
    parsed = pd.to_datetime(series, errors="coerce", format="mixed")
    valid = parsed.dropna()

    base = {
        "logical_type": "datetime",
        "row_count": int(len(series)),
        "parse_success_rate": float(parsed.notna().mean()),
        **_build_null_metadata(series, column_metadata),
    }

    if valid.empty:
        return base

    return {
        **base,
        "min_date": str(valid.min()),
        "max_date": str(valid.max()),
        "future_rate": float((valid > pd.Timestamp.now()).mean()),
    }


def profile_boolean(series: pd.Series, column_metadata: dict | None = None) -> dict:
    non_null = series.dropna()

    return {
        "logical_type": "boolean",
        "row_count": int(len(series)),
        **_build_null_metadata(series, column_metadata),
        "true_rate": float((non_null == True).mean()) if len(non_null) else 0.0,
        "false_rate": float((non_null == False).mean()) if len(non_null) else 0.0,
    }


def build_column_profile(
    df: pd.DataFrame,
    database_name: str,
    schema_name: str,
    table_name: str,
    column_name: str,
    table_metadata: dict[str, dict] | None = None,
) -> ColumnProfile:
    series = df[column_name]
    logical_type = infer_logical_type(series, column_name)
    column_metadata = (table_metadata or {}).get(column_name, {})

    if logical_type == "numeric":
        profile = profile_numeric(series, column_metadata=column_metadata)
    elif logical_type == "categorical":
        profile = profile_categorical(series, column_metadata=column_metadata)
    elif logical_type in {"structured_text", "identifier", "free_text"}:
        profile = profile_text_like(series, logical_type, column_metadata=column_metadata)
    elif logical_type == "datetime":
        profile = profile_datetime(series, column_metadata=column_metadata)
    elif logical_type == "boolean":
        profile = profile_boolean(series, column_metadata=column_metadata)
    else:
        profile = {
            "logical_type": "unknown",
            "row_count": int(len(series)),
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
    profiles = []
    for column_name in df.columns:
        profiles.append(
            build_column_profile(
                df=df,
                database_name=database_name,
                schema_name=schema_name,
                table_name=table_name,
                column_name=column_name,
                table_metadata=table_metadata,
            )
        )
    return profiles


def save_profiles_to_json(
    profiles: list[ColumnProfile],
    output_dir: str,
    database_name: str,
    schema_name: str,
    table_name: str,
) -> str:
    os.makedirs(output_dir, exist_ok=True)

    payload = {
        "database_name": database_name,
        "schema_name": schema_name,
        "table_name": table_name,
        "profiled_at": datetime.utcnow().isoformat(),
        "columns": [asdict(p) for p in profiles],
    }

    filename = f"{database_name}__{schema_name}__{table_name}.json"
    path = os.path.join(output_dir, filename)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)

    return path
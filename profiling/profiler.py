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


def profile_numeric(series: pd.Series) -> dict:
    parsed = pd.to_numeric(series, errors="coerce")
    valid = parsed.dropna()

    base = {
        "row_count": int(len(series)),
        "null_rate": float(series.isna().mean()),
        "parse_success_rate": float(parsed.notna().mean()),
    }

    if valid.empty:
        return {
            "logical_type": "numeric",
            **base,
        }

    q1 = float(valid.quantile(0.25))
    q2 = float(valid.quantile(0.50))
    q3 = float(valid.quantile(0.75))

    return {
        "logical_type": "numeric",
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


def profile_categorical(series: pd.Series) -> dict:
    s = series.dropna().astype(str)

    base = {
        "logical_type": "categorical",
        "row_count": int(len(series)),
        "null_rate": float(series.isna().mean()),
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


def profile_text_like(series: pd.Series, logical_type: str) -> dict:
    s = series.dropna().astype(str)

    base = {
        "logical_type": logical_type,
        "row_count": int(len(series)),
        "null_rate": float(series.isna().mean()),
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


def profile_datetime(series: pd.Series) -> dict:
    parsed = pd.to_datetime(series, errors="coerce")
    valid = parsed.dropna()

    base = {
        "logical_type": "datetime",
        "row_count": int(len(series)),
        "null_rate": float(series.isna().mean()),
        "parse_success_rate": float(parsed.notna().mean()),
    }

    if valid.empty:
        return base

    return {
        **base,
        "min_date": str(valid.min()),
        "max_date": str(valid.max()),
        "future_rate": float((valid > pd.Timestamp.now()).mean()),
    }


def profile_boolean(series: pd.Series) -> dict:
    non_null = series.dropna()

    return {
        "logical_type": "boolean",
        "row_count": int(len(series)),
        "null_rate": float(series.isna().mean()),
        "true_rate": float((non_null == True).mean()) if len(non_null) else 0.0,
        "false_rate": float((non_null == False).mean()) if len(non_null) else 0.0,
    }


def build_column_profile(
    df: pd.DataFrame,
    database_name: str,
    schema_name: str,
    table_name: str,
    column_name: str,
) -> ColumnProfile:
    series = df[column_name]
    logical_type = infer_logical_type(series, column_name)

    if logical_type == "numeric":
        profile = profile_numeric(series)
    elif logical_type == "categorical":
        profile = profile_categorical(series)
    elif logical_type in {"structured_text", "identifier", "free_text"}:
        profile = profile_text_like(series, logical_type)
    elif logical_type == "datetime":
        profile = profile_datetime(series)
    elif logical_type == "boolean":
        profile = profile_boolean(series)
    else:
        profile = {
            "logical_type": "unknown",
            "row_count": int(len(series)),
            "null_rate": float(series.isna().mean()),
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
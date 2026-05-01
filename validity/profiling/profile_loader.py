from __future__ import annotations

import json
import os
from typing import Any


def build_profile_filename(database_name: str, schema_name: str, table_name: str) -> str:
    return f"{database_name}__{schema_name}__{table_name}.json"


def get_profile_path(
    output_dir: str,
    database_name: str,
    schema_name: str,
    table_name: str,
) -> str:
    filename = build_profile_filename(database_name, schema_name, table_name)
    return os.path.join(output_dir, filename)


def load_table_profile_json(
    output_dir: str,
    database_name: str,
    schema_name: str,
    table_name: str,
) -> dict[str, Any]:
    path = get_profile_path(
        output_dir=output_dir,
        database_name=database_name,
        schema_name=schema_name,
        table_name=table_name,
    )

    if not os.path.exists(path):
        raise FileNotFoundError(f"Profile file not found: {path}")

    with open(path, "r", encoding="utf-8") as f:
        payload = json.load(f)

    return payload


def load_column_profiles(
    output_dir: str,
    database_name: str,
    schema_name: str,
    table_name: str,
) -> dict[str, dict[str, Any]]:
    """
    Returns a dictionary keyed by column name.

    Output shape:
    {
        "email": {
            "logical_type": "structured_text",
            ...
        },
        "salary": {
            "logical_type": "numeric",
            ...
        }
    }
    """
    payload = load_table_profile_json(
        output_dir=output_dir,
        database_name=database_name,
        schema_name=schema_name,
        table_name=table_name,
    )

    columns = payload.get("columns", [])
    result: dict[str, dict[str, Any]] = {}

    for col in columns:
        column_name = col.get("column_name")
        profile = col.get("profile", {})

        if not column_name:
            continue

        result[column_name] = profile

    return result


def list_available_profile_files(output_dir: str) -> list[str]:
    if not os.path.exists(output_dir):
        return []

    return sorted(
        [
            os.path.join(output_dir, name)
            for name in os.listdir(output_dir)
            if name.lower().endswith(".json")
        ]
    )
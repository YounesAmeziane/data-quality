from __future__ import annotations

import json
import os
from typing import Any

from sqlalchemy import text


def load_column_profiles(
    database_name: str,
    schema_name: str,
    table_name: str,
) -> dict[str, dict[str, Any]]:
    """
    Load column profiles from the dbo.profiles table in the metadata database.

    Returns a dict keyed by column name:
        { "ColumnA": {"logical_type": "numeric", ...}, ... }

    Raises FileNotFoundError if no profile exists for the given table.
    """
    from validity.profiling.db import get_engine

    metadata_db     = os.getenv("METADATA_DATABASE", "MetadataRepository")
    engine          = get_engine(metadata_db)
    qualified_table = f"{schema_name}.{table_name}"

    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT profile FROM dbo.profiles "
                "WHERE db_name = :db_name AND table_name = :table_name"
            ),
            {"db_name": database_name, "table_name": qualified_table},
        ).fetchone()

    if row is None:
        raise FileNotFoundError(
            f"No profile found for [{database_name}].[{schema_name}].[{table_name}] "
            f"in {metadata_db}.dbo.profiles"
        )

    payload = json.loads(row[0])
    result: dict[str, dict[str, Any]] = {}

    for col in payload.get("columns", []):
        column_name = col.get("column_name")
        profile     = col.get("profile", {})
        if column_name:
            result[column_name] = profile

    return result

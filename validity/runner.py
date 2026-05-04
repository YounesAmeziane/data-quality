from __future__ import annotations

import os
import re

from validity.profiling.db import (
    get_database_list,
    get_table_column_metadata,
    list_user_tables,
    read_table_sample,
)
from validity.profiling.profiler import profile_table, save_profiles_to_db
from validity.scanning.table_scanner import save_scan_results, scan_table


def run(scan: str | None, table_name: str | None = None, job_id: int | None = None, **_) -> None:
    target = _parse_target(table_name)

    if scan == "profile":
        _run_profile(target)
    elif scan == "scan":
        _run_scan(target, job_id=job_id)
    else:
        raise ValueError(
            f"Unknown --scan value '{scan}' for validity. Use 'profile' or 'scan'."
        )


# ---------------------------------------------------------------------------
# Target resolution
# ---------------------------------------------------------------------------

def _parse_target(table_name: str | None) -> dict:
    """
    Resolve --table_name into a target descriptor:
      {"mode": "all"}
      {"mode": "db",    "database": "hrdm_dev"}
      {"mode": "table", "database": "...", "schema": "...", "table": "..."}
    """
    if table_name is None:
        return {"mode": "all"}

    # Check if it matches a known DB name (case-insensitive)
    known_dbs = get_database_list()
    for db in known_dbs:
        if table_name.strip().lower() == db.lower():
            return {"mode": "db", "database": db}

    # Try bracket format: [db].[schema].[table]
    parts = re.findall(r'\[([^\]]+)\]', table_name)
    if len(parts) == 3:
        return {"mode": "table", "database": parts[0], "schema": parts[1], "table": parts[2]}

    raise ValueError(
        f"Cannot parse --table_name '{table_name}'. "
        "Use a DB name (e.g. HRDM_DEV) or bracket format [db].[schema].[table]."
    )


def _iter_tables(target: dict) -> list[tuple[str, str, str]]:
    """
    Yield (database, schema, table) tuples for the given target.
    """
    if target["mode"] == "all":
        rows = []
        for db in get_database_list():
            for schema, table in list_user_tables(db):
                rows.append((db, schema, table))
        return rows

    if target["mode"] == "db":
        db = target["database"]
        return [(db, schema, table) for schema, table in list_user_tables(db)]

    # single table
    return [(target["database"], target["schema"], target["table"])]


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------

def _run_profile(target: dict) -> None:
    sample_rows = int(os.getenv("TABLE_SAMPLE_ROWS", "50000"))

    try:
        tables = _iter_tables(target)
    except Exception as exc:
        print(f"[ERROR] Could not resolve target: {exc}")
        return

    current_db = None
    for database_name, schema_name, table_name in tables:
        if database_name != current_db:
            current_db = database_name
            print(f"\n=== PROFILING DATABASE: {database_name} ===")

        print(f"  Profiling [{database_name}].[{schema_name}].[{table_name}]")
        try:
            df = read_table_sample(database_name, schema_name, table_name, sample_rows)
            if df.empty:
                print("    -> skipped (empty sample)")
                continue

            table_metadata = get_table_column_metadata(database_name, schema_name, table_name)
            profiles       = profile_table(df, database_name, schema_name, table_name, table_metadata)
            save_profiles_to_db(profiles, database_name, schema_name, table_name)
            print(f"    -> {len(profiles)} columns profiled, saved to DB")
        except Exception as exc:
            print(f"    -> [ERROR] {exc}")


# ---------------------------------------------------------------------------
# Scan
# ---------------------------------------------------------------------------

def _run_scan(target: dict, job_id: int | None = None) -> None:
    sample_rows         = int(os.getenv("TABLE_SAMPLE_ROWS", "50000"))
    row_score_threshold = float(os.getenv("ROW_SCORE_THRESHOLD", "0.7"))

    try:
        tables = _iter_tables(target)
    except Exception as exc:
        print(f"[ERROR] Could not resolve target: {exc}")
        return

    current_db = None
    for database_name, schema_name, table_name in tables:
        if database_name != current_db:
            current_db = database_name
            print(f"\n=== SCANNING DATABASE: {database_name} ===")

        print(f"  Scanning [{database_name}].[{schema_name}].[{table_name}]")
        try:
            result = scan_table(
                database_name, schema_name, table_name,
                sample_rows, row_score_threshold,
            )
            run_id = save_scan_results(result, job_id=job_id)
            print(
                f"    -> {result['row_count_scanned']} rows scanned, "
                f"{result['flagged_row_count']} flagged "
                f"({result['flagged_rate']:.1%}) — run_id={run_id}"
            )
        except FileNotFoundError as exc:
            print(f"    -> [SKIPPED] No profile in DB: {exc}")
        except Exception as exc:
            print(f"    -> [ERROR] {exc}")

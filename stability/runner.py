from __future__ import annotations

import re

from validity.profiling.db import get_database_list, list_user_tables


def run(scan: str | None, table_name: str | None = None, job_id: int | None = None, **_) -> None:
    target = _parse_target(table_name)

    if scan == "row_count":
        _run_row_count(target, job_id=job_id)
    else:
        raise ValueError(
            f"Unknown --scan value '{scan}' for stability. Use 'row_count'."
        )


# ---------------------------------------------------------------------------
# Target resolution  (same convention as validity)
# ---------------------------------------------------------------------------

def _parse_target(table_name: str | None) -> dict:
    if table_name is None:
        return {"mode": "all"}

    known_dbs = get_database_list()
    for db in known_dbs:
        if table_name.strip().lower() == db.lower():
            return {"mode": "db", "database": db}

    parts = re.findall(r'\[([^\]]+)\]', table_name)
    if len(parts) == 3:
        return {"mode": "table", "database": parts[0], "schema": parts[1], "table": parts[2]}

    raise ValueError(
        f"Cannot parse --table_name '{table_name}'. "
        "Use a DB name (e.g. HRDM_DEV) or bracket format [db].[schema].[table]."
    )


def _iter_tables(target: dict) -> list[tuple[str, str, str]]:
    if target["mode"] == "all":
        rows = []
        for db in get_database_list():
            for schema, table in list_user_tables(db):
                rows.append((db, schema, table))
        return rows

    if target["mode"] == "db":
        db = target["database"]
        return [(db, schema, table) for schema, table in list_user_tables(db)]

    return [(target["database"], target["schema"], target["table"])]


# ---------------------------------------------------------------------------
# Row count stability
# ---------------------------------------------------------------------------

def _run_row_count(target: dict, job_id: int | None = None) -> None:
    from stability.row_count.scanner import check_table, save_run_result

    try:
        tables = _iter_tables(target)
    except Exception as exc:
        print(f"[ERROR] Could not resolve target: {exc}")
        return

    current_db = None
    for database_name, schema_name, table_name in tables:
        if database_name != current_db:
            current_db = database_name
            print(f"\n=== STABILITY ROW COUNT: {database_name} ===")

        print(f"  Checking [{database_name}].[{schema_name}].[{table_name}]")
        try:
            result     = check_table(database_name, schema_name, table_name)
            save_run_result(result, job_id=job_id)

            change_str = f"{result['change_pct']:+.1%}" if result["change_pct"] is not None else "first snapshot"
            z_str      = f"z={result['z_score']:.2f}"   if result["z_score"]    is not None else "building baseline"
            flag_str   = "  *** ANOMALY ***"             if result["anomaly"]                else ""
            print(f"    -> {result['row_count']:,} rows | change={change_str} | {z_str}{flag_str}")
        except Exception as exc:
            print(f"    -> [ERROR] {exc}")

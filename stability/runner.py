from __future__ import annotations

import os
import re
import traceback
from datetime import datetime, timezone


def run(scan: str | None, table_name: str | None = None, job_id: int | None = None, **_) -> None:
    target = _parse_target(table_name)

    if scan == "snapshot":
        _run_snapshot(target, job_id=job_id)
    elif scan == "check":
        _run_check(target, job_id=job_id)
    else:
        raise ValueError(
            f"Unknown --scan value '{scan}' for stability. Use 'snapshot' or 'check'."
        )


def _log(msg: str) -> None:
    line = f"[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line)
    log_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "runner.log"))
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(line + "\n")


# ---------------------------------------------------------------------------
# Target resolution
# ---------------------------------------------------------------------------

def _parse_target(table_name: str | None) -> dict:
    if table_name is None:
        return {"mode": "all"}

    parts = re.findall(r'\[([^\]]+)\]', table_name)
    if len(parts) == 3:
        return {"mode": "table", "database": parts[0], "schema": parts[1], "table": parts[2]}

    stripped = table_name.strip().strip("[]")
    return {"mode": "db", "database": stripped}


def _update_job_table_name(job_id: int | None, tables: list[tuple[str, str, str]]) -> None:
    if job_id is None:
        return
    from validity.profiling.db import get_engine
    from sqlalchemy import text
    metadata_db = os.getenv("METADATA_DATABASE", "MetadataRepository")
    engine      = get_engine(metadata_db)
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE dm_dq.scan_queue SET table_name = 'tables_in_target' WHERE job_id = :id"),
            {"id": job_id},
        )


def _load_targets(db_filter: str | None = None) -> list[tuple[str, str, str]]:
    """Load enabled rows from dm_dq.stability_targets, optionally filtered by db."""
    from validity.profiling.db import get_engine
    metadata_db = os.getenv("METADATA_DATABASE", "MetadataRepository")
    engine      = get_engine(metadata_db)

    from sqlalchemy import text
    query  = "SELECT db_name, schema_name, table_name FROM dm_dq.stability_targets WHERE enabled = 1"
    params: dict = {}
    if db_filter:
        query += " AND LOWER(db_name) = LOWER(:db_name)"
        params["db_name"] = db_filter
    query += " ORDER BY db_name, schema_name, table_name"

    with engine.connect() as conn:
        rows = conn.execute(text(query), params).fetchall()
    return [(r.db_name, r.schema_name, r.table_name) for r in rows]


def _iter_tables(target: dict) -> list[tuple[str, str, str]]:
    if target["mode"] == "table":
        return [(target["database"], target["schema"], target["table"])]
    if target["mode"] == "db":
        return _load_targets(db_filter=target["database"])
    return _load_targets()


# ---------------------------------------------------------------------------
# Snapshot
# ---------------------------------------------------------------------------

def _run_snapshot(target: dict, job_id: int | None = None) -> None:
    from stability.row_count.scanner import take_snapshot

    try:
        tables = _iter_tables(target)
    except Exception as exc:
        raise RuntimeError(f"Could not resolve target: {exc}") from exc

    _update_job_table_name(job_id, tables)

    errors = []
    current_db = None
    for database_name, schema_name, table_name in tables:
        if database_name != current_db:
            current_db = database_name
            _log(f"=== SNAPSHOT DATABASE: {database_name} ===")

        _log(f"Snapshotting [{database_name}].[{schema_name}].[{table_name}]")
        try:
            row_count = take_snapshot(database_name, schema_name, table_name)
            _log(f"  -> {row_count:,} rows")
        except Exception as exc:
            msg = f"[{database_name}].[{schema_name}].[{table_name}]: {exc}\n{traceback.format_exc()}"
            _log(f"  -> [ERROR] {msg}")
            errors.append(msg)

    if errors:
        raise RuntimeError("Snapshot errors:\n" + "\n".join(errors))


# ---------------------------------------------------------------------------
# Check
# ---------------------------------------------------------------------------

def _run_check(target: dict, job_id: int | None = None) -> None:
    from stability.row_count.scanner import check_table, save_run_result

    try:
        tables = _iter_tables(target)
    except Exception as exc:
        raise RuntimeError(f"Could not resolve target: {exc}") from exc

    _update_job_table_name(job_id, tables)

    errors = []
    current_db = None
    for database_name, schema_name, table_name in tables:
        if database_name != current_db:
            current_db = database_name
            _log(f"=== CHECK DATABASE: {database_name} ===")

        _log(f"Checking [{database_name}].[{schema_name}].[{table_name}]")
        try:
            result = check_table(database_name, schema_name, table_name)
            if result["row_count"] is None:
                _log(f"  -> [SKIPPED] No snapshots yet")
                continue
            save_run_result(result, job_id=job_id)
            change_str = f"{result['change_pct']:+.1%}" if result["change_pct"] is not None else "first snapshot"
            z_str      = f"z={result['z_score']:.2f}"   if result["z_score"]    is not None else "building baseline"
            flag_str   = "  *** ANOMALY ***"             if result["anomaly"]                else ""
            _log(f"  -> {result['row_count']:,} rows | change={change_str} | {z_str}{flag_str}")
        except Exception as exc:
            msg = f"[{database_name}].[{schema_name}].[{table_name}]: {exc}\n{traceback.format_exc()}"
            _log(f"  -> [ERROR] {msg}")
            errors.append(msg)

    if errors:
        raise RuntimeError("Check errors:\n" + "\n".join(errors))

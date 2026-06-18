from __future__ import annotations

import os
import re
import traceback
from datetime import datetime, timezone


def run(scan: str | None, table_name: str | None = None, job_id: int | None = None, **_) -> None:
    if scan == "cross_system":
        _run_cross_system(table_name, job_id=job_id)
    else:
        raise ValueError(
            f"Unknown --scan value '{scan}' for consistency. Use 'cross_system'."
        )


def _log(msg: str) -> None:
    line = f"[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line)
    log_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "runner.log"))
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def _parse_table_path(path: str) -> tuple[str, str, str]:
    parts = re.findall(r'\[([^\]]+)\]', path.strip())
    if len(parts) == 3:
        return parts[0], parts[1], parts[2]
    raise ValueError(
        f"Cannot parse table path '{path}'. Use [db].[schema].[table] format."
    )


def _run_cross_system(table_name: str | None, job_id: int | None = None) -> None:
    from consistency.cross_system.scanner import compare_tables, save_results

    if not table_name:
        raise ValueError("table_name is required. Format: [db].[schema].[table1]|[db].[schema].[table2]|join_key")

    parts = [p.strip() for p in table_name.split("|")]
    if len(parts) < 2:
        raise ValueError(
            f"table_name must be '[db].[schema].[table1]|[db].[schema].[table2]' or add '|join_key' for keyed comparison. Got: '{table_name}'"
        )

    source_db, source_schema, source_table = _parse_table_path(parts[0])
    target_db, target_schema, target_table = _parse_table_path(parts[1])
    join_key = parts[2] if len(parts) >= 3 else None

    key_info = f"on key '{join_key}'" if join_key else "keyless (full row hash)"
    _log(f"Cross-system consistency: [{source_db}].[{source_schema}].[{source_table}] vs [{target_db}].[{target_schema}].[{target_table}] — {key_info}")

    try:
        result = compare_tables(
            source_db, source_schema, source_table,
            target_db, target_schema, target_table,
            join_key,
        )
        run_id = save_results(result, job_id=job_id)
        _log(
            f"  -> source={result['source_count']:,} | target={result['target_count']:,} | "
            f"missing={result['missing_count']} | extra={result['extra_count']} | "
            f"modified={result['modified_count']} — run_id={run_id}"
        )
    except Exception as exc:
        msg = f"{exc}\n{traceback.format_exc()}"
        _log(f"  -> [ERROR] {msg}")
        raise

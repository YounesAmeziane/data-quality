from __future__ import annotations

import os

from validity.profiling.db import (
    get_database_list,
    get_table_column_metadata,
    list_user_tables,
    read_table_sample,
)
from validity.profiling.profiler import profile_table, save_profiles_to_json
from validity.scanning.table_scanner import save_scan_results, scan_table


def run(scan: str | None, **_) -> None:
    if scan == "profile":
        _run_profile()
    elif scan == "scan":
        _run_scan()
    else:
        raise ValueError(
            f"Unknown --scan value '{scan}' for validity. Use 'profile' or 'scan'."
        )


def _run_profile() -> None:
    sample_rows        = int(os.getenv("TABLE_SAMPLE_ROWS", "50000"))
    profile_output_dir = os.getenv("PROFILE_OUTPUT_DIR", "output/profiles")

    for database_name in get_database_list():
        print(f"\n=== PROFILING DATABASE: {database_name} ===")

        try:
            tables = list_user_tables(database_name)
        except Exception as exc:
            print(f"[ERROR] Could not list tables for {database_name}: {exc}")
            continue

        for schema_name, table_name in tables:
            print(f"  Profiling [{database_name}].[{schema_name}].[{table_name}]")
            try:
                df = read_table_sample(database_name, schema_name, table_name, sample_rows)
                if df.empty:
                    print("    -> skipped (empty sample)")
                    continue

                table_metadata = get_table_column_metadata(database_name, schema_name, table_name)
                profiles       = profile_table(df, database_name, schema_name, table_name, table_metadata)
                output_path    = save_profiles_to_json(
                    profiles, profile_output_dir, database_name, schema_name, table_name
                )
                print(f"    -> {len(profiles)} columns profiled, saved to {output_path}")
            except Exception as exc:
                print(f"    -> [ERROR] {exc}")


def _run_scan() -> None:
    sample_rows         = int(os.getenv("TABLE_SAMPLE_ROWS", "50000"))
    profile_output_dir  = os.getenv("PROFILE_OUTPUT_DIR", "output/profiles")
    anomaly_output_dir  = os.getenv("ANOMALY_OUTPUT_DIR", "output/anomalies")
    row_score_threshold = float(os.getenv("ROW_SCORE_THRESHOLD", "0.7"))

    for database_name in get_database_list():
        print(f"\n=== SCANNING DATABASE: {database_name} ===")

        try:
            tables = list_user_tables(database_name)
        except Exception as exc:
            print(f"[ERROR] Could not list tables for {database_name}: {exc}")
            continue

        for schema_name, table_name in tables:
            print(f"  Scanning [{database_name}].[{schema_name}].[{table_name}]")
            try:
                result      = scan_table(
                    database_name, schema_name, table_name,
                    profile_output_dir, sample_rows, row_score_threshold,
                )
                output_path = save_scan_results(result, anomaly_output_dir)
                print(
                    f"    -> {result['row_count_scanned']} rows scanned, "
                    f"{result['flagged_row_count']} flagged "
                    f"({result['flagged_rate']:.1%})"
                )
                print(f"    -> anomalies saved to {output_path}")
            except FileNotFoundError as exc:
                print(f"    -> [SKIPPED] Missing profile: {exc}")
            except Exception as exc:
                print(f"    -> [ERROR] {exc}")

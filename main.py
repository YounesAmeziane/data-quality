from __future__ import annotations

import argparse
import os

from dotenv import load_dotenv

from profiling.db import get_database_list, list_user_tables
from profiling.profiler import profile_table, save_profiles_to_json
from profiling.db import read_table_sample
from scanning.table_scanner import scan_table, save_scan_results


load_dotenv()


def run_profile_mode():
    sample_rows = int(os.getenv("TABLE_SAMPLE_ROWS", "50000"))
    profile_output_dir = os.getenv("PROFILE_OUTPUT_DIR", "output/profiles")

    databases = get_database_list()

    for database_name in databases:
        print(f"\n=== PROFILING DATABASE: {database_name} ===")

        try:
            tables = list_user_tables(database_name)
        except Exception as e:
            print(f"[ERROR] Failed to list tables for {database_name}: {e}")
            continue

        for schema_name, table_name in tables:
            print(f"Profiling [{database_name}].[{schema_name}].[{table_name}]")

            try:
                df = read_table_sample(
                    database=database_name,
                    schema_name=schema_name,
                    table_name=table_name,
                    sample_rows=sample_rows,
                )

                if df.empty:
                    print("  -> skipped (empty sample)")
                    continue

                profiles = profile_table(
                    df=df,
                    database_name=database_name,
                    schema_name=schema_name,
                    table_name=table_name,
                )

                output_path = save_profiles_to_json(
                    profiles=profiles,
                    output_dir=profile_output_dir,
                    database_name=database_name,
                    schema_name=schema_name,
                    table_name=table_name,
                )

                print(f"  -> saved profile to {output_path}")

            except Exception as e:
                print(f"  -> [ERROR] {e}")


def run_scan_mode():
    sample_rows = int(os.getenv("TABLE_SAMPLE_ROWS", "50000"))
    profile_output_dir = os.getenv("PROFILE_OUTPUT_DIR", "output/profiles")
    anomaly_output_dir = os.getenv("ANOMALY_OUTPUT_DIR", "output/anomalies")
    row_score_threshold = float(os.getenv("ROW_SCORE_THRESHOLD", "0.7"))

    databases = get_database_list()

    for database_name in databases:
        print(f"\n=== SCANNING DATABASE: {database_name} ===")

        try:
            tables = list_user_tables(database_name)
        except Exception as e:
            print(f"[ERROR] Failed to list tables for {database_name}: {e}")
            continue

        for schema_name, table_name in tables:
            print(f"Scanning [{database_name}].[{schema_name}].[{table_name}]")

            try:
                result = scan_table(
                    database_name=database_name,
                    schema_name=schema_name,
                    table_name=table_name,
                    profile_dir=profile_output_dir,
                    sample_rows=sample_rows,
                    row_score_threshold=row_score_threshold,
                )

                output_path = save_scan_results(
                    scan_result=result,
                    output_dir=anomaly_output_dir,
                )

                print(
                    f"  -> scanned {result['row_count_scanned']} rows, "
                    f"flagged {result['flagged_row_count']} rows"
                )
                print(f"  -> saved anomalies to {output_path}")

            except FileNotFoundError as e:
                print(f"  -> [SKIPPED] Missing profile: {e}")
            except Exception as e:
                print(f"  -> [ERROR] {e}")


def main():
    parser = argparse.ArgumentParser(description="Data Quality Profiler and Scanner")
    parser.add_argument(
        "--mode",
        required=True,
        choices=["profile", "scan"],
        help="Run profiler or anomaly scanner",
    )

    args = parser.parse_args()

    if args.mode == "profile":
        run_profile_mode()
    elif args.mode == "scan":
        run_scan_mode()


if __name__ == "__main__":
    main()
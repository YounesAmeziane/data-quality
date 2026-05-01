from __future__ import annotations

import argparse

from dotenv import load_dotenv

load_dotenv()


def main() -> None:
    parser = argparse.ArgumentParser(description="Data Quality Platform")
    parser.add_argument(
        "--scan_type",
        required=True,
        choices=["validity", "consistency", "stability"],
        help="Module to run.",
    )
    parser.add_argument(
        "--scan",
        required=False,
        help="Operation within the module (e.g. 'profile' or 'scan' for validity).",
    )
    parser.add_argument(
        "--table_name",
        required=False,
        help=(
            "Target to scan. Pass a DB name (e.g. HRDM_DEV) to scan the entire DB, "
            "or a bracketed reference (e.g. [DB].[schema].[table]) for a single table. "
            "Omit to scan all databases in DB_DATABASES."
        ),
    )
    args = parser.parse_args()

    if args.scan_type == "validity":
        from validity.runner import run
        run(scan=args.scan, table_name=args.table_name)
    elif args.scan_type == "consistency":
        raise NotImplementedError("Consistency module not yet implemented.")
    elif args.scan_type == "stability":
        raise NotImplementedError("Stability module not yet implemented.")


if __name__ == "__main__":
    main()

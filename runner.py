from __future__ import annotations

import os
import time
import traceback
from datetime import datetime, timezone

from dotenv import load_dotenv
from sqlalchemy import text

load_dotenv()

_JOB_DB    = os.getenv("JOB_QUEUE_DATABASE", "hrdm_dev")
_JOB_TABLE = "dbo.ai_scan"
_POLL_INTERVAL = 1  # seconds


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _engine():
    from validity.profiling.db import get_engine
    return get_engine(_JOB_DB)


def _claim_job() -> tuple | None:
    """
    Atomically grab one pending job and mark it processing.
    Uses UPDATE ... OUTPUT so two runners can never claim the same row.
    Returns (job_id, scan_type, scan, table_name) or None if queue is empty.
    """
    with _engine().begin() as conn:
        row = conn.execute(
            text(f"""
                UPDATE TOP (1) {_JOB_TABLE}
                SET    status     = 'processing',
                       started_at = :now
                OUTPUT INSERTED.job_id,
                       INSERTED.scan_type,
                       INSERTED.scan,
                       INSERTED.table_name
                WHERE  status = 'pending'
            """),
            {"now": datetime.now(timezone.utc)},
        ).fetchone()
    return row


def _mark_done(job_id: int) -> None:
    with _engine().begin() as conn:
        conn.execute(
            text(f"UPDATE {_JOB_TABLE} SET status = 'done', finished_at = :now WHERE job_id = :id"),
            {"now": datetime.now(timezone.utc), "id": job_id},
        )


def _mark_failed(job_id: int) -> None:
    with _engine().begin() as conn:
        conn.execute(
            text(f"UPDATE {_JOB_TABLE} SET status = 'failed', finished_at = :now WHERE job_id = :id"),
            {"now": datetime.now(timezone.utc), "id": job_id},
        )


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

def _dispatch(scan_type: str, scan: str, table_name: str | None) -> None:
    if scan_type == "validity":
        from validity.runner import run
        run(scan=scan, table_name=table_name)
    elif scan_type == "consistency":
        raise NotImplementedError("Consistency module not yet implemented.")
    elif scan_type == "stability":
        raise NotImplementedError("Stability module not yet implemented.")
    else:
        raise ValueError(f"Unknown scan_type: '{scan_type}'")


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main() -> None:
    print(f"Runner started — polling {_JOB_DB}.{_JOB_TABLE} every {_POLL_INTERVAL}s")

    while True:
        job = _claim_job()

        if job is None:
            time.sleep(_POLL_INTERVAL)
            continue

        job_id, scan_type, scan, table_name = job
        print(f"\n[JOB {job_id}] scan_type={scan_type} scan={scan} table_name={table_name}")

        try:
            _dispatch(scan_type, scan, table_name or None)
            _mark_done(job_id)
            print(f"[JOB {job_id}] done")
        except Exception as exc:
            _mark_failed(job_id)
            print(f"[JOB {job_id}] failed: {exc}")
            traceback.print_exc()


if __name__ == "__main__":
    main()

from __future__ import annotations

import os
from typing import Any

from dotenv import load_dotenv


load_dotenv()


def _get_env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name, str(default)).strip().lower()
    return raw in {"1", "true", "yes", "y", "on"}


def _get_env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return float(raw)


def _get_env_list(name: str) -> list[str]:
    raw = os.getenv(name, "")
    return [x.strip().lower() for x in raw.split(",") if x.strip()]


SKIP_COLUMN_HINTS = _get_env_list("SKIP_COLUMN_HINTS")
FORCE_INCLUDE_COLUMNS = set(_get_env_list("FORCE_INCLUDE_COLUMNS"))
FORCE_EXCLUDE_COLUMNS = set(_get_env_list("FORCE_EXCLUDE_COLUMNS"))

SKIP_IDENTIFIERS = _get_env_bool("SKIP_IDENTIFIERS", True)
SKIP_FREE_TEXT = _get_env_bool("SKIP_FREE_TEXT", True)
SKIP_HIGH_CARDINALITY = _get_env_bool("SKIP_HIGH_CARDINALITY", True)
SKIP_HIGH_NULL_COLUMNS = _get_env_bool("SKIP_HIGH_NULL_COLUMNS", True)

HIGH_CARDINALITY_THRESHOLD = _get_env_float("HIGH_CARDINALITY_THRESHOLD", 0.98)
HIGH_NULL_THRESHOLD = _get_env_float("HIGH_NULL_THRESHOLD", 0.98)


def should_scan_column(column_name: str, profile: dict[str, Any] | None) -> tuple[bool, str]:
    """
    Decide whether a column should be used for row-level anomaly scoring.

    Returns:
        (should_scan: bool, reason: str)
    """
    col = column_name.lower().strip()

    # 1. explicit overrides
    if col in FORCE_EXCLUDE_COLUMNS:
        return False, "force_exclude"

    if col in FORCE_INCLUDE_COLUMNS:
        return True, "force_include"

    # 2. obvious noisy/system columns by name
    if any(hint in col for hint in SKIP_COLUMN_HINTS):
        return False, "name_hint_skip"

    # 3. missing profile
    if not profile:
        return False, "missing_profile"

    logical_type = str(profile.get("logical_type", "")).strip().lower()
    distinct_ratio = float(profile.get("distinct_ratio", 0.0) or 0.0)
    null_rate = float(profile.get("null_rate", 0.0) or 0.0)

    # 4. logical-type based exclusions
    if SKIP_IDENTIFIERS and logical_type == "identifier":
        return False, "identifier"

    if SKIP_FREE_TEXT and logical_type == "free_text":
        return False, "free_text"

    # 5. statistical exclusions
    if SKIP_HIGH_CARDINALITY and distinct_ratio >= HIGH_CARDINALITY_THRESHOLD:
        return False, "high_cardinality"

    if SKIP_HIGH_NULL_COLUMNS and null_rate >= HIGH_NULL_THRESHOLD:
        return False, "high_null_rate"

    return True, "eligible"
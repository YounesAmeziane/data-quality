from __future__ import annotations

import pandas as pd

from validity.scoring.null_handler import score_null
from validity.scoring.utils import clamp


def score_datetime(value, profile: dict) -> dict:
    null_result = score_null(value, profile)
    if null_result is not None:
        return null_result

    reasons:  list[str] = []
    features: dict      = {}
    score               = 0.0

    try:
        parsed = pd.to_datetime(value, errors="raise", format="mixed")
        if pd.isna(parsed):
            raise ValueError("NaT")
    except Exception:
        return {"score": 0.3, "reasons": ["parse_fail"], "features": {}}

    # Normalise to timezone-naive for consistent comparison
    if parsed.tzinfo is not None:
        parsed = parsed.replace(tzinfo=None)

    now         = pd.Timestamp.now(tz=None)
    future_rate = float(profile.get("future_rate", 1.0))
    min_date_str = profile.get("min_date")
    max_date_str = profile.get("max_date")

    features["parsed"] = str(parsed)
    is_future = parsed > now
    features["is_future"] = is_future

    if is_future:
        if future_rate < 0.01:
            score += 0.5
            reasons.append("unexpected_future")
        elif future_rate < 0.05:
            score += 0.2
            reasons.append("rare_future")
    elif min_date_str and max_date_str:
        try:
            min_dt = pd.Timestamp(min_date_str)
            max_dt = pd.Timestamp(max_date_str)

            if min_dt.tz is not None:
                min_dt = min_dt.tz_localize(None)
            if max_dt.tz is not None:
                max_dt = max_dt.tz_localize(None)

            range_days = max((max_dt - min_dt).days, 1)

            if parsed < min_dt:
                undershoot = (min_dt - parsed).days
                features["undershoot_days"] = undershoot
                if undershoot / range_days > 0.5:
                    score += 0.35
                    reasons.append("far_before_historical_min")
                else:
                    score += 0.1
                    reasons.append("before_historical_min")
            elif parsed > max_dt:
                overshoot = (parsed - max_dt).days
                features["overshoot_days"] = overshoot
                if overshoot / range_days > 0.5:
                    score += 0.2
                    reasons.append("far_after_historical_max")
                else:
                    score += 0.1
                    reasons.append("after_historical_max")
        except Exception:
            pass

    return {"score": clamp(score), "reasons": reasons, "features": features}

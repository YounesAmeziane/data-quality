from __future__ import annotations

from validity.scoring.boolean_scorer import score_boolean
from validity.scoring.categorical_scorer import score_categorical
from validity.scoring.datetime_scorer import score_datetime
from validity.scoring.null_handler import score_null
from validity.scoring.numeric_scorer import score_numeric
from validity.scoring.text_scorer import score_text

_TEXT_TYPES = {"structured_text", "identifier", "free_text"}


def score_value(value, column_profile: dict) -> dict:
    logical_type = column_profile.get("logical_type", "unknown")

    if logical_type == "numeric":
        return score_numeric(value, column_profile)
    if logical_type == "categorical":
        return score_categorical(value, column_profile)
    if logical_type in _TEXT_TYPES:
        return score_text(value, column_profile)
    if logical_type == "datetime":
        return score_datetime(value, column_profile)
    if logical_type == "boolean":
        return score_boolean(value, column_profile)

    null_result = score_null(value, column_profile)
    if null_result is not None:
        return null_result
    return {"score": 0.0, "reasons": [], "features": {}}

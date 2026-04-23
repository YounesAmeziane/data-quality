# scoring/scorer.py

from scoring.numeric_scorer import score_numeric
from scoring.categorical_scorer import score_categorical
from scoring.text_scorer import score_text


def score_value(value, column_profile):
    logical_type = column_profile["logical_type"]

    if logical_type == "numeric":
        return score_numeric(value, column_profile)

    elif logical_type == "categorical":
        return score_categorical(value, column_profile)

    elif logical_type in ["structured_text", "identifier", "free_text"]:
        return score_text(value, column_profile)

    else:
        return {"score": 0.0, "reasons": []}
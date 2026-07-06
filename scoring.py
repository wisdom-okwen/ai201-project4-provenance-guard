from config import (
    LLM_WEIGHT,
    STYLO_WEIGHT,
    DISAGREEMENT_THRESHOLD,
    DISAGREEMENT_PENALTY,
    AI_THRESHOLD,
    HUMAN_THRESHOLD,
)

LABELS = {
    "likely_ai": (
        "This content shows strong signs of AI generation — our analysis estimates a {pct}% "
        "likelihood of AI authorship, based on consistent agreement between language-pattern "
        "and writing-style analysis."
    ),
    "likely_human": (
        "This content shows strong signs of human authorship — our analysis estimates only a "
        "{pct}% likelihood of AI involvement, based on consistent agreement between "
        "language-pattern and writing-style analysis."
    ),
    "uncertain": (
        "We could not confidently determine the origin of this content — our analysis "
        "estimates a {pct}% likelihood of AI involvement, but the language-pattern and "
        "writing-style signals did not agree strongly enough to reach a confident conclusion."
    ),
}


def combine(llm_score, stylo_score):
    """Weighted blend of the two signals, penalized when they disagree strongly."""
    raw = LLM_WEIGHT * llm_score + STYLO_WEIGHT * stylo_score
    disagreement = abs(llm_score - stylo_score)
    penalty = DISAGREEMENT_PENALTY if disagreement > DISAGREEMENT_THRESHOLD else 0.0
    return max(0.0, raw - penalty)


def classify(confidence):
    """Maps a confidence score to an attribution bucket and its transparency label text."""
    if confidence >= AI_THRESHOLD:
        attribution = "likely_ai"
    elif confidence < HUMAN_THRESHOLD:
        attribution = "likely_human"
    else:
        attribution = "uncertain"

    pct = round(confidence * 100)
    label = LABELS[attribution].format(pct=pct)
    return attribution, label

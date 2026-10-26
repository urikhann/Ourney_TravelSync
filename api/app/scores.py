"""Score scales. The team recommender scores places 1–5 (it says so in "score_scale");
the old mock used 0–1. The web app always gets a 0–1 "fit" so the screens never need to know."""


def to_fit(score: float | None, scale: str | None) -> float | None:
    """1–5 -> 0–1 (1 = 0 %, 5 = 100 %). 0–1 stays as it is. None stays None."""
    if score is None:
        return None
    if scale == "1-5":
        return round(max(0.0, min(1.0, (score - 1) / 4)), 4)
    return score

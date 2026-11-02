"""Keep TF-IDF cosine on 0-1; convert historical 1-5 runs for the web display."""


def to_fit(score: float | None, scale: str | None) -> float | None:
    """1–5 -> 0–1 (1 = 0 %, 5 = 100 %). 0–1 stays as it is. None stays None."""
    if score is None:
        return None
    if scale == "1-5":
        return round(max(0.0, min(1.0, (score - 1) / 4)), 4)
    return score

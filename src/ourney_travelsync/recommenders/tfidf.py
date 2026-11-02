"""Option C: unweighted metadata + description character TF-IDF cosine."""

import re
import unicodedata

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

FIELDS = ("detailed_category", "food_type", "representative_menu", "activity_type",
          "description_summary")
VECTORIZER_CONFIG = dict(analyzer="char", ngram_range=(2, 3), smooth_idf=True, norm="l2")
MISSING = {"", "NULL", "N/A", "NA", "UNKNOWN", "NOT_APPLICABLE"}


def clean(value):
    """Use the experiment's text cleaning; absent metadata contributes no text."""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return ""
    if not isinstance(value, str):
        raise ValueError("TF-IDF metadata and queries must be text or missing.")
    if value.strip().upper() in MISSING:
        return ""
    value = unicodedata.normalize("NFKC", value).lower()
    return " ".join(re.sub(r"[^가-힣a-z0-9\s]", " ", value).split())


def document(row):
    """Strict five-field allowlist, in experimental field order, no aliases."""
    return " ".join(part for field in FIELDS if (part := clean(row.get(field))))


def score_tfidf(queries, records):
    """Return [members, eligible POIs] raw cosine scores in input order.

    Callers must filter eligibility before passing records. Only those documents
    enter vocabulary/IDF fitting; queries are transformed using that fitted model.
    Empty/OOV queries, empty documents and empty vocabularies score zero.
    """
    texts = [document(row) for row in records]
    queries = [clean(query) for query in queries]
    zeros = np.zeros((len(queries), len(texts)), dtype=float)
    if not texts or not queries:
        return zeros
    vectorizer = TfidfVectorizer(**VECTORIZER_CONFIG)
    if not any(vectorizer.build_analyzer()(text) for text in texts):
        return zeros
    pois = vectorizer.fit_transform(texts)
    users = vectorizer.transform(queries)
    return np.clip(cosine_similarity(users, pois), 0.0, 1.0)

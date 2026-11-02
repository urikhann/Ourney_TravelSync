"""Method C: explicit preference text against enriched POI text, cosine [0, 1]."""
from collections.abc import Mapping

import numpy as np

TEXT_FIELDS = ('detailed_category', 'food_type', 'representative_menu', 'activity_type', 'description_summary')


def taste_query(tastes):
    """Use the upstream optional survey's explicit positive keywords.

    Avoid keywords are separate from likes and never become positive query text.
    The backend already resolves the survey's hard exclusion tags.
    """
    if tastes is None:
        return ''
    if not isinstance(tastes, Mapping):
        raise ValueError('TASTES_MUST_BE_AN_OBJECT')
    for key in ('keywords', 'avoid_keywords'):
        values = tastes.get(key, [])
        if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
            raise ValueError('TASTE_KEYWORDS_MUST_BE_STRING_LISTS')
    return ' '.join(tastes.get('keywords', []))


def document(row):
    metadata = row.get('enriched_metadata')
    source = metadata if isinstance(metadata, Mapping) else row
    return ' '.join(source[k].strip() for k in TEXT_FIELDS
                    if isinstance(source.get(k), str) and source[k].strip()
                    and source[k].strip().lower() not in ('null', 'nan', 'none', r'\n'))


def score_texts(records, queries):
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity

    records, queries = list(records), list(queries)
    if any(not isinstance(q, str) for q in queries):
        raise ValueError('PREFERENCE_TEXT_REQUIRED_STRING')
    scores = np.zeros((len(queries), len(records)), dtype=float)
    documents = [document(row) for row in records]
    if not queries or not any(len(d) >= 2 for d in documents):
        return scores
    vectorizer = TfidfVectorizer(analyzer='char', ngram_range=(2, 3), smooth_idf=True, norm='l2')
    matrix = vectorizer.fit_transform(documents)
    return np.clip(cosine_similarity(vectorizer.transform(queries), matrix), 0, 1)

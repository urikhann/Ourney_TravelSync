"""Isolated scoring contracts. No database writes or production wiring."""
from pathlib import Path
import sys
import hashlib
import json
import numpy as np
from scipy.stats import rankdata

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from ourney_travelsync.features import FEATURE_NAMES
from ourney_travelsync.common import GroupInput
from ourney_travelsync.recommenders.gfar import GFAR

LABELS = ('음식 식사', '카페 커피', '자연 경관', '역사 문화유산', '미술 예술',
          '쇼핑', '체험 활동', '오락 놀이', '휴식', '축제 행사')

def clean(value):
    s = str(value or '').strip()
    return '' if s.lower() in ('null', 'none', 'nan', r'\n') else s

def structured(users, features, penalty=0):
    u, x = np.asarray(users, float), np.asarray(features, float)
    if u.ndim != 2 or x.ndim != 2 or u.shape[1] != 10 or x.shape[1] != 10:
        raise ValueError('Expected two ten-column matrices')
    if not np.isfinite(u).all() or np.any((u < 1) | (u > 5)) or not np.isin(x, [0, 1]).all():
        raise ValueError('Invalid survey or binary features')
    if not np.isfinite(penalty) or penalty < 0:
        raise ValueError('Invalid dislike penalty')
    count = x.sum(1)
    score = np.full((len(u), len(x)), np.nan)
    valid = count > 0
    score[:, valid] = u @ x[valid].T / count[valid]
    for i in np.flatnonzero(valid):
        score[:, i] -= penalty * np.maximum(0, 3 - u[:, x[i] == 1].min(1))
    return np.clip(score, 1, 5), ~valid

def document(row, representation):
    text = ' '.join(filter(None, [clean(row.get('place_name')), clean(row.get('category_name'))]))
    if representation == 'features':
        text += ' ' + ' '.join(LABELS[k] for k, f in enumerate(FEATURE_NAMES) if row[f] == '1')
    elif representation == 'description':
        # Stored, source-backed text only; CONFLICT records are excluded from this arm.
        if clean(row.get('description_source_url')) and row.get('detail_verification_status') != 'CONFLICT':
            text += ' ' + clean(row.get('description_summary'))
    elif representation != 'name_category':
        raise ValueError('Unknown representation')
    return text.strip()

def semantic(users, poi_embeddings, interests, beta=1, strategy='weighted'):
    """Return uncalibrated relevance; neutral ratings contribute no direction.

    Weighted strategy sums each interest cosine with rating-distance weights.
    Centroid strategy compares against separately normalized interest centroids.
    Neither asks an embedding model to understand negation.
    """
    u = np.asarray(users, float)
    positive, negative = np.maximum(u - 3, 0), np.maximum(3 - u, 0)
    def component(weights):
        if strategy == 'weighted':
            return (weights @ (interests @ poi_embeddings.T)) / np.maximum(weights.sum(1, keepdims=True), 1)
        if strategy != 'centroid':
            raise ValueError('Unknown profile strategy')
        centroid = weights @ interests
        centroid /= np.maximum(np.linalg.norm(centroid, axis=1, keepdims=True), 1e-12)
        return centroid @ poi_embeddings.T
    return component(positive) - beta * component(negative)

def normalize(scores, mode='percentile'):
    a = np.asarray(scores, float)
    if not np.isfinite(a).all():
        raise ValueError('Normalize classified finite scores only')
    if not a.shape[1]:
        return a.copy()
    if mode == 'percentile':
        if a.shape[1] == 1:
            return np.full_like(a, .5)
        return (rankdata(a, axis=1, method='average') - 1) / (a.shape[1] - 1)
    if mode != 'minmax':
        raise ValueError('Unknown normalization')
    lo, hi = a.min(1, keepdims=True), a.max(1, keepdims=True)
    return np.divide(a-lo, hi-lo, out=np.full_like(a, .5), where=hi>lo)

def hybrid(feature, text, alpha, mode='percentile'):
    if not 0 <= alpha <= 1:
        raise ValueError('alpha outside [0,1]')
    return (1-alpha)*normalize(feature, mode) + alpha*normalize(text, mode)

def aggregate(users, features, ids, scores, method, *, scale, excluded=(), eligible=None, k=10, threshold=2.5):
    """Enforce hard IDs and missing-data exclusions before every aggregator."""
    if isinstance(k,bool) or not isinstance(k,int) or k<0:
        raise ValueError('k must be a nonnegative integer')
    if scores.shape != (len(users),len(ids)) or len(features)!=len(ids) or len(set(ids))!=len(ids):
        raise ValueError('Misaligned scores or duplicate IDs')
    if scale == 'survey_feature_1_5' and np.any(np.isfinite(scores) & ((scores<1)|(scores>5))):
        raise ValueError('Survey-feature scores must be in [1,5]')
    keep = np.array([pid not in excluded and (eligible is None or pid in eligible)
                     for pid in ids]) & np.isfinite(scores).all(0) & (features.sum(1) > 0)
    before = int(keep.sum())
    if method == 'awm':
        if scale != 'survey_feature_1_5':
            raise ValueError('AWM requires an explicit survey-feature scale; cosine/ranks are not ratings')
        if not np.isfinite(threshold) or not 1<=threshold<=5:
            raise ValueError('AWM threshold must be in [1,5]')
        keep &= (scores >= threshold).all(0)
    indices = np.flatnonzero(keep)
    if not len(indices):
        return [], {'before_awm': before, 'surviving': 0, 'reason': 'awm_threshold' if before else 'hard_constraints_or_unclassified'}
    if method == 'gfar':
        data = GroupInput(tuple(map(str, range(len(users)))), tuple(ids[i] for i in indices),
                          users, features[indices], scores[:, indices])
        result = GFAR(20).recommend(data, top_k=k)
        lookup = {ids[i]: i for i in indices}
        selected = [lookup[r.poi_id] for r in result]
    elif method in ('average', 'awm'):
        selected = indices[np.argsort(-scores[:, indices].mean(0), kind='stable')[:k]].tolist()
    else:
        raise ValueError('Unknown aggregator')
    return selected, {'before_awm': before, 'surviving': len(indices), 'reason': 'short_pool' if len(selected)<k else ''}

def encode(model, texts, cache, prefix=''):
    payload = json.dumps([getattr(model, '_experiment_identity'), prefix, texts], ensure_ascii=False)
    key = hashlib.sha256(payload.encode()).hexdigest()
    cache.mkdir(parents=True, exist_ok=True)
    path = cache / (key + '.npy')
    if path.exists():
        return np.load(path), True
    result = model.encode([prefix+t for t in texts], batch_size=32, normalize_embeddings=True,
                          convert_to_numpy=True, show_progress_bar=False)
    np.save(path, result)
    return result, False

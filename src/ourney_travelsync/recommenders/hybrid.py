"""Versioned E5 relevance, ported from the 2026-11-05 study (29018ce).

No human labels or private profiles enter the shared embedding cache. Transformer
imports and CPU model initialization happen only on a cache miss.
"""
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import zipfile

import numpy as np
from scipy.stats import rankdata

from .individual_scoring import score_individuals

MODEL_ID = 'intfloat/multilingual-e5-small'
MODEL_REVISION = '614241f622f53c4eeff9890bdc4f31cfecc418b3'
TEXT_VERSION = 'study-description-v1'
SCORING_VERSION = 'e5-weighted-percentile-v1'
LABELS = ('음식 식사', '카페 커피', '자연 경관', '역사 문화유산', '미술 예술',
          '쇼핑', '체험 활동', '오락 놀이', '휴식', '축제 행사')


@dataclass(frozen=True)
class ScoringConfig:
    alpha: float = .5
    beta: float = 1.
    cache_dir: str = 'output/e5-cache'
    model_cache_dir: str | None = None
    local_files_only: bool = True
    regenerate: bool = False
    lock_timeout: float = 10.

    def __post_init__(self):
        if isinstance(self.alpha, bool) or not np.isfinite(self.alpha) or not 0 <= self.alpha <= 1:
            raise ValueError('HYBRID_ALPHA_MUST_BE_IN_0_1')
        if isinstance(self.beta, bool) or not np.isfinite(self.beta) or self.beta < 0:
            raise ValueError('SEMANTIC_BETA_MUST_BE_NONNEGATIVE')
        if not np.isfinite(self.lock_timeout) or self.lock_timeout < 0:
            raise ValueError('INVALID_CACHE_LOCK_TIMEOUT')
        if not self.cache_dir:
            raise ValueError('CACHE_DIRECTORY_REQUIRED')

    @classmethod
    def from_env(cls, **overrides):
        return cls(**(dict(alpha=float(os.getenv('TRAVELSYNC_HYBRID_ALPHA', '.5')),
                          beta=float(os.getenv('TRAVELSYNC_SEMANTIC_BETA', '1')),
                          cache_dir=os.getenv('TRAVELSYNC_E5_CACHE', 'output/e5-cache'),
                          model_cache_dir=os.getenv('TRAVELSYNC_E5_MODEL_CACHE') or None,
                          local_files_only=True,
                          regenerate=os.getenv('TRAVELSYNC_E5_REGENERATE', '0') == '1') | overrides))


def clean(value):
    text = str(value or '').strip()
    return '' if text.lower() in ('null', 'none', 'nan', r'\n') else text


def document(row):
    """Exact evaluated text; nested DB metadata is exposed through the same keys."""
    row = {**(row.get('enriched_metadata') or {}), **row}
    text = ' '.join(filter(None, [clean(row.get('place_name')), clean(row.get('category_name'))]))
    backed = bool(clean(row.get('description_source_url'))
                  and row.get('detail_verification_status') != 'CONFLICT'
                  and clean(row.get('description_summary')))
    if clean(row.get('description_source_url')) and row.get('detail_verification_status') != 'CONFLICT':
        text += ' ' + clean(row.get('description_summary'))
    return text.strip(), backed


def normalize(scores):
    """Ascending average ranks: larger relevance maps to larger percentiles."""
    a = np.asarray(scores, float)
    if a.ndim != 2 or not np.isfinite(a).all():
        raise ValueError('NORMALIZE_FINITE_MATRIX_ONLY')
    if a.shape[1] == 0:
        return a.copy()
    if a.shape[1] == 1:
        return np.full_like(a, .5)
    return (rankdata(a, axis=1, method='average') - 1) / (a.shape[1] - 1)


def semantic(users, poi_embeddings, interests, beta=1.):
    """Weighted cosine signals, with the study's max(sum(weights), 1) divisor."""
    similarities = interests @ poi_embeddings.T
    def component(weights):
        return (weights @ similarities) / np.maximum(weights.sum(1, keepdims=True), 1)
    return component(np.maximum(users - 3, 0)) - beta * component(np.maximum(3 - users, 0))


class SemanticUnavailable(RuntimeError):
    """An operational semantic failure, safe to expose as a fixed reason code."""


@dataclass(frozen=True)
class ScoreResult:
    scores: np.ndarray
    feature_raw: np.ndarray
    semantic_raw: np.ndarray
    feature_normalized: np.ndarray
    semantic_normalized: np.ndarray
    hybrid: np.ndarray
    diagnostics: dict
    description_backed: tuple[bool, ...]

    def subset(self, indices):
        return replace(self, **{name: getattr(self, name)[:, indices] for name in
                               ('scores', 'feature_raw', 'semantic_raw', 'feature_normalized',
                                'semantic_normalized', 'hybrid')},
                       description_backed=tuple(self.description_backed[i] for i in indices))

    def components(self, user, poi):
        def finite(a):
            value = float(a[user, poi])
            return value if np.isfinite(value) else None
        member = self.diagnostics['members'][user]
        return dict(feature_score_raw=finite(self.feature_raw), semantic_score_raw=finite(self.semantic_raw),
                    feature_score_normalized=finite(self.feature_normalized),
                    semantic_score_normalized=finite(self.semantic_normalized), hybrid_score=finite(self.hybrid),
                    scoring_method=member['effective_scoring_method'], scoring_version=self.diagnostics['scoring_version'])


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':'), default=str).encode()).hexdigest()


def catalogue_identity(places):
    if len(places.records) != len(places.place_ids) or len(set(places.place_ids)) != len(places.place_ids):
        raise ValueError('INVALID_CATALOGUE_ALIGNMENT')
    order = sorted(range(len(places.place_ids)), key=lambda i: places.place_ids[i])
    ids = tuple(places.place_ids[i] for i in order)
    if any(r.get('place_id') != pid for r, pid in zip(places.records, places.place_ids)):
        raise ValueError('INVALID_CATALOGUE_ALIGNMENT')
    docs = [document(places.records[i]) for i in order]
    texts = tuple(text for text, _ in docs)
    # Canonical scoring inputs let CSV prewarming and the API's equivalent JOIN
    # reuse a cache. Unused CSV columns and JSON null spellings are not identity.
    source = [{**(places.records[i].get('enriched_metadata') or {}), **places.records[i]} for i in order]
    metadata = dict(model_id=MODEL_ID, model_revision=MODEL_REVISION, text_version=TEXT_VERSION,
                    dataset_content_hash=_digest([[places.place_ids[i], places.features[i].tolist(),
                                                   places.coordinates[i].tolist(), docs[n][0], docs[n][1],
                                                   clean(source[n].get('description_source_url')),
                                                   clean(source[n].get('detail_verification_status'))]
                                                  for n, i in enumerate(order)]),
                    text_fingerprint=_digest([ids, texts, LABELS, 'query: ', 'passage: ']),
                    dimensions=384, poi_ids=list(ids))
    return ids, texts, metadata


def _validate_embeddings(pois, interests, count):
    for matrix, rows in ((pois, count), (interests, 10)):
        if matrix.shape != (rows, 384) or not np.isfinite(matrix).all():
            raise SemanticUnavailable('semantic_invalid_model_output')
        if not np.allclose(np.linalg.norm(matrix, axis=1), 1, atol=1e-4):
            raise SemanticUnavailable('semantic_invalid_model_output')


def _read_cache(path, expected):
    try:
        with np.load(path, allow_pickle=False) as f:
            meta = json.loads(str(f['metadata'].item()))
            if any(meta.get(k) != v for k, v in expected.items()):
                return None
            pois, interests = f['poi_embeddings'], f['interest_embeddings']
            _validate_embeddings(pois, interests, len(expected['poi_ids']))
            # Content corruption must not produce finite but incorrect scores.
            if meta.get('embedding_hash') != hashlib.sha256(pois.tobytes() + interests.tobytes()).hexdigest():
                return None
            return pois, interests
    except (OSError, ValueError, KeyError, EOFError, zipfile.BadZipFile, SemanticUnavailable):
        return None


def _encode(texts, config):
    try:
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer(MODEL_ID, revision=MODEL_REVISION, device='cpu',
                                    cache_folder=config.model_cache_dir, local_files_only=config.local_files_only)
        kwargs = dict(batch_size=32, normalize_embeddings=True, convert_to_numpy=True, show_progress_bar=False)
        interests = model.encode(['query: ' + label for label in LABELS], **kwargs)
        pois = model.encode(['passage: ' + text for text in texts], **kwargs)
        return pois, interests
    except Exception:
        raise SemanticUnavailable('semantic_model_unavailable') from None


_BUILD_LOCK = threading.Lock()


@lru_cache(maxsize=2)
def _cached_embeddings(ids, texts, metadata_json, config):
    expected = json.loads(metadata_json)
    path = Path(config.cache_dir) / (_digest(expected) + '.npz')
    with _BUILD_LOCK:
        hit = _read_cache(path, expected)
        if hit is not None:
            return hit
        if not config.regenerate:
            raise SemanticUnavailable('semantic_cache_unavailable')
        path.parent.mkdir(parents=True, exist_ok=True)
        lock = path.with_suffix('.lock')
        deadline = time.monotonic() + config.lock_timeout
        while True:
            try:
                lock.mkdir()
                break
            except FileExistsError:
                if time.monotonic() >= deadline:
                    raise SemanticUnavailable('semantic_cache_lock_timeout') from None
                time.sleep(.05)
        try:
            hit = _read_cache(path, expected)
            if hit is not None:
                return hit
            pois, interests = _encode(texts, config)
            _validate_embeddings(pois, interests, len(ids))
            meta = expected | dict(generated_at=datetime.now(timezone.utc).isoformat(),
                                   embedding_hash=hashlib.sha256(pois.tobytes() + interests.tobytes()).hexdigest())
            fd, name = tempfile.mkstemp(dir=path.parent, suffix='.npz')
            try:
                with os.fdopen(fd, 'wb') as f:
                    np.savez_compressed(f, poi_embeddings=pois, interest_embeddings=interests,
                                        metadata=np.array(json.dumps(meta, ensure_ascii=False)))
                os.replace(name, path)
            finally:
                if os.path.exists(name):
                    os.unlink(name)
            return pois, interests
        finally:
            lock.rmdir()


def embeddings(places, config):
    ids, texts, metadata = catalogue_identity(places)
    pois, interests = _cached_embeddings(ids, texts, json.dumps(metadata, sort_keys=True), config)
    index = {pid: i for i, pid in enumerate(ids)}
    return pois[[index[pid] for pid in places.place_ids]], interests, metadata


def score_catalogue(places, users, *, method='e5_hybrid', config=None):
    if method not in ('e5_hybrid', 'feature_mean', 'binary'):
        raise ValueError('UNKNOWN_INDIVIDUAL_METHOD')
    config = ScoringConfig.from_env() if config is None else config
    users = np.asarray(users, float)
    # Input errors are deliberately outside the semantic fallback boundary.
    feature = score_individuals(users, places.features)
    if len(places.records) != len(places.place_ids) or len(places.features) != len(places.place_ids):
        raise ValueError('INVALID_CATALOGUE_ALIGNMENT')
    fn = normalize(feature)
    sr = np.full_like(feature, np.nan)
    sn = np.full_like(feature, np.nan)
    hybrid = np.full_like(feature, np.nan)
    output = feature.copy() if method != 'e5_hybrid' else fn.copy()
    statuses = [dict(requested_scoring_method=method, effective_scoring_method='feature_mean',
                     fallback_reason=None) for _ in users]
    directional = np.any(users != 3, axis=1)
    metadata = None
    if method == 'e5_hybrid':
        for u in np.flatnonzero(~directional):
            statuses[u]['fallback_reason'] = 'all_neutral_preferences'
        if directional.any() and len(places.place_ids):
            try:
                pois, interests, metadata = embeddings(places, config)
                for u in np.flatnonzero(directional):
                    try:
                        row = semantic(users[u:u+1], pois, interests, config.beta)
                        if row.shape != (1, len(places.place_ids)) or not np.isfinite(row).all():
                            raise ValueError('invalid_semantic_output')
                        sr[u] = row[0]
                        sn[u] = normalize(row)[0]
                        hybrid[u] = (1 - config.alpha) * fn[u] + config.alpha * sn[u]
                        output[u] = hybrid[u]
                        statuses[u]['effective_scoring_method'] = 'e5_hybrid'
                    except Exception:
                        statuses[u]['fallback_reason'] = 'semantic_scoring_error'
            except Exception as exc:
                reason = str(exc) if isinstance(exc, SemanticUnavailable) else 'semantic_cache_unavailable'
                for u in np.flatnonzero(directional):
                    statuses[u]['fallback_reason'] = reason
    diagnostics = dict(requested_scoring_method=method, scoring_version=SCORING_VERSION if method == 'e5_hybrid' else 'active-feature-mean-v1',
                       model_id=MODEL_ID if method == 'e5_hybrid' else None,
                       model_revision=MODEL_REVISION if method == 'e5_hybrid' else None,
                       text_version=TEXT_VERSION, alpha=config.alpha, beta=config.beta,
                       normalization='full-catalogue-tied-percentile', catalogue_size=len(places.place_ids),
                       cache_identity=({k: v for k, v in metadata.items() if k != 'poi_ids'} if metadata else None), members=statuses,
                       score_scale='relative_relevance_0_1' if method == 'e5_hybrid' else '1-5')
    return ScoreResult(output, feature, sr, fn, sn, hybrid, diagnostics,
                       tuple(document(row)[1] for row in places.records))



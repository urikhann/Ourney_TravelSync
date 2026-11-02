"""Reproducible Option C oracle, missing-data, eligibility and GFAR checks."""

from dataclasses import replace

import numpy as np
import pytest
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from ourney_travelsync import FEATURE_NAMES, GFAR, load_places
from ourney_travelsync.recommenders.pipeline import prepare_group
from ourney_travelsync.recommenders.tfidf import FIELDS, clean, document, score_tfidf


def test_document_exact_allowlist_and_experiment_cleaning():
    row = dict(zip(FIELDS, ["한식!", "NULL", "떡볶이", None, "ＡＢＣ, 바닷가 산책"]))
    row.update(place_name="CANARY", category_name="CANARY", representative_activity="CANARY")
    assert document(row) == "한식 떡볶이 abc 바닷가 산책"
    assert document({}) == ""
    assert all(clean(v) == "" for v in [None, np.nan, "NULL", "N/A", "UNKNOWN", "NOT_APPLICABLE"])


def test_exact_sklearn_cosine_and_query_not_in_fitting_corpus():
    rows = [{"description_summary": s} for s in ["바다 산책", "숲속 산책", "떡볶이 식당", ""]]
    queries = ["바다 산책", "떡볶이", "queryonly", ""]
    oracle = TfidfVectorizer(analyzer="char", ngram_range=(2, 3), smooth_idf=True, norm="l2")
    docs = oracle.fit_transform(["바다 산책", "숲속 산책", "떡볶이 식당", ""])
    expected = cosine_similarity(oracle.transform(queries), docs)
    scores = score_tfidf(queries, rows)
    np.testing.assert_allclose(scores, expected, atol=1e-15)
    np.testing.assert_array_equal(scores, score_tfidf(queries, rows))
    assert "다 산" in oracle.vocabulary_  # char, including spaces, not char_wb or word-local grams
    assert scores[0, 0] == pytest.approx(1)
    assert scores[1, 2] > 0
    assert not scores[2:].any() and not scores[:, 3].any()
    np.testing.assert_array_equal(scores[:1], score_tfidf(queries[:1], rows))


@pytest.mark.parametrize("rows", [[], [{}], [{"description_summary": "a"}], [{"food_type": "NULL"}]])
def test_empty_and_no_vocabulary(rows):
    scores = score_tfidf(["바다", ""], rows)
    assert scores.shape == (2, len(rows)) and not scores.any()
    assert score_tfidf([], rows).shape == (0, len(rows))


def test_pipeline_fits_eligible_only_and_preserves_member_alignment():
    base = load_places()
    rows = tuple(dict(base.records[i], description_summary=text) for i, text in enumerate(
        ["바다 산책", "숲속 산책", "바다 바다 숲속 산책 카페"]))
    places = replace(base, records=rows, place_ids=base.place_ids[:3],
                     features=base.features[:3], coordinates=base.coordinates[:3])
    users = {"b": dict.fromkeys(FEATURE_NAMES, 3), "a": dict.fromkeys(FEATURE_NAMES, 3)}
    queries = {"a": "숲속 산책", "b": "바다 산책"}  # opposite mapping order
    data = prepare_group(places, users, user_queries=queries, excluded_place_ids=places.place_ids[2:])
    expected = score_tfidf([queries[u] for u in users], rows[:2])
    indices = [places.place_ids.index(pid) for pid in data.poi_ids]
    np.testing.assert_allclose(data.individual_scores, expected[:, indices])
    assert GFAR().recommend(data, top_k=2)
    poisoned = replace(places, records=rows[:2] + (dict(rows[2], description_summary="분리 어휘"),))
    repeat = prepare_group(poisoned, users, user_queries=queries, eligible_place_ids=places.place_ids[:2])
    np.testing.assert_array_equal(data.individual_scores, repeat.individual_scores)
    assert data.poi_ids == repeat.poi_ids
    assert not prepare_group(places, users).individual_scores.any()
    with pytest.raises(ValueError, match="member IDs"):
        prepare_group(places, users, user_queries={"a": "산책"})


def test_db_tfidf_scores_persist_same_values_used_for_gfar():
    from test_db_adapter import snapshot
    from ourney_travelsync.db.adapter import adapt_run_inputs, compute_snapshot
    inputs = snapshot()
    inputs["run"]["config_snapshot"].update(individual_method="tfidf", candidate_top_n=1)
    for i, row in enumerate(inputs["pois"]):
        row["enriched_metadata"] = {"description_summary": ["바다 산책", "떡볶이 식당"][i]}
    for i, row in enumerate(inputs["preferences"]):
        row["preference_text"] = ["떡볶이", "바다"][i]
    adapted = adapt_run_inputs(inputs)
    expected = prepare_group(adapted.places, adapted.preferences, user_queries=adapted.queries,
                             individual_top_n=1)
    results, individual, group = compute_snapshot(inputs)
    assert results == GFAR().recommend(expected, top_k=2)
    for u, uid in enumerate(expected.user_ids):
        for j, pid in enumerate(expected.poi_ids):
            saved = next(r for r in individual if r["user_id"] == uid and r["poi_id"] == pid)
            assert saved["score"] == expected.individual_scores[u, j]
            assert saved["rank"] is not None
    for row in group:
        assert row["mean_score"] == pytest.approx(np.mean([
            r["score"] for r in individual if r["poi_id"] == row["poi_id"]]))

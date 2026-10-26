"""The fixtures from TS SDD 1.1 (8.2 score example, 9.3 GFAR example, 9.2 boundary rules)."""
import random

import group_rec as g  # services/mocks/group_rec.py (conftest adds it to the path)

F = g.FEATURES


def test_score_example_is_10_over_30():
    w = g.weights(dict(zip(F, [5, 4, 2, 1, 3, 2, 5, 4, 3, 1])))
    x = dict(zip(F, [0, 0, 1, 0, 0, 0, 1, 0, 1, 0]))   # nature, activity, relax
    assert abs(g.score(w, x) - 10 / 30) < 1e-6
    assert abs(sum(w.values()) - 1) < 1e-12


def test_all_features_one_scores_1_and_all_zero_scores_0():
    w = g.weights(dict(zip(F, [5, 1, 3, 2, 4, 5, 1, 2, 3, 4])))
    assert abs(g.score(w, {f: 1 for f in F}) - 1) < 1e-12
    assert g.score(w, {f: 0 for f in F}) == 0


def test_gfar_fixture_gives_a_c_b():
    tops = {"u1": ["A", "B", "D"], "u2": ["A", "B", "D"], "u3": ["C", "B", "D"]}
    out = g.gfar(tops, 3, {})
    assert [x["poi_id"] for x in out] == ["A", "C", "B"]
    assert [round(x["selection_gain"], 4) for x in out] == [round(4 / 3, 4), round(2 / 3, 4), round(1 / 3, 4)]
    assert out[1]["picked_for"] == "u3"   # C is chosen for the member who differs


def test_gfar_does_not_depend_on_member_order():
    tops = {"u1": ["A", "B", "D"], "u2": ["A", "B", "D"], "u3": ["C", "B", "D"]}
    items = list(tops.items())
    for seed in range(5):
        random.Random(seed).shuffle(items)
        assert [x["poi_id"] for x in g.gfar(dict(items), 3, {})] == ["A", "C", "B"]


def test_boundaries_single_candidate_and_zero_gains():
    assert g.relevance(["X"]) == {"X": 1.0}                         # no division by zero
    out = g.gfar({"u1": ["A", "B"], "u2": ["A", "B"]}, 3, {"A": 0.5, "B": 0.4})
    assert [x["poi_id"] for x in out] == ["A", "B"]                  # stops when the pool is used up
    tie = g.gfar({"u1": ["B", "A"], "u2": ["A", "B"]}, 1, {"A": 0.5, "B": 0.5})
    assert tie[0]["poi_id"] == "A"                                   # equal gain + equal mean -> poi_id ascending


def test_group_utility():
    assert [g.utility(r, 4) for r in range(1, 5)] == [1000, 750, 500, 250]

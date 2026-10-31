"""The service must only translate: same picks as calling the package directly, contract shape, E1 rules."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import main
from ourney_travelsync import FEATURE_NAMES, load_places
from ourney_travelsync.recommenders import recommend_group
from ourney_travelsync.recommenders.average_without_misery import AverageWithoutMisery

client = TestClient(main.app)
EXAMPLE = json.loads((Path(__file__).resolve().parents[3] / "contracts" / "recommend.request.json").read_text())


def req(method="gfar", **opts):
    r = json.loads(json.dumps(EXAMPLE))
    r["options"] = {**r["options"], "method": method, **opts}
    return r


def test_health():
    body = client.get("/health").json()
    assert body["ok"] and body["places"] == 3570 and body["dataset_version"] == "busan-enriched-v1"


@pytest.mark.parametrize("method", ["gfar", "average", "awm"])
def test_contract_shape_and_scale(method):
    r = client.post("/recommend", json=req(method))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["score_scale"] == "1-5" and body["dataset_version"] == "busan-enriched-v1" and body["method"] == method
    ids = [g["poi_id"] for g in body["group"]]
    assert len(ids) == len(set(ids)) and [g["group_rank"] for g in body["group"]] == list(range(1, len(ids) + 1))
    for g in body["group"]:
        assert 1 <= g["mean_score"] <= 5 and set(g["member_scores"]) == {m["user_id"] for m in EXAMPLE["members"]}
        assert all(1 <= s <= 5 for s in g["member_scores"].values())
    if method == "gfar":
        assert len(ids) == 30 and all(g["selection_gain"] is not None for g in body["group"])


def test_same_order_as_the_package():
    users = {m["user_id"]: m["ratings"] for m in EXAMPLE["members"]}
    for method, model in [("gfar", None), ("awm", AverageWithoutMisery(2.5))]:
        body = client.post("/recommend", json=req(method, tie_seed=0)).json()
        direct = recommend_group(load_places(), users, top_k=30, tie_seed=0, recommender=model)
        assert [g["poi_id"] for g in body["group"]] == [x.poi_id for x in direct]


def test_member_exclusions_are_removed_for_everyone():
    first = client.post("/recommend", json=req()).json()["group"][0]["poi_id"]
    r = req()
    r["members"][1]["exclusions"] = {"tags": ["hiking"], "poi_ids": [], "resolved": {first: "hiking"}}
    body = client.post("/recommend", json=r).json()
    assert first not in [g["poi_id"] for g in body["group"]]
    assert {"poi_id": first, "stage": "E1", "rule": "member_exclusion", "detail": "hiking (u_jae)"} in body["removed"]


def test_seed_per_trip_is_repeatable_and_varies():
    a1 = client.post("/recommend", json={**req(), "trip_id": "trip-a"}).json()["group"]
    a2 = client.post("/recommend", json={**req(), "trip_id": "trip-a"}).json()["group"]
    b = client.post("/recommend", json={**req(), "trip_id": "trip-b"}).json()["group"]
    assert [g["poi_id"] for g in a1] == [g["poi_id"] for g in a2]
    assert [g["poi_id"] for g in a1] != [g["poi_id"] for g in b]


@pytest.mark.parametrize("change,status", [
    (lambda r: r.update(dataset_version="busan-csv-1"), 409),
    (lambda r: r["members"][0]["ratings"].update(food=6), 422),
    (lambda r: r["members"][0]["ratings"].update(food=4.5), 422),
    (lambda r: r["members"][0]["ratings"].pop("food"), 422),
    (lambda r: r.update(features=list(reversed(FEATURE_NAMES))), 422),
])
def test_bad_requests(change, status):
    r = req()
    change(r)
    assert client.post("/recommend", json=r).status_code == status

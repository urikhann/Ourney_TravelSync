"""End to end: 4 friends sign up, make a trip, answer preferences, get recommendations."""
import re
from datetime import date

from fastapi.testclient import TestClient

from app import config
from app.emailer import OUTBOX
from app.main import app

c = TestClient(app)
config.RESEND_COOLDOWN_SECONDS = 0


def signup(name):
    email = f"{name.lower()}@flowtest.com"
    assert c.post("/auth/signup", json={"email": email, "name": name, "password": "password123"}).status_code == 201
    code = re.search(r"\b(\d{6})\b", [m for m in OUTBOX if m["to"] == email][-1]["body"]).group(1)
    tok = c.post("/auth/verify-email", json={"email": email, "code": code}).json()["access_token"]
    return {"Authorization": f"Bearer {tok}"}


FEATURES = ["food", "cafe", "nature", "history", "art", "shopping", "activity", "entertainment", "relax", "festival"]
RATINGS = {  # 10 raw 1–5 scores in the fixed order, plus personal exclusions
    "Bat": ([5, 5, 2, 3, 2, 3, 2, 3, 4, 3], ["hiking"]),
    "Jaehoon": ([5, 4, 3, 3, 2, 2, 4, 4, 2, 5], []),
    "Sora": ([3, 4, 5, 3, 5, 2, 1, 2, 5, 3], []),
    "Minho": ([4, 3, 5, 4, 3, 2, 5, 4, 2, 3], ["raw_fish"]),
}


def prefs(n):
    return {"ratings": dict(zip(FEATURES, RATINGS[n][0]))}


def test_full_trip_flow():
    h = {n: signup(n) for n in RATINGS}

    # Bat creates the trip
    r = c.post("/trips", headers=h["Bat"], json={"name": "Busan Weekend", "city": "busan",
                                                 "start_date": "2026-10-09", "end_date": "2026-10-11"})
    assert r.status_code == 201
    trip = r.json()
    tid, code = trip["id"], trip["invite_code"]

    # others can't see it until they join
    assert c.get(f"/trips/{tid}", headers=h["Sora"]).status_code == 404
    assert c.get(f"/invites/{code}", headers=h["Sora"]).json()["name"] == "Busan Weekend"
    for n in ["Jaehoon", "Sora", "Minho"]:
        assert c.post(f"/trips/join/{code}", headers=h[n]).status_code == 200
    assert len(c.get(f"/trips/{tid}", headers=h["Bat"]).json()["members"]) == 4
    assert len(c.get("/trips", headers=h["Minho"]).json()) == 1

    # availability
    r = c.put(f"/trips/{tid}/availability", headers=h["Bat"], json={"dates": ["2026-10-09", "2026-10-10"]})
    me = [m for m in r.json()["members"] if m["is_me"]][0]
    assert me["free_dates"] == ["2026-10-09", "2026-10-10"]

    # personal exclusions are their own input
    for n in RATINGS:
        assert c.put(f"/trips/{tid}/exclusions/me", headers=h[n], json={"tags": RATINGS[n][1]}).status_code == 200
    assert c.get(f"/trips/{tid}/exclusions/me", headers=h["Bat"]).json() == {"tags": ["hiking"], "poi_ids": []}

    # recommendations wait for everyone
    for n in ["Bat", "Jaehoon", "Minho"]:
        assert c.put(f"/trips/{tid}/preferences/me", headers=h[n], json=prefs(n)).status_code == 200
    r = c.post(f"/trips/{tid}/recommendations", headers=h["Bat"])
    assert r.status_code == 409 and "Sora" in r.json()["detail"]
    assert c.post(f"/trips/{tid}/nudge", headers=h["Bat"]).json()["nudged"] == ["Sora"]

    assert c.put(f"/trips/{tid}/preferences/me", headers=h["Sora"], json=prefs("Sora")).json()["version"] == 1
    r = c.put(f"/trips/{tid}/preferences/me", headers=h["Sora"], json={**prefs("Sora"), "expected_version": 1})
    assert r.json()["version"] == 2                                             # saving adds a version
    assert c.put(f"/trips/{tid}/preferences/me", headers=h["Sora"],
                 json={**prefs("Sora"), "expected_version": 1}).status_code == 409  # stale edit refused
    assert c.get(f"/trips/{tid}/preferences/me", headers=h["Sora"]).json()["version"] == 2

    r = c.post(f"/trips/{tid}/recommendations", headers=h["Bat"])
    assert r.status_code == 200, r.text
    rec = r.json()
    assert rec["method"] == "gfar" and rec["dataset_version"] == "sample-v1" and rec["feature_schema_version"] == "fs-1.0"
    ids = [x["poi_id"] for x in rec["group"]]
    removed = {x["poi_id"]: x for x in rec["removed"]}
    assert removed["igidae"]["rule"] == "member_exclusion" and removed["igidae"]["stage"] == "E1"   # Bat: no hiking
    assert removed["jagalchi"]["rule"] == "member_exclusion"                                      # Minho: no raw fish
    assert "igidae" not in ids and "taejongdae" not in ids and "millak" not in ids
    assert [x["group_rank"] for x in rec["group"]] == list(range(1, len(ids) + 1))
    assert rec["group"][0]["group_utility"] == 1000
    assert all(0 <= s <= 1 for x in rec["group"] for s in x["member_scores"].values())
    assert all(abs(x["mean_score"] - sum(x["member_scores"].values()) / 4) < 1e-6 for x in rec["group"])
    assert rec["group"][0]["place"]["name"] and rec["group"][0]["reasons"]
    assert set(rec["metrics"]["per_member_relevance"]) == set(rec["members"])
    assert c.get(f"/trips/{tid}/recommendations/latest", headers=h["Sora"]).json()["run_id"] == rec["run_id"]

    # GFAR with a short list: every member gets at least one of their own top picks
    small = c.post(f"/trips/{tid}/recommendations", headers=h["Bat"], json={"group_k": 4}).json()
    assert len(small["group"]) == 4 and min(small["metrics"]["per_member_relevance"].values()) > 0
    # the plain average is available as a comparison method
    avg = c.post(f"/trips/{tid}/recommendations", headers=h["Bat"], json={"method": "average", "group_k": 4}).json()
    assert avg["method"] == "average" and len(avg["group"]) == 4
    awm = c.post(f"/trips/{tid}/recommendations", headers=h["Bat"], json={"method": "awm", "awm_threshold": 0.1}).json()
    assert all(min(x["member_scores"].values()) >= 0.1 for x in awm["group"])
    c.post(f"/trips/{tid}/recommendations", headers=h["Bat"])  # back to the default GFAR run for the plans

    # plans: 3 variants, vote, majority picks the winner
    r = c.post(f"/trips/{tid}/itineraries/generate", headers=h["Bat"])
    assert r.status_code == 200, r.text
    plans = r.json()
    assert [p["letter"] for p in plans["plans"]] == ["A", "B", "C"]
    a = plans["plans"][0]
    assert len(a["days"]) == 3 and a["days"][0]["items"][0]["place"]["name"]
    assert plans["availability_checked"] is False and plans["holidays"] == []
    for n in ["Bat", "Jaehoon"]:
        c.post(f"/itineraries/{a['id']}/vote", headers=h[n])
    r = c.post(f"/itineraries/{plans['plans'][1]['id']}/vote", headers=h["Sora"]).json()
    assert r["voted"] == 3 and r["chosen_id"] is None
    r = c.post(f"/itineraries/{a['id']}/vote", headers=h["Minho"]).json()
    assert r["chosen_id"] == a["id"] and r["my_vote"] == a["id"]

    # place details; the availability checker is switched off in the first version
    poi = c.get("/pois/gamcheon", headers=h["Bat"]).json()
    assert poi["hours"] and poi["features"] == ["history", "art"] and poi["dataset_version"] == "sample-v1"
    assert a["stats"]["cost_unknown_count"] == 0
    assert c.get("/pois/gamcheon/availability", headers=h["Bat"], params={"date": "2026-10-10"}).status_code == 404
    assert c.get("/meta/features").json() == {"availability": False}
    assert all(i["availability"] is None for d in a["days"] for i in d["items"])


def test_availability_checker_logic_for_later():
    """The checker is off in v1, but its logic (in the mock service) stays tested for when we switch it on."""
    from tests.conftest import mocks
    r = mocks.check_one("gamcheon", date(2026, 10, 10), "16:00", 90)
    assert r["available"] is False and "17:30" in r["reason"]
    r = mocks.check_one("gukje", date(2026, 10, 9), "10:00", 90)
    assert r["available"] is False and "Hangul" in r["reason"] and r["next_available"] == "2026-10-10"


def test_validation():
    h = signup("Val")
    assert c.post("/trips", headers=h, json={"name": "x", "start_date": "2026-10-11", "end_date": "2026-10-09"}).status_code == 422
    assert c.post("/trips", headers=h, json={"name": "x", "start_date": "2026-10-09", "end_date": "2026-10-12"}).status_code == 422  # max 3 days
    tid = c.post("/trips", headers=h, json={"name": "x", "start_date": "2026-10-09", "end_date": "2026-10-10"}).json()["id"]
    good = dict(zip(FEATURES, [3] * 10))
    url = f"/trips/{tid}/preferences/me"
    assert c.put(url, headers=h, json={"ratings": good}).status_code == 200
    for bad in [{**good, "food": 0}, {**good, "food": 6}, {**good, "food": 4.5}, {**good, "food": 4.0},
                {**good, "food": "4"}, {k: v for k, v in good.items() if k != "festival"}, {**good, "culture": 3}]:
        assert c.put(url, headers=h, json={"ratings": bad}).status_code == 422, bad
    assert c.put(f"/trips/{tid}/exclusions/me", headers=h, json={"tags": ["no_such_rule"]}).status_code == 422
    assert c.get("/trips").status_code == 401


def test_group_size_limit():
    old = config.MAX_GROUP_MEMBERS
    config.MAX_GROUP_MEMBERS = 2
    try:
        a, b, d = signup("Lim1"), signup("Lim2"), signup("Lim3")
        code = c.post("/trips", headers=a, json={"name": "x", "start_date": "2026-10-09", "end_date": "2026-10-09"}).json()["invite_code"]
        assert c.post(f"/trips/join/{code}", headers=b).status_code == 200
        r = c.post(f"/trips/join/{code}", headers=d)
        assert r.status_code == 409 and r.json()["detail"] == "trip_full"
    finally:
        config.MAX_GROUP_MEMBERS = old


def test_meta_preferences_lists_the_ten_features_in_order():
    meta = c.get("/meta/preferences").json()
    assert [f["code"] for f in meta["features"]] == FEATURES
    assert meta["features"][3]["label_ko"] == "역사와 전통"

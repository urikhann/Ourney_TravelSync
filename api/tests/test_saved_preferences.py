"""Issue #21: my 10 ratings are saved once and reused for every trip; a trip can be customized on its own."""
from uuid import uuid4

from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import Preference, RecommendationRun, UserPreference
from .test_flow import FEATURES, c, signup

DAY = "2026-11-06"
LIKES = {"food": 5, "cafe": 4, "nature": 4, "history": 2, "art": 3, "shopping": 1, "activity": 3, "entertainment": 2,
         "relax": 5, "festival": 2}


def person():
    return signup("Sp" + uuid4().hex[:10])


def new_trip(h):
    r = c.post("/trips", headers=h, json=dict(name="Saved prefs", start_date=DAY, end_date=DAY))
    assert r.status_code == 201, r.text
    return r.json()


def mine(h, tid):
    return c.get(f"/trips/{tid}/preferences/me", headers=h).json()


def test_first_answers_are_saved_and_reused_in_the_next_trip():
    me = person()
    assert c.get("/me/preferences", headers=me).json()["saved"] is False
    first = new_trip(me)
    assert mine(me, first["id"])["source"] == "none"  # first trip: the survey is needed
    r = c.put(f"/trips/{first['id']}/preferences/me", headers=me, json={"ratings": LIKES})
    assert r.json()["source"] == "saved"
    profile = c.get("/me/preferences", headers=me).json()
    assert profile["saved"] is True and profile["ratings"] == LIKES and profile["version"] == 1

    second = new_trip(me)  # returning user: no survey
    p = mine(me, second["id"])
    assert p == {**p, "submitted": True, "source": "saved", "ratings": LIKES}
    detail = c.get(f"/trips/{second['id']}", headers=me).json()
    assert detail["members"][0]["preferences_done"] is True


def test_joining_a_trip_uses_saved_preferences():
    owner, friend = person(), person()
    c.put("/me/preferences", headers=friend, json={"ratings": LIKES})
    trip = new_trip(owner)
    c.post(f"/trips/join/{trip['invite_code']}", headers=friend)
    assert mine(friend, trip["id"])["ratings"] == LIKES and mine(friend, trip["id"])["source"] == "saved"


def test_customizing_a_trip_keeps_my_saved_preferences():
    me = person()
    c.put("/me/preferences", headers=me, json={"ratings": LIKES})
    trip = new_trip(me)
    custom = {**LIKES, "shopping": 5}
    r = c.put(f"/trips/{trip['id']}/preferences/me", headers=me, json={"ratings": custom, "expected_version": 1})
    assert r.json()["source"] == "custom" and r.json()["version"] == 2
    assert c.get("/me/preferences", headers=me).json()["ratings"] == LIKES  # not overwritten

    back = c.post(f"/trips/{trip['id']}/preferences/me/use-saved", headers=me).json()
    assert back["ratings"] == LIKES and back["source"] == "saved" and back["version"] == 3  # a new version, history kept

    r = c.put(f"/trips/{trip['id']}/preferences/me", headers=me, json={"ratings": custom, "save_as_default": True})
    assert r.json()["source"] == "saved"
    profile = c.get("/me/preferences", headers=me).json()
    assert profile["ratings"] == custom and [h["version"] for h in profile["history"]] == [2, 1]


def test_changing_saved_preferences_does_not_change_existing_trips_or_runs():
    me, friend = person(), person()
    c.put("/me/preferences", headers=me, json={"ratings": LIKES})
    trip = new_trip(me)
    c.post(f"/trips/join/{trip['invite_code']}", headers=friend)
    c.put(f"/trips/{trip['id']}/preferences/me", headers=friend, json={"ratings": dict.fromkeys(FEATURES, 3)})
    assert c.post(f"/trips/{trip['id']}/recommendations", headers=me, json={"method": "gfar"}).status_code == 200

    changed = {**LIKES, "food": 1}
    assert c.put("/me/preferences", headers=me, json={"ratings": changed, "expected_version": 1}).status_code == 200
    assert c.put("/me/preferences", headers=me, json={"ratings": changed, "expected_version": 1}).status_code == 409
    p = mine(me, trip["id"])
    assert p["ratings"] == LIKES and p["source"] == "custom"  # the trip keeps what the group planned with
    my_id = c.get("/me", headers=me).json()["id"]
    with SessionLocal() as db:  # the run points at the exact version it used, and that version is unchanged
        run = db.scalar(select(RecommendationRun).where(RecommendationRun.trip_id == trip["id"]))
        used = next(m["preference_version"] for m in run.member_snapshot if m["user_id"] == my_id)
        row = db.scalar(select(Preference).where(Preference.trip_id == trip["id"], Preference.user_id == my_id,
                                                 Preference.version == used))
        assert row.ratings == LIKES


def test_ratings_are_checked_and_deleting_the_account_removes_them():
    me = person()
    assert c.put("/me/preferences", headers=me, json={"ratings": {**LIKES, "food": 6}}).status_code == 422
    assert c.put("/me/preferences", headers=me, json={"ratings": {**LIKES, "food": 4.5}}).status_code == 422
    assert c.post(f"/trips/{new_trip(me)['id']}/preferences/me/use-saved", headers=me).status_code == 409
    uid = c.get("/me", headers=me).json()["id"]
    c.put("/me/preferences", headers=me, json={"ratings": LIKES})
    assert c.post("/me/delete", headers=me, json={"password": "password123"}).status_code == 204
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(UserPreference).where(UserPreference.user_id == uid)) == 0
        assert db.scalar(select(func.count()).select_from(Preference).where(Preference.user_id == uid)) == 0

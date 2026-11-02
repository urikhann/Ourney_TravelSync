"""Onboarding survey: catalog, saving, limits, skip, and how the answers reach trips and the recommender."""
import re

from fastapi.testclient import TestClient

from app import config, services
from app.emailer import OUTBOX
from app.main import app
from app.taste_catalog import QUESTIONS, check_answers, profile

c = TestClient(app)
config.RESEND_COOLDOWN_SECONDS = 0
FEATURES = ["food", "cafe", "nature", "history", "art", "shopping", "activity", "entertainment", "relax", "festival"]


def signup(name):
    email = f"{name.lower()}@tastetest.com"
    assert c.post("/auth/signup", json={"email": email, "name": name, "password": "password123"}).status_code == 201
    code = re.search(r"\b(\d{6})\b", [m for m in OUTBOX if m["to"] == email][-1]["body"]).group(1)
    tok = c.post("/auth/verify-email", json={"email": email, "code": code}).json()["access_token"]
    return {"Authorization": f"Bearer {tok}"}


def test_catalog_has_four_questions_with_unique_codes():
    meta = c.get("/meta/tastes").json()
    assert [q["code"] for q in meta["questions"]] == ["food", "places", "activities", "avoid"] and meta["max_picks"] == 5
    for q in QUESTIONS:
        codes = [o["code"] for o in q["options"]]
        assert len(codes) == len(set(codes)) and all(o["keywords"] for o in q["options"])


def test_profile_turns_answers_into_keywords_and_rules():
    p = profile(check_answers({"food": ["seafood"], "places": ["market", "sea"], "avoid": ["hiking", "crowded"]}))
    assert "해산물" in p["keywords"] and "시장" in p["keywords"] and "바다" in p["keywords"]
    assert "등산" in p["avoid_keywords"] and "인파" in p["avoid_keywords"]
    assert p["exclusion_tags"] == ["hiking"]  # "crowded" is a soft dislike, not a hard rule


def test_new_user_has_no_answers_then_saves_and_edits():
    h = signup("Tina")
    assert c.get("/me/tastes", headers=h).json()["status"] is None
    r = c.put("/me/tastes", headers=h, json={"answers": {"food": ["seafood", "korean"], "places": ["market"]}})
    assert r.status_code == 200 and r.json()["status"] == "done"
    assert r.json()["answers"] == {"food": ["korean", "seafood"], "places": ["market"], "activities": [], "avoid": []}
    r = c.put("/me/tastes", headers=h, json={"answers": {"activities": ["shopping"]}})
    assert r.json()["answers"]["food"] == [] and r.json()["answers"]["activities"] == ["shopping"]


def test_limits_and_unknown_codes_are_refused():
    h = signup("Lena")
    six = ["korean", "japanese", "chinese", "western", "seafood", "meat_bbq"]
    for bad in [{"food": six}, {"food": ["pizza"]}, {"music": ["kpop"]}, {"food": ["korean", "korean"]}]:
        assert c.put("/me/tastes", headers=h, json={"answers": bad}).status_code == 422, bad
    assert c.get("/me/tastes").status_code == 401


def test_skip_is_remembered_but_never_deletes_answers():
    h = signup("Skip")
    assert c.put("/me/tastes", headers=h, json={"skipped": True}).json()["status"] == "skipped"
    c.put("/me/tastes", headers=h, json={"answers": {"food": ["korean"]}})
    r = c.put("/me/tastes", headers=h, json={"skipped": True}).json()
    assert r["status"] == "done" and r["answers"]["food"] == ["korean"]


def test_avoid_rules_reach_trips_and_keywords_reach_the_recommender():
    a, b = signup("Ava"), signup("Ben")
    # a trip that existed before the survey picks up the hard rule (no rules set there yet)
    t1 = c.post("/trips", headers=a, json={"name": "x", "start_date": "2026-11-07", "end_date": "2026-11-07"}).json()
    c.put("/me/tastes", headers=a, json={"answers": {"food": ["seafood"], "avoid": ["hiking", "outdoor"]}})
    assert c.get(f"/trips/{t1['id']}/exclusions/me", headers=a).json()["tags"] == ["hiking"]
    # a new trip starts with it too; my own changes there are kept afterwards
    t2 = c.post("/trips", headers=a, json={"name": "y", "start_date": "2026-11-07", "end_date": "2026-11-07"}).json()
    assert c.get(f"/trips/{t2['id']}/exclusions/me", headers=a).json()["tags"] == ["hiking"]
    c.put(f"/trips/{t2['id']}/exclusions/me", headers=a, json={"tags": ["alcohol"]})
    c.put("/me/tastes", headers=a, json={"answers": {"avoid": ["hiking"]}})
    assert c.get(f"/trips/{t2['id']}/exclusions/me", headers=a).json()["tags"] == ["alcohol"]

    # the recommender request carries the keywords of members who answered (and nothing for the others)
    c.post(f"/trips/join/{t1['invite_code']}", headers=b)
    for h in (a, b):
        c.put(f"/trips/{t1['id']}/preferences/me", headers=h, json={"ratings": dict(zip(FEATURES, [3] * 10))})
    sent = []
    old = services.recommend

    async def spy(payload):
        sent.append(payload)
        return await old(payload)
    services.recommend = spy
    try:
        assert c.post(f"/trips/{t1['id']}/recommendations", headers=a).status_code in (200, 201)
    finally:
        services.recommend = old
    members = {m["user_id"]: m for m in sent[0]["members"]}
    me_a = c.get("/me", headers=a).json()["id"]
    me_b = c.get("/me", headers=b).json()["id"]
    assert "해산물" not in members[me_a]["tastes"]["keywords"]  # food answer was replaced by the later save
    assert members[me_a]["tastes"]["avoid_keywords"][0] == "등산"
    assert "tastes" not in members[me_b]

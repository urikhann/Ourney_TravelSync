"""Trip chat: members talk to each other; TravelSync answers only when asked (@TravelSync or a quick action)."""
import re

from fastapi.testclient import TestClient

from app import config, services
from app.emailer import OUTBOX
from app.main import app

c = TestClient(app)
config.RESEND_COOLDOWN_SECONDS = 0


def signup(name):
    email = f"{name.lower()}@chattest.com"
    assert c.post("/auth/signup", json={"email": email, "name": name, "password": "password123"}).status_code == 201
    code = re.search(r"\b(\d{6})\b", [m for m in OUTBOX if m["to"] == email][-1]["body"]).group(1)
    tok = c.post("/auth/verify-email", json={"email": email, "code": code}).json()["access_token"]
    return {"Authorization": f"Bearer {tok}"}


def trip_with(a, b):
    t = c.post("/trips", headers=a, json={"name": "Chat trip", "start_date": "2026-11-21", "end_date": "2026-11-22"}).json()
    c.post(f"/trips/join/{t['invite_code']}", headers=b)
    return t["id"]


def test_plain_messages_are_shared_and_never_sent_to_the_agent():
    a, b = signup("Cora"), signup("Dan")
    tid = trip_with(a, b)
    calls = []
    old = services.agent_turn

    async def spy(payload):
        calls.append(payload)
        return await old(payload)
    services.agent_turn = spy
    try:
        r = c.post(f"/trips/{tid}/chat", headers=a, json={"text": "Let's eat seafood tonight"})
        assert r.status_code == 201 and len(r.json()["messages"]) == 1
        c.post(f"/trips/{tid}/chat", headers=b, json={"text": "travelsync is great"})  # no @ at the start
    finally:
        services.agent_turn = old
    assert calls == []
    msgs = c.get(f"/trips/{tid}/chat", headers=b).json()["messages"]
    assert [m["author"]["name"] for m in msgs] == ["Cora", "Dan"] and all(m["kind"] == "user" for m in msgs)


def test_long_agent_reply_survives_save_and_reload(monkeypatch):
    a, b = signup("LongReplyA"), signup("LongReplyB")
    tid = trip_with(a, b)
    long_reply = "## Planning rules\n\n" + "- **Group fit:** 그룹 선호를 반영합니다.\n" * 180 + "\nComplete explanation."
    async def answer(payload):
        return dict(reply=long_reply, proposal=None, tool_calls=[])
    monkeypatch.setattr(services, "agent_turn", answer)
    response = c.post(f"/trips/{tid}/chat", headers=a, json={"text": "@TravelSync Why this plan?"})
    assert response.status_code == 201, response.text
    reply = response.json()["messages"][-1]
    assert len(reply["text"]) > 2000 and reply["text"] == long_reply
    saved = c.get(f"/trips/{tid}/chat", headers=a).json()["messages"][-1]
    assert saved["text"] == long_reply and saved["agent"]["reply"] == long_reply


def test_mention_gets_an_agent_reply_with_context():
    a, b = signup("Eve"), signup("Finn")
    tid = trip_with(a, b)
    c.post(f"/trips/{tid}/chat", headers=b, json={"text": "I'm so tired"})
    r = c.post(f"/trips/{tid}/chat", headers=a, json={"text": "@TravelSync it's raining, anything indoor?", "trigger": "MENTION"})
    me, reply = r.json()["messages"]
    assert reply["kind"] == "agent" and reply["author"] is None and reply["reply_to"] == me["id"]
    assert reply["agent"]["state"] == "SUCCEEDED" and "indoor" in reply["text"]
    assert reply["agent"]["tool_calls"][0]["name"] == "understand_request"
    # newer-than filter for polling
    newer = c.get(f"/trips/{tid}/chat", headers=b, params={"after": me["id"]}).json()["messages"]
    assert [m["id"] for m in newer] == [reply["id"]]


def test_quick_action_asks_the_agent_and_failures_become_a_friendly_reply():
    a, b = signup("Gia"), signup("Hal")
    tid = trip_with(a, b)
    r = c.post(f"/trips/{tid}/chat", headers=a, json={"text": "We're tired — less walking please", "trigger": "QUICK_ACTION"})
    assert r.json()["messages"][1]["agent"]["state"] == "SUCCEEDED"
    old = services.agent_turn

    async def down(payload):
        from fastapi import HTTPException
        raise HTTPException(503, "agent_unavailable")
    services.agent_turn = down
    try:
        r = c.post(f"/trips/{tid}/chat", headers=a, json={"text": "@TravelSync hello"})
    finally:
        services.agent_turn = old
    assert r.status_code == 201 and r.json()["messages"][1]["agent"]["state"] == "FAILED"


def test_only_members_and_valid_messages():
    a, b, x = signup("Ivy"), signup("Jo"), signup("Kit")
    tid = trip_with(a, b)
    assert c.get(f"/trips/{tid}/chat", headers=x).status_code == 404
    assert c.post(f"/trips/{tid}/chat", headers=x, json={"text": "hi"}).status_code == 404
    assert c.post(f"/trips/{tid}/chat", headers=a, json={"text": "   "}).status_code == 422
    assert c.post(f"/trips/{tid}/chat", headers=a, json={"text": "x" * 2001}).status_code == 422
    assert c.get(f"/trips/{tid}/chat").status_code == 401


def test_short_mentions_ask_the_agent_too():
    a, b = signup("Gus"), signup("Hana")
    tid = trip_with(a, b)
    for text in ("@ai is it raining?", "@AI indoor ideas", "@ts less walking"):
        r = c.post(f"/trips/{tid}/chat", headers=a, json={"text": text})
        mine, reply = r.json()["messages"]
        assert reply["kind"] == "agent"
        assert mine["text"].startswith("@TravelSync ")  # saved in the long form, so history reads the same
    r = c.post(f"/trips/{tid}/chat", headers=b, json={"text": "@aisle seats please"})
    assert len(r.json()["messages"]) == 1  # only the exact word counts


def test_location_search_finds_popular_points_and_places():
    a, b = signup("Lia"), signup("Moe")
    tid = trip_with(a, b)
    r = c.get(f"/trips/{tid}/locations", headers=a, params={"q": "station"}).json()
    assert {"부산역", "해운대역"} <= {x["name"] for x in r} and all(x["source"] == "popular" for x in r)
    assert len(c.get(f"/trips/{tid}/locations", headers=a).json()) >= 9  # empty search: the popular list
    hit = c.get(f"/trips/{tid}/locations", headers=a, params={"q": "부산역"}).json()[0]
    assert hit["name"] == "부산역" and 35 < hit["lat"] < 36 and 128 < hit["lng"] < 130
    outsider = signup("Nell")
    assert c.get(f"/trips/{tid}/locations", headers=outsider, params={"q": "역"}).status_code == 404


def test_location_search_uses_kakao_when_a_key_is_set(monkeypatch):
    import httpx
    from app.routes import locations
    calls = []

    def kakao(request):
        calls.append(request)
        return httpx.Response(200, json={"documents": [{"id": "123", "place_name": "신라스테이 해운대",
            "road_address_name": "부산 해운대구 해운대로570번길 46", "x": "129.1601", "y": "35.1610"}]})
    real = httpx.AsyncClient
    monkeypatch.setattr(locations.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(kakao), **kw))
    monkeypatch.setattr(config, "KAKAO_REST_API_KEY", "test-key")
    locations._cache.clear()
    a, b = signup("Otto"), signup("Pia")
    tid = trip_with(a, b)
    for _ in range(2):
        r = c.get(f"/trips/{tid}/locations", headers=a, params={"q": "신라스테이"}).json()
        assert r[0] == {"id": "kakao:123", "name": "신라스테이 해운대", "sub": "부산 해운대구 해운대로570번길 46",
                        "lat": 35.161, "lng": 129.1601, "source": "kakao"}
    assert len(calls) == 1  # the second search came from the cache
    assert calls[0].headers["Authorization"] == "KakaoAK test-key"

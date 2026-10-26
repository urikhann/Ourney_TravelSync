import re
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app import config
from app.emailer import OUTBOX
from app.main import app

c = TestClient(app)


def last_code(email):
    msg = [m for m in OUTBOX if m["to"] == email][-1]
    return re.search(r"\b(\d{6})\b", msg["body"]).group(1)


def test_full_flow(monkeypatch):
    monkeypatch.setattr(config, "RESEND_COOLDOWN_SECONDS", 0)
    r = c.post("/auth/signup", json={"email": "Bat@Example.com", "name": "Bat", "password": "supersecret1"})
    assert r.status_code == 201
    # can't log in before verifying
    assert c.post("/auth/login", json={"email": "bat@example.com", "password": "supersecret1"}).status_code == 403
    # wrong code is rejected
    assert c.post("/auth/verify-email", json={"email": "bat@example.com", "code": "000000" if last_code("bat@example.com") != "000000" else "111111"}).json()["detail"] == "wrong_code"
    r = c.post("/auth/verify-email", json={"email": "bat@example.com", "code": last_code("bat@example.com")})
    assert r.status_code == 200 and r.json()["user"]["email_verified"]
    token = r.json()["access_token"]
    assert c.get("/me", headers={"Authorization": f"Bearer {token}"}).json()["name"] == "Bat"
    # login
    assert c.post("/auth/login", json={"email": "bat@example.com", "password": "nope-nope"}).status_code == 401
    r = c.post("/auth/login", json={"email": "bat@example.com", "password": "supersecret1"})
    assert r.status_code == 200
    # duplicate signup
    assert c.post("/auth/signup", json={"email": "bat@example.com", "name": "X", "password": "supersecret1"}).status_code == 409
    # password reset logs out old tokens
    old = r.json()["access_token"]
    c.post("/auth/forgot-password", json={"email": "bat@example.com"})
    r = c.post("/auth/reset-password", json={"email": "bat@example.com", "code": last_code("bat@example.com"), "new_password": "newpassword2"})
    assert r.status_code == 200
    assert c.get("/me", headers={"Authorization": f"Bearer {old}"}).status_code == 401
    assert c.post("/auth/login", json={"email": "bat@example.com", "password": "newpassword2"}).status_code == 200


def test_attempt_limit_and_no_token(monkeypatch):
    monkeypatch.setattr(config, "RESEND_COOLDOWN_SECONDS", 0)
    c.post("/auth/signup", json={"email": "sora@example.com", "name": "Sora", "password": "supersecret1"})
    good = last_code("sora@example.com")
    bad = "123456" if good != "123456" else "654321"
    for _ in range(config.CODE_MAX_ATTEMPTS):
        c.post("/auth/verify-email", json={"email": "sora@example.com", "code": bad})
    r = c.post("/auth/verify-email", json={"email": "sora@example.com", "code": good})
    assert r.json()["detail"] == "too_many_attempts"
    assert c.get("/me").status_code == 401
    # resend gives a fresh working code
    c.post("/auth/resend-code", json={"email": "sora@example.com"})
    assert c.post("/auth/verify-email", json={"email": "sora@example.com", "code": last_code("sora@example.com")}).status_code == 200


def test_unknown_email_gives_same_answer():
    a = c.post("/auth/forgot-password", json={"email": "nobody@example.com"}).json()
    assert a["message"] == "if_the_account_exists_a_code_was_sent"

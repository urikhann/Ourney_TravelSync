"""Opt-in real HTTP processes; isolated DB and explicitly synthetic upstream only."""
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time

import httpx
import pytest


@pytest.mark.skipif(os.environ.get("TRAVELSYNC_LIVE_OPTIMIZER_TEST") != "1", reason="Opt-in loopback process test")
def test_real_http_app_recommender_optimizer_and_data_adapter(tmp_path):
    root = Path(__file__).resolve().parents[2]
    held, ports = [], []
    for _ in range(4):
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        held.append(sock)
        ports.append(sock.getsockname()[1])
    for sock in held:
        sock.close()
    urls = [f"http://127.0.0.1:{port}" for port in ports]
    mode = tmp_path / "mode.txt"
    mode.write_text("known")
    env = os.environ | dict(DATABASE_URL=f"sqlite:///{tmp_path / 'live.db'}", PLACES_CSV="", HOURS_CSV="",
                            PLACES_FOLLOW_ENV="false", RECOMMENDER_URL=urls[1], OPTIMIZER_URL=urls[2],
                            PLANNING_DATA_URL=urls[3], PLANNING_DATA_TOKEN="", SMTP_HOST="", FEATURE_AVAILABILITY="false",
                            SECRET_KEY="synthetic-loopback-key-000000000000000000000000", PYTHONUNBUFFERED="1",
                            TRAVELSYNC_SYNTHETIC_MODE_FILE=str(mode))
    modules = [("app.main:app", "api"), ("main:app", "services/recommender"),
               ("main:app", "services/optimizer"), ("runtime_planning:app", "api/tests")]
    processes, logs = [], []
    try:
        for i, (module, directory) in enumerate(modules):
            log = (tmp_path / f"server-{i}.log").open("w", encoding="utf-8")
            logs.append(log)
            processes.append(subprocess.Popen([sys.executable, "-m", "uvicorn", module, "--app-dir", str(root / directory),
                                               "--host", "127.0.0.1", "--port", str(ports[i])], cwd=root, env=env,
                                               stdout=log, stderr=subprocess.STDOUT,
                                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)))
        for url in urls:
            deadline = time.monotonic() + 45
            while True:
                try:
                    if httpx.get(url + "/health", timeout=0.5).status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                if time.monotonic() > deadline:
                    pytest.fail("An isolated loopback service did not start; inspect temporary server logs")
                time.sleep(0.1)
        with httpx.Client(base_url=urls[0], timeout=50) as client:
            assert client.get("/docs").status_code == 200
            assert client.post("/auth/signup", json=dict(email="loopback@flowtest.com", name="Loopback fixture",
                                                        password="synthetic-fixture-password")).status_code == 201
            log_text = (tmp_path / "server-0.log").read_text(encoding="utf-8")
            code = re.findall(r"Your code to verify your email is:\s*(\d{6})", log_text)[-1]
            verified = client.post("/auth/verify-email", json=dict(email="loopback@flowtest.com", code=code))
            client.headers["Authorization"] = "Bearer " + verified.json()["access_token"]
            trip = client.post("/trips", json=dict(name="Synthetic loopback", start_date="2026-11-06", end_date="2026-11-06")).json()
            tid = trip["id"]
            client.put(f"/trips/{tid}/preferences/me", json={"ratings": dict.fromkeys(
                ("food", "cafe", "nature", "history", "art", "shopping", "activity", "entertainment", "relax", "festival"), 3)})
            client.put(f"/trips/{tid}/availability", json={"dates": ["2026-11-06"]})
            rec = client.post(f"/trips/{tid}/recommendations", json={"group_k": 3})
            assert rec.status_code == 200
            from .test_optimizer_integration import configuration
            response = client.post(f"/trips/{tid}/itineraries/generate", json=configuration())
            assert response.status_code == 200, response.text
            plan = response.json()["plans"][0]
            assert plan["stats"]["validation_status"] == "PASSED"
            assert plan["days"][0]["return_travel_minutes"] == 10
            client.post(f"/itineraries/{plan['id']}/vote")
            mode.write_text("unknown")
            failed = client.post(f"/trips/{tid}/itineraries/generate", json=configuration())
            assert failed.status_code == 409 and failed.json()["detail"] == "optimizer_insufficient_candidates"
            view = client.get(f"/trips/{tid}/itineraries").json()
            assert view["plans"][0]["id"] == plan["id"] and view["voted"] == 1
    finally:
        for p in processes:
            p.terminate()
        for p in processes:
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait(timeout=5)
        for log in logs:
            log.close()

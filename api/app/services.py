"""Calls to the teammates' services. The only place the backend talks to them.

Each function sends the JSON from contracts/ and returns the JSON reply.
To switch from the mock to a real service, change the URL in .env — no code changes.
"""
import httpx
from fastapi import HTTPException

from . import config

# Tests put in-process transports here, keyed by base URL, so no server is needed.
TEST_TRANSPORTS: dict[str, httpx.AsyncBaseTransport] = {}


async def _post(base_url: str, path: str, payload: dict, service: str) -> dict:
    transport = TEST_TRANSPORTS.get(base_url)
    try:
        async with httpx.AsyncClient(base_url=base_url, timeout=config.SERVICE_TIMEOUT_SECONDS,
                                     transport=transport) as client:
            r = await client.post(path, json=payload)
            r.raise_for_status()
            return r.json()
    except httpx.HTTPStatusError as e:
        raise HTTPException(502, f"{service}_error_{e.response.status_code}")
    except httpx.HTTPError:
        raise HTTPException(503, f"{service}_unavailable")


async def recommend(payload: dict) -> dict:
    return await _post(config.RECOMMENDER_URL, "/recommend", payload, "recommender")


async def build_plans(payload: dict) -> dict:
    return await _post(config.OPTIMIZER_URL, "/plans", payload, "optimizer")


async def check_availability(payload: dict) -> dict:
    return await _post(config.AVAILABILITY_URL, "/availability/check", payload, "availability")


async def agent_turn(payload: dict) -> dict:
    return await _post(config.AGENT_URL, "/agent/turn", payload, "agent")

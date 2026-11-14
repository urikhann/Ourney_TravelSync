"""Calls to the teammates' services. The only place the backend talks to them.

Each function sends the JSON from contracts/ and returns the JSON reply.
To switch from the mock to a real service, change the URL in .env — no code changes.
"""
import httpx
from fastapi import HTTPException

from . import config

# Tests put in-process transports here, keyed by base URL, so no server is needed.
TEST_TRANSPORTS: dict[str, httpx.AsyncBaseTransport] = {}


async def _post(base_url: str, path: str, payload: dict, service: str, timeout: float | None = None,
                headers: dict | None = None) -> dict:
    transport = TEST_TRANSPORTS.get(base_url)
    try:
        async with httpx.AsyncClient(base_url=base_url, timeout=timeout or config.SERVICE_TIMEOUT_SECONDS,
                                     transport=transport, headers=headers) as client:
            r = await client.post(path, json=payload)
            r.raise_for_status()
            body = r.json()
            if not isinstance(body, dict):
                raise ValueError("object response required")
            return body
    except ValueError:
        raise HTTPException(502, f"{service}_invalid_response") from None
    except httpx.HTTPStatusError as e:
        if service == "planning_data":
            # Only forward our adapter's fixed codes; never expose provider bodies or credentials.
            allowed = {"routing_credentials_rejected", "routing_quota_exceeded",
                       "routing_provider_error", "routing_provider_unavailable",
                       "routing_invalid_response", "routing_no_routes", "routing_request_rejected",
                       "routing_start_point_unreachable", "routing_end_point_unreachable", "routing_cache_unavailable",
                       "kakao_rest_api_key_not_configured"}
            try:
                detail = e.response.json().get("detail")
            except (ValueError, AttributeError):
                detail = None
            if isinstance(detail, str) and detail in allowed:
                raise HTTPException(503, detail) from None
        raise HTTPException(502, f"{service}_error_{e.response.status_code}")
    except httpx.HTTPError:
        raise HTTPException(503, f"{service}_unavailable")


async def recommend(payload: dict) -> dict:
    return await _post(config.RECOMMENDER_URL, "/recommend", payload, "recommender")


async def planning_candidates(payload: dict) -> dict:
    return await _post(config.RECOMMENDER_URL, "/planning-candidates", payload,
                       "planning_candidates", config.PLANNING_DATA_TIMEOUT_SECONDS)


async def build_plans(payload: dict) -> dict:
    return await _post(config.OPTIMIZER_URL, "/plans", payload, "optimizer", config.OPTIMIZER_TIMEOUT_SECONDS)


async def planning_data(payload: dict) -> dict:
    if not config.PLANNING_DATA_URL:
        raise HTTPException(503, "planning_data_not_configured")
    return await _post(config.PLANNING_DATA_URL, "/snapshot", payload, "planning_data",
                       config.PLANNING_DATA_TIMEOUT_SECONDS,
                       {"Authorization": f"Bearer {config.PLANNING_DATA_TOKEN}"} if config.PLANNING_DATA_TOKEN else None)


async def check_availability(payload: dict) -> dict:
    return await _post(config.AVAILABILITY_URL, "/availability/check", payload, "availability")


async def agent_turn(payload: dict) -> dict:
    return await _post(config.AGENT_URL, "/agent/turn", payload, "agent")

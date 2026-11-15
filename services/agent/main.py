"""TravelSync agent service (port 8005): the real replacement for the mock /agent/turn.

Same contract as the mock (contracts/agent.request.json -> contracts/agent.response.json), so the backend only
needs AGENT_URL=http://localhost:8005. Uses Gemini when GEMINI_API_KEY is set, simple rules otherwise.
"""
import os

from fastapi import FastAPI, HTTPException
from fastapi.concurrency import run_in_threadpool

import brain

app = FastAPI(title="TravelSync agent", version="0.1.0")


@app.post("/agent/turn")
async def agent_turn(req: dict):
    if not isinstance(req.get("message"), dict) or not str(req["message"].get("text", "")).strip():
        raise HTTPException(422, "message.text is required")
    return await run_in_threadpool(brain.turn, req)  # Gemini calls are blocking; keep the server responsive


@app.get("/health")
def health():
    return {"ok": True, "brain": "gemini" if os.getenv("GEMINI_API_KEY") else "rules",
            "model": brain.GEMINI_MODEL if os.getenv("GEMINI_API_KEY") else None}

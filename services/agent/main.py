"""TravelSync agent service (port 8005): the real replacement for the mock /agent/turn.

Same contract as the mock (contracts/agent.request.json -> contracts/agent.response.json), so the backend only
needs AGENT_URL=http://localhost:8005. Groq is the default with Gemini and rules as backups.
"""
from fastapi import FastAPI, HTTPException
from fastapi.concurrency import run_in_threadpool

import brain

app = FastAPI(title="TravelSync agent", version="0.1.0")


@app.post("/agent/turn")
async def agent_turn(req: dict):
    if not isinstance(req.get("message"), dict) or not str(req["message"].get("text", "")).strip():
        raise HTTPException(422, "message.text is required")
    return await run_in_threadpool(brain.turn, req)  # Model calls are blocking; keep the server responsive


@app.get("/health")
def health():
    settings = brain.provider_settings()
    active, model = "rules", None
    if settings["enabled"]:
        active, model = settings["provider"], settings["model"]
    elif settings["gemini_backup"]:
        active, model = "gemini", brain.GEMINI_MODEL
    return {"ok": settings["configured"], "brain": active,
            "provider": settings["provider"], "model": model}

"""TravelSync backend.  Run:  uvicorn app.main:app --reload   ->  http://localhost:8000/docs"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import config
from .db import Base, SessionLocal, engine
from .routes import auth, itineraries, pois, preferences, recommendations, trips
from .seed import seed_if_empty

Base.metadata.create_all(engine)
with SessionLocal() as _db:
    seed_if_empty(_db)

app = FastAPI(title="TravelSync API", version="0.2.0",
              description="Backend for TravelSync. Contracts for the teammates' services live in /contracts.")
app.add_middleware(CORSMiddleware, allow_origins=[config.WEB_ORIGIN, "http://127.0.0.1:5173"],
                   allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

app.include_router(auth.router)
app.include_router(auth.me_router)
app.include_router(trips.router)
app.include_router(preferences.router)
app.include_router(recommendations.router)
app.include_router(pois.router)
app.include_router(itineraries.router)


@app.get("/meta/features", tags=["meta"])
def features():
    """Which optional parts are switched on. The web app hides what's off."""
    return {"availability": config.FEATURE_AVAILABILITY}


@app.get("/health", tags=["meta"])
def health():
    return {"ok": True, "services": {"recommender": config.RECOMMENDER_URL, "optimizer": config.OPTIMIZER_URL,
                                     "availability": config.AVAILABILITY_URL, "agent": config.AGENT_URL}}

# Next milestones: WebSocket chat, agent proposals, feedback.

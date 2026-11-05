"""Internal Module H service. Caller owns authentication and run access checks."""
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from ourney_travelsync.optimizer.api import optimize, optimize_run
from ourney_travelsync.optimizer.contracts import PlanRequest, PlanResponse, PlanRunRequest

app = FastAPI(title="TravelSync itinerary optimizer", version="1.0")


@app.exception_handler(RequestValidationError)
async def invalid_request(request: Request, exc: RequestValidationError):
    # Avoid echoing arbitrary request values or internal exception contexts.
    return JSONResponse(status_code=422, content={"error_code": "INVALID_INPUT", "errors": [
        {"loc": e["loc"], "type": e["type"]} for e in exc.errors()]})


@app.post("/plans", response_model=PlanResponse)
def plans(request: PlanRequest):
    return optimize(request)


@app.post("/runs", response_model=PlanResponse)
def runs(request: PlanRunRequest):
    return optimize_run(request)


@app.get("/health")
def health():
    return {"ok": True, "module": "H", "schema_version": "optimizer-1.0"}

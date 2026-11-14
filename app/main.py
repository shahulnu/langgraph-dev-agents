"""HTTP API. The portfolio site forwards requests here when CODE_REVIEW_ENGINE_URL is set.

Run locally: uv run uvicorn app.main:app --port 8090"""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app import live_limit, settings
from app.code_review.cases import find_case, load_cases
from app.code_review.graph import run_review
from app.code_review.reviewers import ModelDeclinedError, ReplayMissingError
from app.incident import agents as incident_agents
from app.incident.graph import investigate
from app.incident.world import list_incidents

app = FastAPI(title="LangGraph dev agents")


class ReviewRequest(BaseModel):
    caseId: str
    mode: str = "replay"


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    return forwarded.split(",")[0].strip() or (request.client.host if request.client else "unknown")


@app.get("/health")
def health():
    return {"status": "ok", "model": settings.MODEL}


@app.get("/api/code-review/cases")
def cases():
    return [{k: c[k] for k in ("id", "title", "author", "language", "teaser")} for c in load_cases()]


@app.get("/api/code-review/review")
def review_info():
    return {"liveAvailable": settings.live_available(), "engine": "python"}


@app.post("/api/code-review/review")
def review(body: ReviewRequest, request: Request):
    case = find_case(body.caseId)
    if case is None:
        return JSONResponse({"error": f'Unknown pull request "{body.caseId}".'}, status_code=404)

    mode, notice = "replay", None
    if body.mode == "live":
        if not settings.live_available():
            notice = "Live runs are off on this deployment, so this is a recorded live run."
        elif not live_limit.take(client_ip(request)):
            notice = f"You've reached today's limit of {settings.DAILY_LIMIT_PER_IP} live runs, so this is a recorded live run."
        else:
            mode = "live"

    try:
        result = run_review(case, mode)
    except ReplayMissingError as e:
        return JSONResponse({"error": str(e)}, status_code=404)
    except ModelDeclinedError as e:
        return JSONResponse({"error": str(e)}, status_code=422)
    return {**result, "notice": notice}


# ---------- Incident Debugger ----------

class InvestigateRequest(BaseModel):
    incidentId: str
    mode: str = "replay"


@app.get("/api/incident/incidents")
def incidents():
    return list_incidents()


@app.get("/api/incident/investigate")
def investigate_info():
    return {"liveAvailable": settings.live_available(), "engine": "python"}


@app.post("/api/incident/investigate")
async def investigate_incident(body: InvestigateRequest, request: Request):
    if body.incidentId not in {i["id"] for i in list_incidents()}:
        return JSONResponse({"error": f'Unknown incident "{body.incidentId}".'}, status_code=404)
    mode, notice = "replay", None
    if body.mode == "live":
        if not settings.live_available():
            notice = "Live runs are off on this deployment, so this is a recorded live run."
        elif not live_limit.take(client_ip(request)):
            notice = f"You've reached today's limit of {settings.DAILY_LIMIT_PER_IP} live runs, so this is a recorded live run."
        else:
            mode = "live"
    try:
        result = await investigate(body.incidentId, mode)
    except incident_agents.ReplayMissingError as e:
        return JSONResponse({"error": str(e)}, status_code=404)
    except incident_agents.ModelDeclinedError as e:
        return JSONResponse({"error": str(e)}, status_code=422)
    return {**result, "notice": notice}

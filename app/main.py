"""FastAPI backend: submit an incident, watch it run, and answer approval pauses.

Run: uvicorn app.main:app --reload
Runs are kept in memory, so use one worker process.
"""
import asyncio
import re

from fastapi import FastAPI, HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.graph.workflow import new_config, resume_ticket, start_ticket
from app.progress import COST, EVENTS, TOKENS, current_step, emit
from app.layer2_harness.subagents.browser_agent import SCREENSHOT_DIR
from app.schemas import Incident

app = FastAPI(title="Incident Resolver")

RUNS: dict[str, dict] = {}   # incident_id -> run record
_tasks: set[asyncio.Task] = set()
# Each Layer 2 run uses many model tokens per minute. Limit concurrent runs to avoid 429 rate-limit errors.
MAX_CONCURRENT_RUNS = 2
_slots = asyncio.Semaphore(MAX_CONCURRENT_RUNS)
RUN_TIMEOUT_SECONDS = 900  # a hung model call must not hold a slot forever


class Decision(BaseModel):
    approve: bool
    message: str | None = None


def _apply(run_id: str, state: dict) -> None:
    run = RUNS[run_id]
    run["route"] = state.get("route")
    run["layer1"] = jsonable_encoder(state.get("layer1"))
    run["rca"] = jsonable_encoder(state.get("rca"))
    # Per-agent evidence for the dashboard cards: {agent name: finding}.
    run["findings"] = {
        key.split("|")[1]: jsonable_encoder(value)
        for key, value in (state.get("completed_investigations") or {}).items()
    }
    run["resolution_status"] = state.get("resolution_status")
    if "__interrupt__" in state:
        run["status"] = "awaiting_approval"
        run["pending"] = jsonable_encoder(state["__interrupt__"][0].value)
    else:
        run["status"] = "done"
        run["pending"] = None
        emit(run_id, "done", "Finished")


async def _drive(run_id: str, work) -> None:
    try:
        async with _slots:  # wait for a free slot; the run stays "running" meanwhile
            emit(run_id, "status", "Started")
            _apply(run_id, await asyncio.wait_for(work, RUN_TIMEOUT_SECONDS))
    except TimeoutError:
        message = f"timed out after {RUN_TIMEOUT_SECONDS // 60} minutes"
        RUNS[run_id].update(status="failed", error=message, pending=None)
        emit(run_id, "error", f"Failed: {message}")
    except Exception as exc:  # keep the run visible instead of losing it
        message = str(exc) or type(exc).__name__
        RUNS[run_id].update(status="failed", error=message, pending=None)
        emit(run_id, "error", f"Failed: {message[:300]}")


def _background(run_id: str, work) -> None:
    task = asyncio.create_task(_drive(run_id, work))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


def _public(run: dict) -> dict:
    run_id = run["incident"]["incident_id"]
    view = {k: v for k, v in run.items() if k != "config"}
    view["events"] = EVENTS.get(run_id, [])
    view["tokens"] = TOKENS.get(run_id, 0)
    view["cost_usd"] = round(COST.get(run_id, 0.0), 4)
    return view


@app.post("/incidents", status_code=202)
async def submit(incident: Incident):
    if incident.incident_id in RUNS:
        raise HTTPException(409, f"{incident.incident_id} already exists")
    config = new_config()
    RUNS[incident.incident_id] = {
        "incident": incident.model_dump(mode="json"), "status": "running", "route": None,
        "layer1": None, "rca": None, "findings": {}, "pending": None, "error": None, "config": config,
    }
    emit(incident.incident_id, "status", "Queued")
    _background(incident.incident_id, start_ticket(incident, config))
    return {"incident_id": incident.incident_id, "status": "running"}


@app.get("/incidents")
async def list_incidents():
    return [{**_public(run), "current_step": current_step(run["incident"]["incident_id"])} for run in RUNS.values()]


@app.get("/incidents/{incident_id}")
async def get_incident(incident_id: str):
    run = RUNS.get(incident_id)
    if run is None:
        raise HTTPException(404, "unknown incident")
    return _public(run)


@app.get("/incidents/{incident_id}/screenshot")
async def get_screenshot(incident_id: str):
    """Serve the browser agent's evidence screenshot. The name is rebuilt from the ID, never taken from a path."""
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", incident_id)
    path = (SCREENSHOT_DIR / f"{safe}.jpg").resolve()
    if SCREENSHOT_DIR.resolve() not in path.parents or not path.is_file():
        raise HTTPException(404, "no screenshot for this incident")
    return FileResponse(path, media_type="image/jpeg")


@app.post("/incidents/{incident_id}/decision", status_code=202)
async def decide(incident_id: str, decision: Decision):
    run = RUNS.get(incident_id)
    if run is None:
        raise HTTPException(404, "unknown incident")
    if run["status"] != "awaiting_approval":
        raise HTTPException(409, f"incident is {run['status']}, not awaiting approval")
    if decision.approve:
        payload = {"decisions": [{"type": "approve"}]}
    else:
        payload = {"decisions": [{"type": "reject", "message": decision.message or "Rejected by reviewer."}]}
    emit(incident_id, "approval", "Approved" if decision.approve else "Rejected")
    run.update(status="running", pending=None)
    _background(incident_id, resume_ticket(run["config"], payload))
    return {"incident_id": incident_id, "status": "running"}

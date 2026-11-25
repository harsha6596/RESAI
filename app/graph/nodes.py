"""Graph nodes: triage, Layer 1, Layer 2 investigation, and human approval."""
from langgraph.types import interrupt

from app.graph.state import IncidentState
from app.layer1_rag.agent import run_layer1
from app.layer2_harness.adaptive import execute, plan, resolve
from app.progress import emit

# Fields that tie a ticket to live transaction data. Tickets with these need Layer 2.
LIVE_FIELDS = ("vin", "rdr_id", "submission_id")


def triage_node(state: IncidentState) -> dict:
    """Simple rule: a ticket about a live transaction goes to Layer 2; a general question goes to Layer 1."""
    inc = state["incident"]
    live = inc.reported_status is not None or any(getattr(inc, f) for f in LIVE_FIELDS)
    if live:
        emit(inc.incident_id, "triage", "Routed to Layer 2: the ticket names a live transaction (VIN, RDR ID, submission or status)")
    else:
        emit(inc.incident_id, "triage", "Routed to Layer 1: general question with no transaction identifiers")
    return {"route": "layer2" if live else "layer1"}


def layer1_node(state: IncidentState) -> dict:
    inc = state["incident"]
    result = run_layer1(inc)
    if result.resolved:
        emit(inc.incident_id, "layer1", f"Answered from the knowledge base ({len(result.sources)} source(s))")
    else:
        emit(inc.incident_id, "layer1", "No confident answer in the knowledge base. Escalating to Layer 2")
    return {"layer1": result}


async def layer2_orchestrator_node(state: IncidentState) -> dict:
    """Plan or replan; this node never invokes a specialist directly."""
    inc = state["incident"]
    emit(inc.incident_id, "layer2", "Orchestrator planning/replanning")
    decision = await plan(inc, state)
    return {"investigation_plan": decision}


async def layer2_execute_node(state: IncidentState) -> dict:
    """Execute the selected independent investigations in parallel."""
    return await execute(state["incident"], state)


async def layer2_resolution_node(state: IncidentState) -> dict:
    """Apply the safe resolution path and hand approval requests to the graph."""
    result = await resolve(state["incident"], state)
    if result.get("pending_interrupt"):
        payload = result["pending_interrupt"]
        requests = payload.get("action_requests", [])
        names = ", ".join(r.get("name", "change") for r in requests) or "a change"
        emit(state["incident"].incident_id, "approval", f"Waiting for your approval: {names}")
    return result


def approval_node(state: IncidentState) -> dict:
    """Pause the whole graph until a human approves or rejects the pending change."""
    decision = interrupt(state["pending_interrupt"])
    return {"decision": decision}

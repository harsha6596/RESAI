"""Incident workflow: triage -> Layer 1 (simple) or Layer 2 (complex), with a human approval loop.

Run a ticket:  python -m app.graph.workflow
"""
import asyncio
import uuid

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from app.graph import nodes
from app.graph.state import IncidentState
from app.schemas import Incident


def _after_triage(state: IncidentState) -> str:
    return state["route"]


def _after_layer1(state: IncidentState) -> str:
    return "done" if state["layer1"].resolved else "layer2"


def _after_orchestrator(state: IncidentState) -> str:
    decision = state["investigation_plan"].decision
    return "execute" if decision == "investigate" else "resolve"


def _after_resolution(state: IncidentState) -> str:
    return "approval" if state.get("pending_interrupt") else "done"


def build_graph():
    g = StateGraph(IncidentState)
    g.add_node("triage", nodes.triage_node)
    g.add_node("layer1", nodes.layer1_node)
    g.add_node("layer2_orchestrator", nodes.layer2_orchestrator_node)
    g.add_node("layer2_execute", nodes.layer2_execute_node)
    g.add_node("layer2_resolution", nodes.layer2_resolution_node)
    g.add_node("approval", nodes.approval_node)

    g.add_edge(START, "triage")
    g.add_conditional_edges("triage", _after_triage, {"layer1": "layer1", "layer2": "layer2_orchestrator"})
    g.add_conditional_edges("layer1", _after_layer1, {"done": END, "layer2": "layer2_orchestrator"})
    g.add_conditional_edges("layer2_orchestrator", _after_orchestrator, {"execute": "layer2_execute", "resolve": "layer2_resolution"})
    g.add_edge("layer2_execute", "layer2_orchestrator")
    g.add_conditional_edges("layer2_resolution", _after_resolution, {"done": END, "approval": "approval"})
    g.add_edge("approval", "layer2_resolution")
    # Allow our own pydantic types to be restored from the checkpoint (LangGraph otherwise warns).
    allowed = [
        ("app.schemas", name)
        for name in ("RdrStatus", "Incident", "Layer1Result", "Rca", "InvestigationPlan", "InvestigationRequest", "WriteRequest")
    ]
    serde = JsonPlusSerializer(allowed_msgpack_modules=allowed)
    return g.compile(checkpointer=InMemorySaver(serde=serde))


GRAPH = build_graph()


def new_config() -> dict:
    return {"configurable": {"thread_id": str(uuid.uuid4())}}


async def start_ticket(incident: Incident, config: dict) -> dict:
    """Run until done or until an approval pause. Pause shows as state["__interrupt__"]."""
    return await GRAPH.ainvoke({"incident": incident}, config=config)


async def resume_ticket(config: dict, decision: dict) -> dict:
    """Continue a paused ticket with the human decision."""
    return await GRAPH.ainvoke(Command(resume=decision), config=config)


async def run_ticket(incident: Incident, approve=None) -> dict:
    """Run one ticket to completion. `approve(payload) -> decision` answers any approval pause.

    Returns the final state. Its `rca` (Layer 2) or `layer1` (Layer 1) holds the resolution.
    """
    config = new_config()
    state = await start_ticket(incident, config)
    while "__interrupt__" in state:
        payload = state["__interrupt__"][0].value
        decision = approve(payload) if approve else {"decisions": [{"type": "reject", "message": "No approver."}]}
        state = await resume_ticket(config, decision)
    return state


def _ask(payload) -> dict:
    print("\n[PAUSED FOR HUMAN APPROVAL]")
    print(payload)
    choice = input("\nApprove action? (y/n): ").strip().lower()
    if choice == "y":
        return {"decisions": [{"type": "approve"}]}
    return {"decisions": [{"type": "reject", "message": "Action denied by engineer."}]}


if __name__ == "__main__":
    sample = Incident(
        incident_id="INC-001",
        title="RDR Transaction Stuck in Pending Status",
        application="RDR",
        vin="4T1R11AK6TA900101",
        dealer_id="DLR-1042",
        rdr_id="RDR-20261123-008421",
        submission_id="SUB-20261123-000184",
        reported_status="PENDING",
        incident_description="RDR remained in PENDING status. Dashboard displays Processing delayed.",
        support_request="Investigate why the RDR is still Pending and recommend a resolution.",
    )
    final = asyncio.run(run_ticket(sample, approve=_ask))
    print("\n--- Route:", final["route"])
    if final.get("layer1"):
        print("Layer 1:", final["layer1"].model_dump_json(indent=2))
    if final.get("rca"):
        print("Layer 2 RCA:", final["rca"].model_dump_json(indent=2))

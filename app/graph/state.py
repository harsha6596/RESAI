"""Shared IncidentState for the LangGraph workflow."""
from typing import Any, Literal, TypedDict

from app.schemas import Incident, InvestigationPlan, Layer1Result, Rca


class IncidentState(TypedDict, total=False):
    incident: Incident
    route: Literal["layer1", "layer2"]          # set by triage
    layer1: Layer1Result                        # set when Layer 1 ran
    inner_thread: str                           # DeepAgents thread id for the Layer 2 run
    pending_interrupt: Any                      # approval request from db-agent, while paused
    decision: Any                               # human decision, set on resume
    rca: Rca                                    # final Layer 2 resolution
    investigation_plan: InvestigationPlan       # latest orchestrator decision
    completed_investigations: dict[str, dict]    # blackboard: task key -> concise finding
    findings: list[dict]                         # consolidated findings for replanning
    investigation_round: int                    # starts at zero, maximum two by default
    resolution_status: Literal["not_started", "pending_approval", "resolved", "escalated"]

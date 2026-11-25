"""Layer 2 orchestrator: a DeepAgents supervisor that plans, delegates to subagents, and synthesizes the RCA."""
import asyncio
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()

from deepagents import create_deep_agent  # noqa: E402
from langchain.agents.middleware import AgentMiddleware  # noqa: E402
from langchain_core.messages import ToolMessage  # noqa: E402
from langgraph.checkpoint.memory import InMemorySaver  # noqa: E402
from langgraph.types import Command  # noqa: E402

from app.config import planner_model  # noqa: E402
from app.progress import ProgressHandler  # noqa: E402
from app.layer2_harness.subagents.browser_agent import create_browser_subagent  # noqa: E402
from app.layer2_harness.subagents.db_agent import db_subagent  # noqa: E402
from app.layer2_harness.subagents.docs_agent import docs_subagent  # noqa: E402
from app.layer2_harness.subagents.logs_agent import logs_subagent  # noqa: E402
from app.schemas import Incident, Rca  # noqa: E402

ORCHESTRATOR_PROMPT = """You are the Layer 2 Autonomous Incident Orchestrator for RDR support.
Use this bounded workflow and keep the reasoning concise:
1. Incident understanding: extract the ticket identifiers, symptom, status and requested outcome.
2. Evidence collection: in ONE assistant turn, delegate the independent checks needed for this ticket
   (logs, database, docs and browser). The task calls may run in parallel. Do not delegate the same
   investigation again after it returns; the shared incident blackboard already contains its report.
3. Root-cause identification: compare the returned reports. Stop collecting evidence once the cause is
   supported; do not ask agents to restate or refine the same check.
4. Remediation planning: recommend the smallest safe next step. Never claim a write unless the DB agent
   reports APPLIED and human approval was obtained.
5. Health verification and resolution: use fresh browser evidence when a change was approved; otherwise
   report the remaining pending checks and escalate.

Delegate with the task() tool. Always set subagent_type to exactly one of logs-agent, db-agent, docs-agent or browser-agent. Never use general-purpose. Only these subagents exist:
   - logs-agent: application logs, stack traces, trace IDs
   - db-agent: RDR database checks. Python pre-fetches the record from the VIN, RDR ID or submission ID you pass. Its update_rdr_record tool pauses for human approval. Always include the VIN, RDR ID and submission ID from the ticket in the task() description.
   - docs-agent: runbooks and status/error catalog. Its results are context, not proof. Delegate it at most once; it performs one semantic retrieval.
   - browser-agent: reads the RDR Support Console dashboard for a VIN, dealer ID or RDR ID (read-only). For every live ticket containing a VIN, dealer ID, RDR ID, submission ID, or reported status, delegate browser-agent once to verify the current UI row. It reports state only and never diagnoses the cause. If the browser is unavailable, record that as a pending check.
   Call the agents needed by the ticket, including browser-agent for every live ticket as described above. DB is conditional: use it when the issue involves transaction state, data validation, a submission record, or a requested correction. You must call task() at least once. Never write the RCA before the required subagents have returned their reports.
Do not issue follow-up task() calls for a different wording of an already completed check. If a task result says it was reused, use that result and continue.
6. Synthesize the subagent reports into the RCA. Follow these rules:
   - Base the root cause on logs-agent and db-agent evidence. docs-agent and browser-agent results never make a cause confirmed.
   - root_cause_status "confirmed": logs show the cause directly, and the database state agrees where a database check was needed.
   - "probable": logs point to a cause, but a required check is still missing.
   - "undetermined": evidence is missing or contradictory. Say what is missing.
   - recommended_action: the safe next step. Describe any data change as a proposal. Say a change was made only if db-agent reported write_result APPLIED.
   - pending_checks: every check a subagent said is still needed, plus any check you could not run.
   - incident_id: copy the exact incident ID from the ticket into the RCA.
   - Use only facts a subagent reported. Do not guess identifiers or invent events.
"""


def _config(thread_id: str, run_id: str) -> dict:
    # Keep a runaway supervisor/subagent loop from repeatedly resending the
    # growing tool transcript and consuming unbounded model tokens. Normal
    # investigations finish well below this limit after the subagent guards.
    return {
        "configurable": {"thread_id": thread_id},
        "recursion_limit": 20,
        "callbacks": [ProgressHandler(run_id)],
    }


# deepagents adds a general-purpose subagent with host filesystem tools by default. It searched the
# host machine instead of the RDR data, so replace it with a stub that cannot do any work.
GENERAL_PURPOSE_STUB = {
    "name": "general-purpose",
    "description": "Not for incident work. Do not use. Use logs-agent, db-agent, docs-agent or browser-agent instead.",
    "system_prompt": "You do not investigate incidents. Reply only: use logs-agent, db-agent, docs-agent or browser-agent.",
    "tools": [],
}


@dataclass
class InvestigationEntry:
    status: str = "running"
    result: str = ""
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class IncidentBlackboard:
    """Small per-thread blackboard for completed and in-flight specialist work."""

    incident_id: str
    incident_details: dict = field(default_factory=dict)
    investigations: dict[str, InvestigationEntry] = field(default_factory=dict)
    pending_investigations: list[str] = field(default_factory=list)
    root_cause: str | None = None
    remediation: str | None = None
    verification_status: str = "not_started"


_BLACKBOARDS: dict[str, IncidentBlackboard] = {}
_BLACKBOARD_LOCK = asyncio.Lock()


def _incident_id_from_state(state) -> str:
    text = "\n".join(str(m.content) for m in state.get("messages", []) if getattr(m, "content", None))
    match = re.search(r"\bINC-[A-Z0-9-]+\b", text, re.IGNORECASE)
    return match.group(0).upper() if match else "unknown-incident"


def _scope_key(target: str, args: dict) -> str:
    """Create a stable investigation key from target plus ticket identifiers.

    Descriptions are deliberately excluded: changing prose must not bypass the duplicate guard.
    """
    description = str(args.get("description", ""))
    identifiers = re.findall(
        r"\b(?:INC-[A-Z0-9-]+|RDR-\d{8}-\d{6}|SUB-\d{8}-\d{6}|DLR-\d+|[A-HJ-NPR-Z0-9]{17})\b",
        description,
        flags=re.IGNORECASE,
    )
    incident_ids = sorted({item.upper() for item in identifiers if item.upper().startswith("INC-")})
    # The incident ID is the canonical scope. This allows a VIN-based request and a
    # submission-ID-based request for the same ticket to reuse one investigation.
    scope = incident_ids or sorted({item.upper() for item in identifiers})
    return f"{target}|{'|'.join(scope)}"


def _blackboard_scope(request, state) -> str:
    runtime = getattr(request, "runtime", None)
    config = getattr(runtime, "config", {}) if runtime else {}
    configurable = config.get("configurable", {}) if isinstance(config, dict) else {}
    return str(configurable.get("thread_id") or _incident_id_from_state(state))


def _board_for_thread(thread_id: str, incident: Incident) -> IncidentBlackboard:
    board = _BLACKBOARDS.setdefault(
        thread_id,
        IncidentBlackboard(
            incident_id=incident.incident_id,
            incident_details=incident.model_dump(mode="json"),
        ),
    )
    return board


class DelegationGuardMiddleware(AgentMiddleware):
    """Blackboard and hard budget around the orchestrator task tool."""

    LIMITS = {"logs-agent": 1, "db-agent": 1, "browser-agent": 1, "docs-agent": 1}
    MAX_TOTAL = 4

    def _task_count(self, state, target: str) -> int:
        count = 0
        for message in state.get("messages", []):
            for call in getattr(message, "tool_calls", None) or []:
                if call.get("name") != "task":
                    continue
                args = _tool_call_args(call)
                if args.get("subagent_type") == target:
                    count += 1
        return count

    def _total_task_count(self, state) -> int:
        return sum(self._task_count(state, target) for target in self.LIMITS)

    def wrap_tool_call(self, request, handler):
        return self._handle(request, handler)

    async def awrap_tool_call(self, request, handler):
        return await self._handle_async(request, handler)

    def _blocked(self, request, target: str):
        label = target.upper()
        return ToolMessage(
            content=(
                f"{label}_DELEGATION_LIMIT_REACHED: {target} was already delegated for this "
                "incident. Do not retry it; use its existing report or record the failed check "
                "as a pending check."
            ),
            tool_call_id=request.tool_call["id"],
            name="task",
        )

    def _blackboard_message(self, request, key: str, entry: InvestigationEntry):
        status = "already completed" if entry.status == "completed" else "already in progress"
        content = (
            f"BLACKBOARD_REUSE: {key} is {status}. Reuse this investigation result and continue; "
            f"do not retry it. Cached result: {entry.result[:500]}"
        )
        return ToolMessage(content=content, tool_call_id=request.tool_call["id"], name="task")

    @staticmethod
    def _record_result(entry: InvestigationEntry, result) -> None:
        entry.status = "completed"
        entry.result = str(getattr(result, "content", result))
        entry.updated_at = datetime.now(timezone.utc).isoformat()

    def _before(self, request):
        target = _tool_call_args(request.tool_call).get("subagent_type")
        if target not in self.LIMITS:
            return None
        if self._total_task_count(request.state) >= self.MAX_TOTAL:
            return self._blocked(request, target)
        key = _scope_key(target, _tool_call_args(request.tool_call))
        scope = _blackboard_scope(request, request.state)
        board = _BLACKBOARDS.setdefault(scope, IncidentBlackboard(_incident_id_from_state(request.state)))
        entry = board.investigations.get(key)
        if entry is not None:
            return self._blackboard_message(request, key, entry)
        board.investigations[key] = InvestigationEntry()
        return None

    def _handle(self, request, handler):
        if request.tool_call["name"] == "task":
            args = _tool_call_args(request.tool_call)
            target = args.get("subagent_type")
            if target in self.LIMITS:
                decision = self._before(request)
                if decision is not None:
                    return decision
                key = _scope_key(target, args)
                scope = _blackboard_scope(request, request.state)
                try:
                    result = handler(request)
                except Exception:
                    _BLACKBOARDS[scope].investigations.pop(key, None)
                    raise
                self._record_result(_BLACKBOARDS[scope].investigations[key], result)
                return result
        return handler(request)

    async def _handle_async(self, request, handler):
        if request.tool_call["name"] == "task":
            args = _tool_call_args(request.tool_call)
            target = args.get("subagent_type")
            if target in self.LIMITS:
                decision = self._before(request)
                if decision is not None:
                    return decision
                key = _scope_key(target, args)
                scope = _blackboard_scope(request, request.state)
                try:
                    result = await handler(request)
                except Exception:
                    _BLACKBOARDS[scope].investigations.pop(key, None)
                    raise
                self._record_result(_BLACKBOARDS[scope].investigations[key], result)
                return result
        return await handler(request)

_agent_instance = None
_agent_lock = asyncio.Lock()
_browser_available = False


async def _agent():
    """One agent per process, so a paused run can be resumed on the same checkpointer.
    The browser subagent is created here once. If it cannot start (for example RDR_EMAIL is not set),
    the other subagents still run, and the failure is logged."""
    global _agent_instance, _browser_available
    async with _agent_lock:
        if _agent_instance is None:
            subagents = [GENERAL_PURPOSE_STUB, logs_subagent, db_subagent, docs_subagent]
            try:
                browser_subagent, _browser = await create_browser_subagent()
                subagents.append(browser_subagent)
                _browser_available = True
            except Exception as exc:
                _browser_available = False
                print(f"browser-agent not available: {exc}", flush=True)
            _agent_instance = create_deep_agent(
                model=planner_model(),
                system_prompt=ORCHESTRATOR_PROMPT,
                subagents=subagents,
                response_format=Rca,
                middleware=[DelegationGuardMiddleware()],
                checkpointer=InMemorySaver(),
            )
    return _agent_instance


def _tool_call_args(call: dict) -> dict:
    args = call.get("args")
    if isinstance(args, dict):
        return args
    raw = call.get("arguments")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _delegated_agents(result: dict) -> set[str]:
    agents = set()
    for message in result.get("messages", []):
        for call in getattr(message, "tool_calls", None) or []:
            if call.get("name") == "task":
                subagent = _tool_call_args(call).get("subagent_type")
                if isinstance(subagent, str):
                    agents.add(subagent)
    return agents


def _check_delegated(result: dict, *, require_browser: bool = False) -> dict:
    """Reject an RCA that skipped required evidence collection."""
    if "structured_response" in result:
        agents = _delegated_agents(result)
        if not agents:
            raise RuntimeError("Orchestrator wrote an RCA without delegating to any subagent. Run rejected.")
        if require_browser and "browser-agent" not in agents:
            availability = "available" if _browser_available else "unavailable"
            raise RuntimeError(
                f"Orchestrator wrote an RCA without the compulsory browser-agent verification "
                f"(browser-agent is {availability}). Run rejected."
            )
    return result


def _normalize_rca(result: dict, incident_id: str) -> dict:
    """Restore trusted ticket metadata that the model may omit from its RCA."""
    rca = result.get("structured_response")
    # The ticket ID is authoritative. This also prevents a hallucinated or
    # stale model-supplied ID from attaching the RCA to another incident.
    if isinstance(rca, Rca) and rca.incident_id != incident_id:
        result = dict(result)
        result["structured_response"] = rca.model_copy(update={"incident_id": incident_id})
    return result


async def investigate(incident: Incident, thread_id: str) -> dict:
    """Run the investigation. Returns the agent result: structured_response, or __interrupt__ when paused for approval."""
    board = _board_for_thread(thread_id, incident)
    agent = await _agent()
    result = await agent.ainvoke(
        {"messages": [{"role": "user", "content": incident.as_text()}]},
        config=_config(thread_id, incident.incident_id),
    )
    normalized = _normalize_rca(
        _check_delegated(
            result,
            require_browser=incident.reported_status is not None or any(
                getattr(incident, field) for field in ("vin", "rdr_id", "submission_id")
            ),
        ),
        incident.incident_id,
    )
    _update_blackboard(board, normalized)
    return normalized


async def resume_investigation(thread_id: str, decision: dict, run_id: str) -> dict:
    """Resume a paused run with the human decision, e.g. {"decisions": [{"type": "approve"}]}."""
    agent = await _agent()
    result = await agent.ainvoke(Command(resume=decision), config=_config(thread_id, run_id))
    normalized = _normalize_rca(_check_delegated(result, require_browser=True), run_id)
    board = _BLACKBOARDS.get(thread_id)
    if board is not None:
        _update_blackboard(board, normalized)
    return normalized


def _update_blackboard(board: IncidentBlackboard, result: dict) -> None:
    """Record lifecycle state without copying the full model transcript."""
    if "__interrupt__" in result:
        board.verification_status = "pending_approval"
        board.pending_investigations.append("human approval for requested database change")
        return
    rca = result.get("structured_response")
    if isinstance(rca, Rca):
        board.root_cause = rca.root_cause
        board.remediation = rca.recommended_action
        board.pending_investigations = list(rca.pending_checks)
        board.verification_status = "pending_checks" if rca.pending_checks else "resolved"

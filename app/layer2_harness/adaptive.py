"""Small adaptive plan-and-execute Layer 2 workflow.

The orchestrator plans only. Python owns specialist execution, deduplication and
the two-round limit; this prevents the old supervisor task loop from expanding
indefinitely.
"""
import asyncio
import json
import time
from datetime import datetime, timezone
from typing import Any

from deepagents import create_deep_agent
from langchain.agents import create_agent
from langchain_core.messages import HumanMessage
from app.config import planner_model, worker_model
from app.layer2_harness.subagents.browser_agent import create_browser_subagent, read_ui_row
from app.layer2_harness.subagents.db_agent import db_subagent
from app.layer2_harness.subagents.docs_agent import docs_subagent
from app.layer2_harness.subagents.logs_agent import logs_subagent
from app.progress import ProgressHandler, emit
from app.schemas import Incident, InvestigationPlan, InvestigationRequest, Rca
from app.tools.db_tools import prefetch_record, update_rdr_record
from app.tools.log_tools import investigate_logs

MAX_ROUNDS = 2
MAX_SUBAGENT_CALLS = 6

PLANNER_PROMPT = """You are the Layer 2 planning and analysis orchestrator for RDR support.
You never call subagents yourself. Return exactly one InvestigationPlan.

On the first pass, select only the independent checks needed for this ticket:
- browser-agent is compulsory for every live ticket and reports current UI state only;
- logs-agent is needed for application or processing failures;
- db-agent is needed for transaction, validation, submission or data-state questions;
- docs-agent is optional supporting evidence and may be selected at most once.

On later passes, inspect the concise completed findings. Request another investigation only
when a specific new fact is required and has not already been collected. Otherwise choose
resolve or escalate. Never request the same agent twice for the same incident.

Root cause must be based on logs/database evidence. Browser and docs are supporting evidence.
Choose resolve only when the evidence is sufficient and the recommended action is safe. Choose
escalate when evidence is missing, contradictory, or the two-round limit has been reached.
Never invent identifiers, events, database values or a successful write.

The input includes `ticket_guidance`, computed from the database. Follow it: it states what the
row means and which decision fits. Copy its concrete facts into root_cause and recommended_action.
If `final_pass` is true you MUST choose resolve or escalate (never investigate) and must fully
write root_cause and recommended_action; never leave the placeholder text.

root_cause_status must be exactly one of: confirmed, probable, undetermined.

Write a resolution that is specific to THIS ticket. Always quote the concrete facts the evidence
gives (status, transaction_state, retry_count, last_error, log event names, service names) and say
which agent reported them. Never reuse generic wording such as "further investigation needed"
without naming what is missing. Use the evidence like this:
- Logs show the cause and the DB agrees: confirmed. Name the failing service/event and the fix.
- Only the DB row (no log file for this ticket): at most probable, and only when the row itself
  explains the state. State plainly that no log evidence exists for this transaction, and ask for it.
- DB row by state, when logs are missing:
  * PENDING, retry_count 0, last_error "Processing in progress": normal in-flight processing. Not a
    fault. Advise the dealer to wait and re-check; escalate only if it stays PENDING past the SLA.
  * RETRYING with retry_count >= 1 and "Temporary processing delay": the platform is retrying
    automatically. Advise waiting for the next retry; do not resubmit; escalate if retries are exhausted.
  * FAILED, last_error "Processing failed": the failure reason is not in the row. Cause undetermined;
    request worker logs for the RDR ID and do not claim a cause. Retry count shows attempts so far.
  * REJECTED with transaction_state NOT_CREATED, "Submission validation failed": the submission was
    rejected before an RDR was created. The row does not name the invalid field; the dealer must
    correct the submission, and support needs the validation log to name the field.
  * BLOCKED with NOT_CREATED, "Vehicle information unavailable": vehicle verification could not
    complete. Probable dependency/vehicle-data problem; verify the vehicle master service and the VIN.
- Choose decision "resolve" only when the cause and a safe action are both supported by evidence.
  Otherwise choose "escalate" and list in pending_checks exactly what to collect.
"""

def _playbook(rows: list[dict], has_logs: bool) -> str:
    """Ticket-specific guidance computed from the DB row, so dashboard-only tickets get a real answer."""
    if not rows or "error" in rows[0]:
        return "No database record was found for this ticket."
    row = rows[0]
    status, state, retries = row.get("status"), row.get("transaction_state"), row.get("retry_count") or 0
    error = row.get("last_error") or "none"
    facts = f"DB row: status={status}, transaction_state={state}, retry_count={retries}, last_error={error}."
    if has_logs:
        return facts + " Log evidence exists for this transaction; base the root cause on it."
    note = " No log file mentions this transaction, so the cause cannot be confirmed from logs."
    if status == "PENDING" and retries == 0:
        return facts + " Normal in-flight processing, not a fault: no retries and no error beyond 'in progress'. Advise the dealer to wait and re-check; escalate only if it stays PENDING past the processing SLA. Decision: resolve." + note
    if status == "RETRYING":
        return facts + f" The platform is retrying automatically ({retries} retries so far). Advise the dealer not to resubmit and to wait for the next retry; escalate if retries are exhausted." + note
    if status == "FAILED":
        return facts + " The failure reason is not stored in the row, so the cause is undetermined. Request the worker logs for this RDR ID; do not name a cause. Decision: escalate." + note
    if status == "REJECTED":
        return facts + " Rejected before an RDR was created. The dealer must correct the submission; the row does not name the invalid field, so support needs the validation log to identify it. Decision: escalate." + note
    if status == "BLOCKED":
        return facts + " Blocked because vehicle information could not be verified. Probable vehicle-data or dependency issue; check the vehicle master service and the VIN. Decision: escalate." + note
    return facts + note


_planner = None
_planner_lock = asyncio.Lock()
_worker_agents: dict[str, Any] = {}
_worker_lock = asyncio.Lock()


def _live(incident: Incident) -> bool:
    return incident.reported_status is not None or any(
        getattr(incident, field) for field in ("vin", "rdr_id", "submission_id")
    )


def _task_key(incident: Incident, agent: str, scope: str = "initial") -> str:
    return f"{incident.incident_id}|{agent}|{scope}"


def _finding(incident: Incident, state: dict, agent: str) -> dict:
    prefix = f"{incident.incident_id}|{agent}|"
    for key, value in state.get("completed_investigations", {}).items():
        if key.startswith(prefix) and value.get("status") == "completed" and isinstance(value.get("report"), dict):
            return value["report"]
    return {}


def _logs_and_db_agree(incident: Incident, state: dict) -> bool:
    """True when the logs agent confirms a cause with cited evidence and the DB agent found the record."""
    logs, db = _finding(incident, state, "logs-agent"), _finding(incident, state, "db-agent")
    return bool(
        logs.get("root_cause_status") == "confirmed"
        and logs.get("classification") not in (None, "unknown")
        and logs.get("evidence")
        and db.get("record_found")
    )


async def _get_planner():
    global _planner
    async with _planner_lock:
        if _planner is None:
            # The planner only reads the findings and returns an InvestigationPlan. A plain agent keeps the
            # input to a few thousand tokens; the DeepAgents default prompt and tools added about 14K per call.
            _planner = create_agent(
                model=planner_model(),
                system_prompt=PLANNER_PROMPT,
                response_format=InvestigationPlan,
            )
    return _planner


async def plan(incident: Incident, state: dict) -> InvestigationPlan:
    """Use the same structured orchestrator for initial planning and replanning."""
    planner = await _get_planner()
    context = {
        "incident": incident.as_text(),
        "round": state.get("investigation_round", 0),
        "max_rounds": MAX_ROUNDS,
        "completed_investigations": state.get("completed_investigations", {}),
        "findings": state.get("findings", []),
    }
    ids = {k: v for k, v in (("vin", incident.vin), ("rdr_id", incident.rdr_id), ("submission_id", incident.submission_id)) if v}
    rows = prefetch_record(ids)
    log_hit = bool(investigate_logs.invoke({"identifiers": list(ids.values())}).get("files")) if ids else False
    context["ticket_guidance"] = _playbook(rows, log_hit)
    # After the first evidence round the planner must conclude; it may not ask for more.
    context["final_pass"] = context["round"] >= 1
    result = await planner.ainvoke(
        {"messages": [{"role": "user", "content": json.dumps(context, default=str)}]},
        config={"recursion_limit": 6, "callbacks": [ProgressHandler(incident.incident_id)]},
    )
    decision = result.get("structured_response")
    if not isinstance(decision, InvestigationPlan):
        raise RuntimeError("Orchestrator did not return a valid InvestigationPlan")

    # A small planner model sometimes hedges ("probable") when the logs name the cause and the DB row agrees.
    # Apply the rule from the planner prompt in Python so the outcome does not depend on the model's nerve.
    if state.get("investigation_round", 0) >= 1 and decision.decision != "investigate" and decision.write_request is None:
        if _logs_and_db_agree(incident, state):
            decision.root_cause_status = "confirmed"
            decision.decision = "resolve"

    # Without any log evidence a cause cannot be "confirmed"; the DB row alone supports "probable" at most.
    if not log_hit and decision.root_cause_status == "confirmed":
        decision.root_cause_status = "probable"

    # Enforce non-negotiable policy in Python rather than trusting the model.
    chosen = {item.agent: item for item in decision.investigations}
    first_pass = state.get("investigation_round", 0) == 0
    if _live(incident) and decision.decision != "escalate":
        required = {
            "browser-agent": "Verify the current live RDR UI row.",
            # The planner model skipped these on some runs, so INC-002 lost its log evidence.
            # Both are cheap and report insufficient_evidence when nothing matches.
            "logs-agent": "Search the application logs for this transaction.",
            "db-agent": "Read the current database record for this transaction.",
        }
        for name, purpose in required.items():
            if name in chosen or (name != "browser-agent" and not first_pass):
                continue
            if name != "browser-agent" and any(k.startswith(f"{incident.incident_id}|{name}|") for k in state.get("completed_investigations", {})):
                continue
            decision.investigations.append(InvestigationRequest(agent=name, purpose=purpose))
        decision.decision = "investigate" if first_pass else decision.decision
    if state.get("investigation_round", 0) >= MAX_ROUNDS and decision.decision == "investigate":
        decision.decision = "escalate"
        decision.investigations = []
        decision.pending_checks.append("Investigation round limit reached.")
    return decision


async def _get_worker(agent: str):
    """Return a reusable non-browser worker.

    Browser workers are intentionally created per investigation in _run_one so
    every incident gets a fresh visible headful browser session.
    """
    async with _worker_lock:
        if agent == "browser-agent":
            raise RuntimeError("browser-agent workers must be created per investigation")
        spec = {
            "logs-agent": logs_subagent,
            "db-agent": db_subagent,
            "docs-agent": docs_subagent,
        }[agent]
        if agent not in _worker_agents:
            _worker_agents[agent] = create_deep_agent(
                model=worker_model(),
                system_prompt=spec["system_prompt"],
                tools=spec.get("tools"),
                middleware=spec.get("middleware", ()),
                interrupt_on=spec.get("interrupt_on"),
                response_format=spec.get("response_format"),
            )
        return _worker_agents[agent]


async def _run_one(incident: Incident, request, round_number: int) -> tuple[str, dict]:
    """Run one specialist, emit start/finish events and record how long it took."""
    name = request.agent if hasattr(request, "agent") else request["agent"]
    emit(incident.incident_id, "agent_start", f"{name} started")
    started = time.monotonic()
    agent, finding = await _run_one_inner(incident, request, round_number)
    finding["seconds"] = round(time.monotonic() - started, 1)
    emit(incident.incident_id, "agent_done", f"{agent} {finding['status']} in {finding['seconds']}s")
    return agent, finding


async def _run_one_inner(incident: Incident, request, round_number: int) -> tuple[str, dict]:
    agent = request.agent if hasattr(request, "agent") else request["agent"]
    purpose = request.purpose if hasattr(request, "purpose") else request["purpose"]
    browser = None
    if agent == "browser-agent" and (incident.vin or incident.rdr_id or incident.dealer_id):
        # Fixed, visible login + search steps. Fall back to the model-driven agent only if they fail.
        try:
            report = await read_ui_row(incident.vin, incident.rdr_id, incident.dealer_id, incident.incident_id)
            return agent, {"status": "completed", "round": round_number, "report": report.model_dump(mode="json")}
        except Exception as exc:
            emit(incident.incident_id, "layer2", f"Fixed browser check failed ({str(exc)[:120]}); using the browser agent")
    if agent == "browser-agent":
        # create_browser_subagent explicitly launches headless=False and primes
        # the RDR login page. A fresh instance avoids stale/closed-page reuse.
        spec, browser = await create_browser_subagent()
        worker = create_deep_agent(
            model=worker_model(),
            system_prompt=spec["system_prompt"],
            tools=spec.get("tools"),
            middleware=spec.get("middleware", ()),
            interrupt_on=spec.get("interrupt_on"),
            response_format=spec.get("response_format"),
        )
    else:
        worker = await _get_worker(agent)
    prefetched = ""
    if agent == "logs-agent":
        # Search the logs in Python with the ticket's own identifiers. The model used to pick
        # identifiers itself and sometimes searched with the wrong ones, finding nothing.
        ids = [v for v in (incident.rdr_id, incident.submission_id, incident.vin) if v]
        if ids:
            evidence = investigate_logs.invoke({"identifiers": ids})
            prefetched = (
                "PREFETCHED LOG EVIDENCE (searched by Python with the ticket identifiers; "
                "do not call investigate_logs again unless this is empty):\n"
                + json.dumps(evidence, default=str)[:16000] + "\n\n"
            )
    if agent == "browser-agent":
        # Pick the lookup field in Python. The model used the RDR ID for rejected submissions,
        # which have none, and reported NOT_FOUND for a row that exists.
        if incident.vin:
            lookup = f"Filter with #filter-vin = {incident.vin} and press the Search button (button.search-button)."
        elif incident.rdr_id:
            lookup = f"Filter with #filter-rdr = {incident.rdr_id} and press the Search button (button.search-button)."
        elif incident.dealer_id:
            lookup = f"Filter with #filter-dealer = {incident.dealer_id} and press the Search button (button.search-button)."
        else:
            lookup = "No identifier is available; report NOT_FOUND."
        prefetched = f"LOOKUP INSTRUCTION: {lookup} Use only this one identifier.\n\n"
    task = (
        f"Incident report:\n{incident.as_text()}\n\n{prefetched}"
        f"Your bounded purpose: {purpose}\n"
        "Return only your configured structured report. Use one tool call where your instructions permit it."
    )
    try:
        result = await worker.ainvoke(
            {"messages": [HumanMessage(content=task)]},
            # The browser agent needs separate login, filter, extract and screenshot steps, each a
            # model turn plus a tool turn. A limit of 8 cut it off mid-run, so the grid check failed.
            config={"recursion_limit": 30 if agent == "browser-agent" else 12,
                    "callbacks": [ProgressHandler(incident.incident_id)]},
        )
        if "__interrupt__" in result:
            return agent, {"status": "pending_approval", "interrupt": result["__interrupt__"]}
        report = result.get("structured_response")
        if agent == "browser-agent" and hasattr(report, "observed_at"):
            # The model invented timestamps (e.g. 2023-10-05). The time of observation is ours to set.
            report.observed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        return agent, {
            "status": "completed",
            "round": round_number,
            "report": report.model_dump(mode="json") if hasattr(report, "model_dump") else str(report),
        }
    except Exception as exc:
        return agent, {"status": "failed", "round": round_number, "error": str(exc)[:300]}
    finally:
        if browser is not None:
            await browser.close()


async def execute(incident: Incident, state: dict) -> dict:
    """Run the plan's new investigations concurrently and update the state blackboard."""
    plan_value = state.get("investigation_plan")
    requests = plan_value.investigations if isinstance(plan_value, InvestigationPlan) else []
    completed = dict(state.get("completed_investigations", {}))
    unique = []
    for request in requests:
        agent = request.agent
        key = _task_key(incident, agent, request.scope)
        # The model writes free text into `scope`, so the exact key never repeats. Never run the
        # same agent twice for one incident: that wasted a round and re-opened a browser.
        docs_already_used = any(item.startswith(f"{incident.incident_id}|{agent}|") for item in completed)
        if key not in completed and not docs_already_used and all(
            _task_key(incident, existing.agent, existing.scope) != key for existing in unique
        ):
            unique.append(request)
    if len(completed) + len(unique) > MAX_SUBAGENT_CALLS:
        unique = unique[: max(0, MAX_SUBAGENT_CALLS - len(completed))]

    round_number = int(state.get("investigation_round", 0)) + 1
    results = await asyncio.gather(*[_run_one(incident, item, round_number) for item in unique])
    for agent, finding in results:
        request = next(item for item in unique if item.agent == agent)
        completed[_task_key(incident, agent, request.scope)] = finding
    findings = [{"agent": key.split("|")[1], **value} for key, value in completed.items()]
    emit(incident.incident_id, "layer2", f"Evidence round {round_number} completed ({len(results)} specialist check(s))")
    return {
        "completed_investigations": completed,
        "findings": findings,
        "investigation_round": round_number,
    }


async def resolve(incident: Incident, state: dict) -> dict:
    """Finalize a safe recommendation and require current UI evidence before resolution."""
    decision = state.get("investigation_plan")
    if not isinstance(decision, InvestigationPlan):
        return {"resolution_status": "escalated"}

    # The existing graph approval node supplies `decision` after an interrupt.
    if decision.write_request is not None and state.get("decision") is None:
        request = decision.write_request
        return {
            "pending_interrupt": {
                "action_requests": [{
                    "name": "update_rdr_record",
                    "description": f"Update {request.field} for VIN {request.vin}: {request.reason}",
                }]
            },
            "resolution_status": "pending_approval",
        }
    fresh_findings = {}
    if decision.write_request is not None and state.get("decision") is not None:
        approved = any(item.get("type") == "approve" for item in state["decision"].get("decisions", []))
        request = decision.write_request
        if not approved or request.expected_row_version is None:
            return {"resolution_status": "escalated", "pending_interrupt": None}
        result = update_rdr_record(
            dcr_id=incident.incident_id,
            vin=request.vin,
            field=request.field,
            new_value=request.new_value,
            expected_old_value=request.expected_old_value,
            expected_row_version=request.expected_row_version,
            reason=request.reason,
        )
        if result.get("status") != "APPLIED":
            decision.pending_checks.append("Guarded database correction was not applied.")
            return {"resolution_status": "escalated", "pending_interrupt": None}
        # Never treat the pre-change UI observation as health verification.
        agent, fresh = await _run_one(
            incident,
            {"agent": "browser-agent", "scope": "verification", "purpose": "Freshly verify the RDR UI row after the approved change."},
            int(state.get("investigation_round", 0)),
        )
        fresh_findings[_task_key(incident, agent, "verification")] = fresh

    # Keys end with a scope the model chooses, so match on the agent name, not the exact key.
    prefix = f"{incident.incident_id}|browser-agent|"
    pool = fresh_findings or state.get("completed_investigations", {})
    browser_finding = next((v for k, v in pool.items() if k.startswith(prefix)), None)
    report = browser_finding.get("report", {}) if browser_finding else {}
    verified = (
        browser_finding
        and browser_finding.get("status") == "completed"
        and isinstance(report, dict)
        and report.get("result") == "MATCH"
    )
    status = "resolved" if verified and decision.decision == "resolve" else "escalated"
    rca = Rca(
        incident_id=incident.incident_id,
        root_cause_status=decision.root_cause_status,
        root_cause=decision.root_cause,
        recommended_action=decision.recommended_action,
        pending_checks=decision.pending_checks if status != "resolved" else [],
    )
    emit(incident.incident_id, "layer2", f"Resolution outcome: {status}")
    output = {"rca": rca, "resolution_status": status, "pending_interrupt": None, "decision": None}
    if fresh_findings:
        output["completed_investigations"] = {
            **state.get("completed_investigations", {}),
            **fresh_findings,
        }
    return output

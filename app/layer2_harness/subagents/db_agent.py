"""db_agent subagent: RDR database checks and HITL-gated corrections for data change requests (DCR).

Python pre-fetches the record before the LLM runs (DbPrefetchMiddleware). The LLM only compares the
ticket with the row and, for a requested fix, calls update_rdr_record (paused for human approval).
"""
import json
from pathlib import Path

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import HumanMessage

from app.schemas import DbDiagnosis
from app.tools.db_tools import extract_ids, prefetch_record, query_readonly, update_rdr_record

ROOT = Path(__file__).resolve().parents[3]  # repo root


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


class DbPrefetchMiddleware(AgentMiddleware):
    """Before the first model call: find IDs in the task text, read the matching row, add it as a message."""

    def _prefetch_message(self, state) -> dict:
        task_text = "\n".join(str(m.content) for m in state["messages"] if getattr(m, "type", "") == "human")
        rows = prefetch_record(extract_ids(task_text))
        if rows:
            body = "PREFETCHED RECORD (read by Python, do not re-query):\n" + json.dumps(rows, default=str)
        else:
            body = ("PREFETCHED RECORD: none (no VIN, RDR ID or submission ID found in the task). "
                    "Use query_readonly once, with a single SELECT on rdr_records.")
        return {"messages": [HumanMessage(content=body)]}

    def before_agent(self, state, runtime):
        return self._prefetch_message(state)

    async def abefore_agent(self, state, runtime):
        return self._prefetch_message(state)


db_subagent = {
    "name": "db-agent",
    "description": (
        "Checks a data change request (DCR) or stuck transaction against the RDR database. The record is "
        "pre-fetched. update_rdr_record applies one guarded field correction and pauses for HITL approval."
    ),
    "system_prompt": "\n\n".join([
        _read("context/subagents/db-agent/AGENTS.md"),
        _read("context/skills/db-investigation/SKILL.md"),
    ]),
    "tools": [query_readonly, update_rdr_record],
    "middleware": [DbPrefetchMiddleware()],
    # DeepAgents pauses the run on this call; resume with approve/reject decisions.
    "interrupt_on": {"update_rdr_record": True},
    "response_format": DbDiagnosis,
}

"""logs_agent subagent: one call to investigate_logs, then a report. No file tools."""
from deepagents.middleware.filesystem import FilesystemMiddleware

from app.schemas import LogsReport
from app.tools.log_tools import investigate_logs, list_log_files

logs_subagent = {
    "name": "logs-agent",
    "description": "Analyzes application log files for errors, trace IDs, and failure patterns. Read-only.",
    "system_prompt": (
        "You are a read-only log analysis specialist. Follow context/subagents/logs-agent/AGENTS.md.\n"
        "1. Call investigate_logs ONCE with the incident identifiers (RDR ID, submission ID, VIN, trace or correlation IDs).\n"
        "2. Write the report from its result only. Quote counts, line numbers and timestamps exactly as returned.\n"
        "3. If it returns no files, report insufficient_evidence. Do not call it again with guessed values.\n"
        "4. root_cause_status: use \"confirmed\" when an ERROR event in the followed trace states the failure that explains the "
        "symptom (cite its event name, service, timestamp and line). Use \"probable\" only when the logs show related warnings "
        "but no event that states the failure. Use \"insufficient_evidence\" when no log matches.\n"
        "Never invent events, IDs or counts."
    ),
    "tools": [investigate_logs, list_log_files],
    # Keep only read_file (the framework requires it). Removes ls, write, edit and delete, which wasted calls.
    "middleware": [FilesystemMiddleware(tools=["read_file"])],
    "response_format": LogsReport,
}

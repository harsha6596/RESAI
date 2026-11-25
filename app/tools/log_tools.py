"""log_tools: read-only access to local log files under logs/.

investigate_logs is the one tool the logs agent uses. It does all the searching in Python and returns
a short summary, so the model makes one call instead of dozens.
"""
import json
from collections import Counter
from pathlib import Path

from langchain_core.tools import tool

LOG_ROOT = (Path(__file__).resolve().parents[2] / "logs").resolve()
MAX_TRACE_IDS = 3       # trace IDs followed per file
MAX_TRACE_EVENTS = 40   # events kept per trace. 12 cut off the retry, DLQ and alert events, so the agent could only say "probable"
MAX_HITS = 5            # first matching lines kept per file


def _records(target: Path):
    """Yield (line_number, raw_text, parsed_json_or_None) for each line of a log file."""
    with open(target, encoding="utf-8", errors="replace") as f:
        for n, line in enumerate(f, 1):
            text = line.rstrip("\n")
            try:
                rec = json.loads(text)
            except ValueError:
                rec = None
            yield n, text, rec if isinstance(rec, dict) else None


def _summarize_file(target: Path, identifiers: list[str]) -> dict | None:
    """One pass finds identifier hits and error counts and picks trace IDs. A second pass collects the trace events."""
    needles = [i.lower() for i in identifiers]
    hits, total, counts, traces = [], 0, Counter(), []
    for n, text, rec in _records(target):
        if any(needle in text.lower() for needle in needles):
            total += 1
            if len(hits) < MAX_HITS:
                hits.append({"line": n, "text": text[:200]})
            if rec and rec.get("trace.id") and rec.get("severity") in ("ERROR", "WARN") and rec["trace.id"] not in traces:
                if len(traces) < MAX_TRACE_IDS:
                    traces.append(rec["trace.id"])
        if rec and rec.get("severity") in ("ERROR", "WARN"):
            counts[f"{rec['severity']} {rec.get('event.name', '?')}"] += 1
    if total == 0:
        return None

    events = []
    if traces:
        for n, text, rec in _records(target):
            if rec and rec.get("trace.id") in traces and len(events) < MAX_TRACE_EVENTS * len(traces):
                events.append({
                    "line": n, "time": rec.get("@timestamp"), "trace": rec.get("trace.id"),
                    "service": rec.get("service.name"), "event": rec.get("event.name"),
                    "severity": rec.get("severity"), "message": str(rec.get("message", ""))[:160],
                })
    return {
        "file": str(target.relative_to(LOG_ROOT)),
        "total_matches": total,
        "first_matches": hits,
        "error_warn_by_event": dict(counts.most_common(10)),
        "trace_ids_followed": traces,
        "trace_events": events,
    }


@tool
def investigate_logs(identifiers: list[str]) -> dict:
    """Search every log file for the incident's identifiers (RDR ID, submission ID, VIN, trace ID) and summarize.
    Returns, per file that matches: total matches, first matching lines, ERROR/WARN counts by event,
    and the event sequence for up to 3 trace IDs found in those errors. Read-only. One call is enough."""
    identifiers = [str(i).strip() for i in identifiers if str(i).strip()]
    if not identifiers:
        return {"error": "Give at least one identifier, such as an RDR ID, submission ID or VIN."}
    files = []
    for target in sorted(LOG_ROOT.rglob("*.log")):
        if target.is_file():
            summary = _summarize_file(target, identifiers)
            if summary:
                files.append(summary)
    if not files:
        return {"files": [], "note": "No log file contains these identifiers. Report insufficient_evidence."}
    return {"files": files}


@tool
def list_log_files() -> list[str]:
    """List every log file available under logs/."""
    return sorted(str(p.relative_to(LOG_ROOT)) for p in LOG_ROOT.rglob("*.log") if p.is_file())

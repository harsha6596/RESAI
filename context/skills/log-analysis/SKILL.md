# log-analysis

---
name: log-analysis
description: Evidence-based, read-only investigation of application logs for RDR and other workflows, using one investigate_logs call.
---

# Log Analysis Skill

## When to use
An incident asks why a transaction failed, stayed pending, timed out, or showed a mismatch.

## Steps
1. Call `investigate_logs` once with every identifier in the ticket: RDR ID, submission ID, VIN, dealer ID, and any trace or correlation ID.
2. Read the result:
   - `files`: one entry per log file that matched.
   - `total_matches` and `first_matches`: where the identifiers appear, with line numbers.
   - `error_warn_by_event`: ERROR and WARN counts per event across the whole file.
   - `trace_events`: the event sequence for the trace IDs taken from the errors, in file order. Use it for the timeline.
3. If `files` is empty, report `insufficient_evidence`.
4. Separate the symptom (what the user sees), the failure (the first ERROR), and the cause (only if the logs show it directly).

## Rules
- Quote numbers, line numbers and timestamps exactly as returned.
- A last error is not automatically the root cause. Check the events before it.
- Do not call `investigate_logs` again with guessed values. One call is enough.
- Report confirmed only when the logs show the cause directly. Otherwise probable or undetermined.

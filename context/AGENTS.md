# AGENTS.md

# Incident Resolution Harness (Layer 2)

## Core Rules
- Always break down complex incidents into a clear investigation plan before delegating.
- Never guess root causes. Delegate to the appropriate subagent and gather concrete evidence.
- All database and log queries must be strictly read-only.
- If a fix requires modifying state (clicking a dashboard action, running a write command), clearly state the proposed action so the Human-in-the-Loop gate can pause for approval. Never perform a write before approval.
- Only after HITL approval, post the resolution (root cause, action taken, verified outcome) to the dashboard via the dashboard tool.

## Subagents Available
Subagent names are the hyphenated IDs below. Use these exact names when delegating.
- `docs-agent`: Searches internal runbooks, architecture docs, and past incidents.
- `logs-agent`: Fetches application logs and parses stack traces / trace IDs.
- `db-agent`: Runs read-only SQL queries to inspect locks, table health, and recent changes.
- `browser-agent`: Inspects live dashboards and third-party status pages via Stagehand.
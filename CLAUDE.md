# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Status

Early stage. Most of the repo is design docs. Only `main.py` exists as code, and it is a stub that prints a greeting. `README.md`, `requirements.txt` and `.env` are empty. Treat `architecture.md` as the target design, not a description of existing code.

## Commands

- Python: 3.13 (`.python-version`, `requires-python >=3.13`).
- Run the stub: `python main.py`
- There is no build, lint, test, or dependency config yet. `pyproject.toml` has `dependencies = []`. Do not assume pytest, ruff, or uv are set up. Check before adding any of them.

## Planned architecture (from `architecture.md` and `project_architecture/layer_2_harness.png`)

The system is an autonomous incident-resolution pipeline with two layers:

1. **Layer 1 (RAG, fast path):** `layer1_rag/triage.py` routes each incident as "simple" or "complex". `layer1_rag/retriever.py` runs vector search over ChromaDB and checks confidence. High-confidence matches resolve here.
2. **Layer 2 (DeepAgents harness, complex incidents):** a LangGraph supervisor (`layer2_harness/orchestrator.py`) plans the investigation and spawns subagents in `layer2_harness/subagents/`: docs, browser (Stagehand), logs, and db. Each subagent returns findings to the supervisor. Synthesis then aggregates evidence and drafts a root cause and fix.
3. **Graph flow** (`graph/workflow.py`): Triage → L1 → L2 → Approval → Verify → Publish. Approval uses a LangGraph `interrupt` for human-in-the-loop sign-off, and only when the fix requires a write. Read-only resolutions skip approval. Publish posts to the dashboard (`tools/dashboard_tools.py`). Then the verified solution is written to episodic memory so Layer 1 can answer the same incident next time.

Cross-cutting pieces:

- **Shared state:** one `IncidentState` TypedDict in `graph/state.py`, holding short-term memory, a findings log, and the active trace.
- **Persistence:** `AsyncSqliteSaver` checkpoints in `data/checkpoints.db` hold state and HITL pauses. ChromaDB in `data/vector_store/` has two collections: `docs` (semantic) and `incidents` (episodic).
- **Agent context:** `context/` holds `AGENTS.md` (global rules and subagent roles) and `context/skills/*/SKILL.md`. DeepAgents loads these natively through its Memory and Skills middleware. Edit these files to change agent behavior, not the Python code.
- **Safety:** `tools/guardrails.py` enforces SELECT-only SQL and audit-logs tool calls. Every DB path must go through it. The DB agent is read-only.
- **API:** `app/main.py` (FastAPI) triggers an incident and resumes HITL approval.

## Discrepancies to resolve

- Layer 2 folder skeleton now exists under `app/` and `context/`. Files are placeholders (docstrings or headings only, no logic). Guardrails (`tools/guardrails.py`), Layer 1, memory, graph workflow, and FastAPI are not scaffolded yet.
- `db_tools.py` has no SELECT-only enforcement until guardrails lands. Do not wire it to a live DB before then.
- Root `main.py` is still the stub. `app/main.py` (FastAPI) is not created yet.
- `data/` and `.env` are now in `.gitignore`.
- Dependencies (DeepAgents, LangGraph, langchain-stagehand, ChromaDB, FastAPI) are not yet declared in `pyproject.toml` or `requirements.txt`.

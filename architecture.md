incident-resolver/
├── .env
├── pyproject.toml
├── context/                            # Loaded natively by DeepAgents MemoryMiddleware & SkillsMiddleware
│   ├── AGENTS.md                       # Global harness rules & subagent roles
│   └── skills/
│       ├── log-analysis/SKILL.md       # Progressive skill for log investigation
│       ├── db-investigation/SKILL.md   # Progressive skill for read-only DB checks
│       └── browser-check/SKILL.md      # Progressive skill for Stagehand status/dashboard checks
├── data/                               # Local persistence (gitignored)
│   ├── checkpoints.db                  # AsyncSqliteSaver (state + HITL pauses)
│   └── vector_store/                   # Local ChromaDB (docs + past incidents)
└── app/
    ├── main.py                         # FastAPI app (trigger incident & resume HITL approval)
    ├── config.py                       # Env settings, model names, confidence thresholds
    ├── schemas.py                      # Pydantic models for incoming alerts & dashboard output
    │
    ├── memory/
    │   ├── checkpointer.py             # AsyncSqliteSaver setup
    │   └── vector_store.py             # ChromaDB: "docs" (Semantic) & "incidents" (Episodic)
    │
    ├── graph/
    │   ├── state.py                    # Shared IncidentState TypedDict
    │   ├── nodes.py                    # Approval (interrupt), Verify, and Publish nodes
    │   └── workflow.py                 # Compiles Triage → L1 → L2 → Approval → Verify → Publish
    │
    ├── layer1_rag/
    │   ├── triage.py                   # Lightweight router ("simple" vs "complex")
    │   └── retriever.py                # Vector search + confidence check
    │
    ├── layer2_harness/
    │   ├── orchestrator.py             # DeepAgents supervisor (wires Memory, Skills & SubAgents)
    │   └── subagents/
    │       ├── docs_agent.py           # Searches docs & past incidents
    │       ├── browser_agent.py        # langchain-stagehand (StagehandMiddleware + local Chrome)
    │       ├── logs_agent.py           # Parses logs & trace IDs
    │       └── db_agent.py             # Runs read-only DB inspections
    │
    └── tools/
        ├── guardrails.py               # Boundaries: SELECT-only SQL check & tool audit logging
        ├── docs_tools.py               # Vector search tools
        ├── log_tools.py                # Log fetching functions
        ├── db_tools.py                 # Read-only DB query functions
        └── dashboard_tools.py          # Posts resolution to dashboard
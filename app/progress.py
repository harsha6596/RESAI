"""Live progress per incident: a short event list and a token count, fed by stage events and LangChain callbacks."""
import time
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler

MAX_EVENTS = 200
EVENTS: dict[str, list[dict]] = {}   # incident_id -> events, oldest first
TOKENS: dict[str, int] = {}          # incident_id -> total model tokens used so far
COST: dict[str, float] = {}          # incident_id -> estimated model spend in USD
BY_MODEL: dict[str, dict[str, dict[str, int]]] = {}   # incident_id -> model -> {"in": n, "out": n}

# USD per 1M tokens: (input, output, cache read). Cache writes bill at 1.25x input.
PRICES = {
    "haiku-5-5": (0.10, 0.50, 0.01),
    "sonnet-5-5": (2.00, 10.00, 0.10),
    "opus-5-5": (4.00, 20.00, 0.20),
}


def _price(model: str):
    for key, price in PRICES.items():
        if key in (model or ""):
            return price
    return (0.0, 0.0, 0.0)   # unknown model: counted in tokens, not in dollars

TOOL_AGENT = {
    "investigate_logs": "logs-agent", "list_log_files": "logs-agent",
    "query_readonly": "db-agent", "list_tables": "db-agent", "table_health": "db-agent", "apply_change": "db-agent",
    "search_docs": "docs-agent", "search_kb": "layer1",
    "run": "browser-agent", "snapshot": "browser-agent", "screenshot": "browser-agent",
}


def _short(text: Any, limit: int = 160) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[:limit] + "…"


def emit(run_id: str, stage: str, message: str) -> None:
    events = EVENTS.setdefault(run_id, [])
    if len(events) < MAX_EVENTS:
        events.append({"time": time.strftime("%H:%M:%S"), "stage": stage, "message": _short(message, 300)})


def current_step(run_id: str) -> str | None:
    events = EVENTS.get(run_id) or []
    return events[-1]["message"] if events else None


class ProgressHandler(BaseCallbackHandler):
    """Attach to a run's config. Reports which agent is working and how many tokens the model used."""

    def __init__(self, run_id: str):
        self.run_id = run_id
        self._names: dict[Any, str] = {}   # tool call run_id -> tool name, so errors can be labelled

    def on_tool_start(self, serialized, input_str, *, run_id, inputs=None, **kwargs):
        name = (serialized or {}).get("name") or kwargs.get("name") or "tool"
        self._names[run_id] = name
        args = inputs or {}
        if name == "task":
            emit(self.run_id, "agent", f"Orchestrator → {args.get('subagent_type', 'subagent')}: {_short(args.get('description', ''), 120)}")
        elif name in TOOL_AGENT:
            emit(self.run_id, "tool", f"{TOOL_AGENT[name]}: {name} {_short(input_str, 100)}")

    def on_tool_error(self, error, *, run_id, **kwargs):
        name = self._names.get(run_id, "tool")
        emit(self.run_id, "error", f"{name} failed: {_short(error, 200)}")

    def on_llm_end(self, response, **kwargs):
        for generations in response.generations:
            for gen in generations:
                message = getattr(gen, "message", None)
                usage = getattr(message, "usage_metadata", None) or {}
                TOKENS[self.run_id] = TOKENS.get(self.run_id, 0) + int(usage.get("total_tokens", 0))
                meta = getattr(message, "response_metadata", None) or {}
                model = str(meta.get("model_name") or meta.get("model") or "")
                total_in, out = int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0))
                details = usage.get("input_token_details") or {}
                read, write = int(details.get("cache_read", 0)), int(details.get("cache_creation", 0))
                p_in, p_out, p_read = _price(model)
                COST[self.run_id] = COST.get(self.run_id, 0.0) + (
                    (total_in - read - write) * p_in + write * p_in * 1.25 + read * p_read + out * p_out
                ) / 1_000_000
                bucket = BY_MODEL.setdefault(self.run_id, {}).setdefault(model or "unknown", {"in": 0, "out": 0})
                bucket["in"] += total_in
                bucket["out"] += out

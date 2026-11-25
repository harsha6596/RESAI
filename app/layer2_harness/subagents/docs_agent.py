"""docs_agent subagent: one symptom-based retrieval over the RDR knowledge base."""
import re
from pathlib import Path

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage

from app.schemas import DocsReport
from app.tools.docs_tools import search_docs

ROOT = Path(__file__).resolve().parents[3]  # repo root


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


class DocsSearchLimitMiddleware(AgentMiddleware):
    """Allow one symptom-based retrieval and block identifier-driven follow-ups."""

    MAX_SEARCHES = 1

    _IDENTIFIER = re.compile(
        r"\b(?:INC-[A-Z0-9-]+|RDR-\d{8}-\d{6}|SUB-\d{8}-\d{6}|DLR-\d+|"
        r"[A-HJ-NPR-Z0-9]{17})\b",
        re.IGNORECASE,
    )

    @classmethod
    def _semantic_query(cls, query: str) -> str:
        """Remove ticket and transaction identifiers before the docs lookup."""
        cleaned = cls._IDENTIFIER.sub(" ", query or "")
        cleaned = " ".join(cleaned.split())
        return cleaned or "RDR incident documented guidance for the reported submission issue"

    def _count_searches(self, state) -> int:
        count = 0
        for message in state.get("messages", []):
            for call in getattr(message, "tool_calls", None) or []:
                if call.get("name") == "search_docs":
                    count += 1
        return count

    def wrap_tool_call(self, request, handler):
        if request.tool_call["name"] != "search_docs":
            return handler(request)
        if self._count_searches(request.state) >= self.MAX_SEARCHES:
            return ToolMessage(
                content=(
                    "SEARCH_LIMIT_REACHED: one documentation retrieval has already been "
                    "used. Synthesize the findings now; do not search again."
                ),
                tool_call_id=request.tool_call["id"],
                name="search_docs",
            )
        call = dict(request.tool_call)
        args = dict(call.get("args") or {})
        args["query"] = self._semantic_query(str(args.get("query", "")))
        args["top_k"] = min(int(args.get("top_k", 4)), 4)
        return handler(request.override(tool_call={**call, "args": args}))

    async def awrap_tool_call(self, request, handler):
        if request.tool_call["name"] != "search_docs":
            return await handler(request)
        if self._count_searches(request.state) >= self.MAX_SEARCHES:
            return ToolMessage(
                content=(
                    "SEARCH_LIMIT_REACHED: one documentation retrieval has already been "
                    "used. Synthesize the findings now; do not search again."
                ),
                tool_call_id=request.tool_call["id"],
                name="search_docs",
            )
        call = dict(request.tool_call)
        args = dict(call.get("args") or {})
        args["query"] = self._semantic_query(str(args.get("query", "")))
        args["top_k"] = min(int(args.get("top_k", 4)), 4)
        return await handler(request.override(tool_call={**call, "args": args}))


docs_subagent = {
    "name": "docs-agent",
    "description": (
        "Searches the RDR knowledge base (runbooks, status/error catalog, architecture guide) using hybrid "
        "dense + keyword search. Returns cited passages with a relevance label. Read-only."
    ),
    "system_prompt": "\n\n".join([
        _read("context/subagents/docs-agent/AGENTS.md"),
        _read("context/skills/docs-search/SKILL.md"),
    ]),
    "tools": [search_docs],
    "middleware": [DocsSearchLimitMiddleware()],
    "response_format": DocsReport,
}

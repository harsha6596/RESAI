"""Focused docs-agent integration check.

Run from the repository root with:
    uv run python test/test_docs_agent.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from deepagents import create_deep_agent
from dotenv import load_dotenv

from app.config import worker_model
from app.layer2_harness.subagents.docs_agent import docs_subagent

load_dotenv()


PROMPT = """Use docs-agent only for INC-002.
Find the documented validation or status/error guidance for a rejected RDR
submission for VIN 4T1R11AK8TA900102 and dealer DLR-2077. Use one combined
search first, then synthesize a concise DocsReport. Do not use another agent.
"""


async def main():
    agent = create_deep_agent(
        model=worker_model(),
        system_prompt="Delegate this request only to docs-agent and return its report.",
        subagents=[docs_subagent],
    )
    calls = []
    final = None
    async for namespace, chunk in agent.astream(
        {"messages": [{"role": "user", "content": PROMPT}]},
        config={"recursion_limit": 20},
        stream_mode="updates",
        subgraphs=True,
    ):
        for update in chunk.values():
            for message in update.get("messages", []) if isinstance(update, dict) else []:
                final = message
                calls.extend(
                    call["name"]
                    for call in (getattr(message, "tool_calls", None) or [])
                    if call["name"] == "search_docs"
                )
    print(f"search_docs calls: {len(calls)}")
    assert len(calls) == 1, f"docs-agent did not perform exactly one retrieval: {len(calls)}"
    if final is not None:
        print(final.content)


if __name__ == "__main__":
    asyncio.run(main())

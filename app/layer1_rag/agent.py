"""Layer 1 agent: LangChain create_agent over the business knowledge base. Advice only, never writes."""

from functools import lru_cache

from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain_core.tools import tool
from langgraph.errors import GraphRecursionError

from app.config import layer1_model
from app.layer1_rag.retriever import index_ready, search
from app.progress import ProgressHandler
from app.schemas import Incident, Layer1Result

load_dotenv()

SYSTEM_PROMPT = """You are the Layer 1 RDR support assistant. You answer straightforward questions from the business knowledge base only.
Rules:
- Call search_kb first, using the key terms of the incident. Call it at most twice (a second time only with different wording), then answer.
- Answer only from the returned passages. Cite each source exactly as shown in its header, for example policies/SAMPLE_policy.md#0.
- Quote rates, incentive amounts and legal wording exactly as written. Never calculate, extrapolate or add advice beyond the text.
- For rates and incentives, name the source file and say the figures may have changed.
- If search_kb returns NO_CONFIDENT_MATCH or INDEX_NOT_BUILT, or the question needs live data (transaction status, logs, database rows, dealer-specific records), set resolved to false and say what is needed.
- Never change data. You only advise.
- Never use the manufacturer's brand name from your own knowledge. Use only the wording of the passages; say "the manufacturer" if a name is needed."""


@tool
def search_kb(query: str) -> str:
    """Search the business knowledge base (policies, product knowledge, finance rates, incentives, compliance, operations, training, FAQ). Returns cited passages, or NO_CONFIDENT_MATCH."""
    if not index_ready():
        return "INDEX_NOT_BUILT: run `python -m app.layer1_rag.retriever` first."
    hits = search(query)
    if not hits:
        return "NO_CONFIDENT_MATCH: no passage is close enough to answer this."
    return "\n\n".join(f"[{h['source']}#{h['chunk']}] (distance {h['distance']:.2f})\n{h['text']}" for h in hits)


@lru_cache(maxsize=1)
def build_agent():
    return create_agent(
        model=layer1_model(),
        tools=[search_kb],
        system_prompt=SYSTEM_PROMPT,
        response_format=Layer1Result,
    )


def run_layer1(incident: Incident) -> Layer1Result:
    # The step limit stops a search loop from re-sending a growing transcript (one run used 111K tokens).
    config = {"callbacks": [ProgressHandler(incident.incident_id)], "recursion_limit": 12}
    try:
        out = build_agent().invoke({"messages": [{"role": "user", "content": incident.as_text()}]}, config=config)
    except GraphRecursionError:
        return Layer1Result(resolved=False, answer="No confident answer was found in the knowledge base within the search limit.", sources=[])
    return out["structured_response"]

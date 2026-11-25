"""Layer 1 retriever: ingest layer1_kb/ into Chroma collection "kb", then dense search with a distance cutoff.

Run `python -m app.layer1_rag.retriever` to (re)build the index.
"""

from pathlib import Path

import chromadb
from pypdf import PdfReader

from app.tools.docs_tools import _chunks, shared_client

ROOT = Path(__file__).resolve().parents[2]
KB_DIR = ROOT / "layer1_kb"
STORE_DIR = ROOT / "data" / "vector_store"
COLLECTION = "layer1_kb"  # Chroma names need 3+ characters
L1_MAX_DIST = 0.42  # cosine distance cutoff. Measured on the sample KB: on-topic 0.23-0.40, live-data 0.44, off-topic 0.86+. Re-tune on the real KB.
SUFFIXES = {".pdf", ".md", ".txt"}


def _client():
    return shared_client()


def index_ready() -> bool:
    client = _client()
    return COLLECTION in [c.name for c in client.list_collections()] and client.get_collection(COLLECTION).count() > 0


def _read(path: Path) -> str:
    if path.suffix == ".pdf":
        return "\n".join(page.extract_text() or "" for page in PdfReader(path).pages)
    return path.read_text(encoding="utf-8")


def ingest() -> tuple[int, int]:
    """Rebuild the layer1_kb collection from every supported file under layer1_kb/. Returns (files, chunks)."""
    client = _client()
    if COLLECTION in [c.name for c in client.list_collections()]:
        client.delete_collection(COLLECTION)  # full rebuild, so removed documents do not linger
    col = client.create_collection(COLLECTION, metadata={"hnsw:space": "cosine"})

    ids, docs, metas, files = [], [], [], 0
    for path in sorted(KB_DIR.rglob("*")):
        if not path.is_file() or path.suffix not in SUFFIXES or path.name == "README.md":
            continue
        rel = path.relative_to(KB_DIR)
        category = rel.parts[0] if len(rel.parts) > 1 else "general"
        files += 1
        for n, chunk in enumerate(_chunks(_read(path))):
            ids.append(f"{rel.as_posix()}#{n}")
            docs.append(chunk)
            metas.append({"category": category, "source": rel.as_posix(), "chunk": n})
    if ids:
        col.add(ids=ids, documents=docs, metadatas=metas)
    return files, len(ids)


def search(query: str, k: int = 4) -> list[dict]:
    """Top-k passages whose cosine distance is within L1_MAX_DIST. Empty list means no confident match."""
    col = _client().get_collection(COLLECTION)
    # The training guide says "new employee onboarding guide" and "welcome to the dealer family"
    # while tickets commonly say "employee onboarding". Add those local phrases only
    # for onboarding questions so the confidence cutoff remains conservative for
    # unrelated Layer 1 questions.
    query_text = query
    lowered = query.lower()
    if "onboarding" in lowered or "employee development" in lowered:
        query_text += " new employee onboarding guide welcome to the dealer family first 90 days"
    res = col.query(query_texts=[query_text], n_results=min(k, col.count()))
    hits = []
    for cid, text, meta, dist in zip(res["ids"][0], res["documents"][0], res["metadatas"][0], res["distances"][0]):
        if dist <= L1_MAX_DIST:
            hits.append({"id": cid, "text": text, "distance": dist, **meta})
    return hits


if __name__ == "__main__":
    files, chunks = ingest()
    print(f"Ingested {files} files, {chunks} chunks into {STORE_DIR}")

"""docs_tools: hybrid RAG over knowledge_base/ PDFs.

Dense: Chroma (default ONNX embeddings, collection "docs"). Sparse: BM25 over the same chunks.
Merged with Reciprocal Rank Fusion. Run `python -m app.tools.docs_tools` to (re)build the index.
"""

import re
import threading
from functools import lru_cache
from pathlib import Path

import chromadb
from pypdf import PdfReader
from rank_bm25 import BM25Okapi

ROOT = Path(__file__).resolve().parents[2]
KB_DIR = ROOT / "knowledge_base"
STORE_DIR = ROOT / "data" / "vector_store"
COLLECTION = "docs"
CHUNK_CHARS = 800
OVERLAP = 100
CANDIDATES = 10  # top hits taken from each method before fusion
DENSE_MAX_DIST = 0.75  # cosine distance cutoff: measured 0.37-0.70 for on-topic queries, 0.82+ for off-topic
RRF_K = 60

_TOKEN = re.compile(r"[a-z0-9][a-z0-9_\-]*")  # keeps codes like DLR-1042 and ERR_VIN_UNAVAILABLE whole


_client_lock = threading.Lock()
_client_instance = None


def shared_client():
    """One Chroma client per process. Opening a new client per call fails when runs search concurrently."""
    global _client_instance
    with _client_lock:
        if _client_instance is None:
            _client_instance = chromadb.PersistentClient(path=str(STORE_DIR))
        return _client_instance


def _collection():
    return shared_client().get_or_create_collection(COLLECTION, metadata={"hnsw:space": "cosine"})


def _chunks(text: str) -> list[str]:
    """Fixed-size windows over whitespace-normalized text, cut at spaces, with overlap."""
    text = " ".join(text.split())
    out, start = [], 0
    while start < len(text):
        end = min(start + CHUNK_CHARS, len(text))
        if end < len(text):
            cut = text.rfind(" ", start, end)
            if cut > start:
                end = cut
        out.append(text[start:end])
        if end >= len(text):
            break
        start = max(end - OVERLAP, start + 1)
    return out


def build_index() -> int:
    """Rebuild the docs collection from every PDF in knowledge_base/. Returns the number of chunks."""
    client = shared_client()
    if COLLECTION in [c.name for c in client.list_collections()]:
        client.delete_collection(COLLECTION)  # full rebuild, so removed chunks do not linger
    col = client.create_collection(COLLECTION, metadata={"hnsw:space": "cosine"})
    ids, docs, metas = [], [], []
    for pdf in sorted(KB_DIR.glob("*.pdf")):
        text = "\n".join(page.extract_text() or "" for page in PdfReader(pdf).pages)
        for n, chunk in enumerate(_chunks(text)):
            ids.append(f"{pdf.name}#{n}")
            docs.append(chunk)
            metas.append({"source": pdf.name, "chunk": n})
    if ids:
        col.add(ids=ids, documents=docs, metadatas=metas)
    _corpus.cache_clear()
    return len(ids)


def _tokens(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


@lru_cache(maxsize=1)
def _corpus():
    """All chunks in memory for BM25. Cached per process; build_index() clears it."""
    got = _collection().get()
    ids = got["ids"]
    texts = got["documents"]
    lookup = dict(zip(ids, zip(texts, got["metadatas"])))
    return ids, lookup, BM25Okapi([_tokens(t) for t in texts])


def search_docs(query: str, top_k: int = 4) -> str:
    """Hybrid search (dense + BM25) over RDR runbooks, status/error catalog and architecture guide.
    Returns top_k passages labelled with source#chunk and each method's rank."""
    col = _collection()
    if col.count() == 0:
        return "INDEX_NOT_BUILT: run `python -m app.tools.docs_tools` first."

    dense = col.query(query_texts=[query], n_results=min(CANDIDATES, col.count()))
    dense_hits = [(cid, dist) for cid, dist in zip(dense["ids"][0], dense["distances"][0]) if dist <= DENSE_MAX_DIST]
    dense_rank = {cid: r for r, (cid, _) in enumerate(dense_hits, 1)}
    dense_dist = dict(dense_hits)

    ids, lookup, bm25 = _corpus()
    scores = bm25.get_scores(_tokens(query))
    hits = [i for i in sorted(range(len(ids)), key=lambda i: -scores[i])[:CANDIDATES] if scores[i] > 0]
    sparse_rank = {ids[i]: r for r, i in enumerate(hits, 1)}

    fused: dict[str, float] = {}
    for rank_map in (dense_rank, sparse_rank):
        for cid, r in rank_map.items():
            fused[cid] = fused.get(cid, 0.0) + 1.0 / (RRF_K + r)
    if not fused:
        return "NO_MATCH: no passage matched this query by keyword or meaning."

    lines = []
    for cid in sorted(fused, key=lambda c: -fused[c])[:top_k]:
        text, meta = lookup[cid]
        d = f"rank {dense_rank[cid]}, dist {dense_dist[cid]:.2f}" if cid in dense_rank else "-"
        s = f"rank {sparse_rank[cid]}" if cid in sparse_rank else "-"
        lines.append(f"[{meta['source']}#{meta['chunk']}] (dense {d}; bm25 {s})\n{text}")
    return "\n\n".join(lines)


if __name__ == "__main__":
    print(f"Indexed {build_index()} chunks into {STORE_DIR}")

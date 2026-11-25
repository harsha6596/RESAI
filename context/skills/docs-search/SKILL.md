---
name: docs-search
description: Search the RDR knowledge base (runbooks, status/error catalog, architecture guide) with hybrid keyword + meaning search, judge relevance, and cite the matching passages.
---

# docs-search

## When to use
The orchestrator asks what the RDR runbooks, error catalog or architecture guide say about a symptom, status, error code, or service.

## Steps
1. **One semantic retrieval.** Use one plain-language query containing the incident symptom, status, and issue category. Do not include VINs, dealer IDs, submission IDs, incident IDs, or other transaction identifiers.
2. **Stop after the first retrieval.** The result is supporting evidence for the orchestrator. Never issue exact-identifier searches or follow-up searches.
4. **Merge and rank.** Drop duplicate `file#chunk` results. Rank by how directly the passage matches the symptom, not by which search found it.
5. **Label each passage** with the rules in `AGENTS.md` (`RELEVANT` / `POSSIBLY_RELEVANT` / `NOT_RELEVANT`) and give a one-line reason.

## Reading the result
Each passage has a header:
`[file#chunk] (dense rank N, dist D; bm25 rank M)`
- `dense rank` / `dist`: meaning match. Distance below about 0.6 is a close match. Above 0.7 is a weak one. Lower is closer.
- `bm25 rank`: keyword match. A hit with only a low BM25 rank can be a common word, so check the text before labelling it `RELEVANT`.
- `-`: that method did not return the passage.
- `NO_MATCH`: nothing passed the relevance cutoff.
- `INDEX_NOT_BUILT`: the index was never built. Report it, do not work around it.

## Guardrails
- Retrieval is context, not proof. Never state a root cause from a runbook alone.
- A runbook step is a suggested check. Report it as a check to run, not a conclusion.
- Do not quote more than one or two sentences of a passage.
- Do not invent runbook names, IDs, or step numbers. Cite only `file#chunk` from the results.

## Output
- Query used(s), exact text.
- Findings: label, `file#chunk`, reason, the step it suggests.
- Not covered by docs: what the question needs that no passage answers.
- Other-agent checks: what the DB, logs, or browser agent should confirm next.
- Result: `FOUND` | `NO_MATCH` | `INDEX_NOT_BUILT`.

## Example (INC-001)
Ticket: RDR stays PENDING after three attempts, "Processing delayed".
1. Exact: `PENDING retry exhausted worker` → `03_Runbook_Internal_RDR_Worker_Failure.pdf#0` (keyword rank 1). Label `RELEVANT`. The runbook covers PENDING RDRs with exhausted retries.
2. Symptom: "transaction still pending after several processing attempts" → same runbook, plus `02_RDR_Status_Validation_and_Error_Catalog.pdf` for the status meaning. Label `POSSIBLY_RELEVANT` for the catalog.
3. Result: `FOUND`. Other-agent checks: DB agent confirms the row is still PENDING with `retry_count = 3`; logs agent confirms retry events; the runbook says connection-pool evidence is contributing, not proof.

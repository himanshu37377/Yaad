# Hybrid Retrieval Design — Fusing Keyword, Memory, and Semantic Signals

Date: 2026-10-30. Status: design only; no source or test changes made.
Implementation update (2026-10-31): Phase 1 was implemented as designed
(deterministic FTS5 + Tier A memory fusion via RRF in
`yaad/query/hybrid.py`). Phases 2 and 3 (embeddings/reranking) were not
implemented; no semantic retrieval exists. The body below is the
original design record.
Method: code inspection plus disposable read-only probes (`/tmp`, no
production writes, no model spend). All quoted behaviors were measured,
not inferred.

## 1. Background and scope

Yaad retrieves evidence today with a single deterministic channel:
FTS5 keyword search with BM25 ranking. It misses paraphrases, inflected
forms, and anything ranked past its limit. This document designs a
hybrid replacement that fuses keyword, memory, and (later) semantic
signals with Reciprocal Rank Fusion (RRF), reranks the fused set, and
feeds the result through the existing memory, relationship-validation,
and claim-relevance layers unchanged.

Feasibility boundary, stated up front: this environment has no
embedding model, no vector index, no reranker, and no reachable model
endpoint (local client speaks Ollama `/api/chat` only; the sandbox
blocks sockets). Semantic retrieval and model reranking therefore
cannot be implemented *and verified* here. The design splits the work
accordingly: Phase 1 is fully deterministic and verifiable now; Phases
2–3 specify the model-backed channels with exact interfaces, so they
land as new rank sources behind the same fusion — no re-architecture.

## 2. Current behavior

### 2.1 The retrieval stack (`yaad/query/retrieve.py`, 29 lines)

- `search(db, query, folder_id, limit, file_kinds)`: content-term
  tokenization (lowercase, stopwords removed, length > 1, no stemming),
  FTS5 `OR` over up to 20 quoted terms, `ORDER BY` README-demotion then
  `bm25(units_fts)`, hard `LIMIT`. An explicit comment forbids topic
  expansion: only the question's own terms may retrieve.
- Call sites and limits: main answer path 15 (`answer.py:390`), audio
  path 15, disclosure theme scan 20, photo paths `2×limit`/fallback.
- Downstream funnel: 15 retrieved → `best = injected + rows[:8]`
  capped at 12 → semantic review reads the top 8. Anything ranked past
  15 is invisible to every downstream layer, model or deterministic.

### 2.2 The second channel that already exists: memory

`plan_memory` matches records on title/detail wording (Tier A/C), and
Tier A support rows inject into `best` ahead of retrieved rows. Memory
is therefore already a retrieval channel with a *different vocabulary*
from the units: record titles/keys often name things the unit text
paraphrases. Measured (P5): for "what is the registration fee?",
keyword retrieval returns only `ev_seen` ("Registration desk opens
soon", no value); a Tier A record citing it plus the paraphrased
`ev_hidden` ("The entry cost is Rs 150") injects the hidden unit and
the answer flips from twin `cloud_unavailable` to `answered Rs 150`.
Any hybrid design must preserve and subsume this channel, not bypass
it: it is the only paraphrase-tolerant retrieval Yaad has today.

### 2.3 Downstream gates (unchanged by this design)

Fused candidates flow through the existing layers in order: consent /
egress gating, taint (`_superseded_units`), memory matching with Tier
A/C relevance, validated-conflict pairing, pseudo-values, deterministic
or semantic synthesis. All of these are rank-agnostic *filters* over a
candidate set, which is exactly why fusion can sit upstream without
touching them: hybrid retrieval changes *which* candidates the gates
see, never what the gates accept.

## 3. Failure analysis (measured)

- **P1 — paraphrase miss.** Unit "The registration fee is Rs 150";
  question "what is the entry cost?" shares zero content terms →
  0 rows → `not_found`. Literal wording retrieves fine. This is the
  core recall gap semantic retrieval must close (audit H1 adjacent).
- **P2 — inflection miss.** "drafts status?" retrieves nothing for a
  "draft … due" unit: no stemming or plural folding (audit M4). The
  cheapest deterministic recall win on the table.
- **P3 — limit truncation.** With 21 matching units, the gold unit
  ("closing date is June 9") ranks 20th at depth 50 and is absent at
  the production limit of 15. Recall is capped by rank depth before
  any intelligence applies; per-channel depth must grow with fusion.
- **P4 — noise co-retrieval.** Generic words retrieve irrelevant units
  alongside gold (BM25 ranked gold first here because "venue"
  discriminates). When nothing discriminates, noise reaches review —
  the retrieval-side half of G1 (VALIDATION_RESULTS §12.4), whose
  other half is review-time judgment. Reranking addresses the
  ordering; relevance tiers already bound the damage.
- **P5 — memory rescue (preserve case).** Above: Tier A injection
  recovers paraphrased evidence the keyword layer cannot see. The
  RRF design treats memory-unit matches as a first-class channel so
  this rescue becomes systematic instead of incidental.

## 4. Proposed architecture

```
question ─┬─ keyword channel (FTS5/BM25, improved) ──────┐
          ├─ memory channel (Tier A records → units) ────┤─ RRF fuse ─┬─ policy gates ─┬─ rerank ── review/synthesis
          └─ semantic channel (embeddings, Phase 2) ─────┘   (depth D) │  (existing)    │  (Phase 3; RRF
                                                                      │                │   order = fallback)
```

### 4.1 Candidate generation (per-channel ranked lists)

- **Keyword channel** (Phase 1, deterministic): current FTS5 core plus
  plural/stemming folding at query time, exact-phrase bonus for quoted
  spans, and a locator/section signal folded into the order key. Stays
  the precision anchor: exact names, dates, and amounts must keep
  winning (the brief's point 1).
- **Memory channel** (Phase 1, deterministic): Tier A matched records
  contribute their supporting units ranked by the claim's
  (`shared`, `overlap`) order. Formalizes §2.2 as a rank source
  instead of a prepend hack; Tier C stays out (verdict-neutrality).
- **Semantic channel** (Phase 2, model-backed): embedding cosine over
  unit texts, top-D. No new service: embeddings stored as SQLite
  BLOBs with brute-force scan at Yaad's scale (thousands of units),
  `sqlite-vec` only if measured latency demands it.

Each channel returns `(evidence_id, rank)` to a fixed per-channel
depth D (proposed 50; §3 P3 shows 15 truncates gold).

### 4.2 Fusion: Reciprocal Rank Fusion

Standard RRF, `score(e) = Σ 1/(k + rank_c(e))` with `k = 60`, over the
channels that returned the unit; fused list truncated to top-N
(proposed 30) for policy gating. RRF needs no score calibration across
heterogeneous rankers, is deterministic, and degrades gracefully when a
channel is absent (offline semantic outage → fuse the rest). Channel
weights stay uniform in v1; per-channel weights only with harness
evidence.

### 4.3 Policy gates (existing code, unchanged)

Consent/egress, taint, memory tiers, relationship validation, and
relevance tiers run over the fused top-N exactly as today. Fusion must
not smuggle candidates past any gate: fused units still resolve,
attribute, and synthesize through the same functions, so every
existing guarantee (evidence integrity, verdict neutrality of Tier C,
validated-only conflicts) holds by construction.

### 4.4 Reranking

Top-N fused candidates → top-K (proposed 8, matching the review
window) ordered by the actual query. Phase 3 model options in
preference order: (a) local cross-encoder via Ollama where available;
(b) the existing semantic-review call extended with explicit
relevance grades (no new egress purpose). Deterministic fallback is
always the RRF order — offline behavior must equal fused retrieval
with no rerank step, never an error. Rerank prompts/judgments stay
out of the ledger; only retrieved-then-cited evidence is cited.

### 4.5 Interfaces and pseudocode

```python
Channel = str  # "keyword" | "memory" | "semantic"

def retrieve_channel(db, question, channel: Channel, depth: int,
                     folder_id=None, file_kinds=None) -> list[str]:
    """Ranked evidence_ids from one channel. Keyword/memory are local
    and deterministic; semantic raises Unavailable (fuse the rest)."""

def rrf_fuse(ranked: dict[Channel, list[str]], k: int = 60) -> list[str]:
    scores: dict[str, float] = {}
    for ids in ranked.values():
        for rank, eid in enumerate(ids, start=1):
            scores[eid] = scores.get(eid, 0.0) + 1.0 / (k + rank)
    return sorted(scores, key=lambda e: (-scores[e], e))

def hybrid_search(db, question, folder_id=None, file_kinds=None,
                  depth: int = 50, top_n: int = 30) -> list[Row]:
    """Drop-in successor to search(): fuse available channels, hydrate
    rows in fused order. Current search() stays as the keyword entry."""
    ranked = {}
    ranked["keyword"] = retrieve_channel(db, question, "keyword", depth, ...)
    ranked["memory"] = retrieve_channel(db, question, "memory", depth, ...)
    try:
        ranked["semantic"] = retrieve_channel(db, question, "semantic", depth, ...)
    except Unavailable:
        pass  # fuse the rest; never fail retrieval on one channel
    fused = rrf_fuse(ranked)[:top_n]
    return fetch_unit_rows(db, fused)  # existing joins, fused order

def rerank(question, rows, settings, budget) -> list[Row]:
    """Top-N fused rows → top-K for review. Model-backed when a
    provider is available and consented; else returns input order."""
```

`answer.py` call sites switch from `search(..., 15)` to
`hybrid_search(...)` one at a time, each behind its existing tests;
`search()` itself remains for the keyword channel and the disclosure
scan. The `best`/`review` windows (12/8) are unchanged in Phase 1.

## 5. Deterministic-now vs model-later split

**Now (Phase 1, verifiable here):** query-time plural/stemming fold;
exact-phrase bonus; memory-unit channel with Tier A restriction; RRF
fusion over keyword+memory with depth 50/top-N 30; README rule
preserved as a fused-order tiebreak; per-channel ablation flags for
evaluation. Zero new egress, zero new dependencies, zero schema
change (no stored vectors).

**Later (Phase 2, needs model infra):** embedding backfill + refresh
on index (new `unit_embeddings` table: BLOB + model tag + updated_at;
stale-embedding invalidation on reindex); Ollama `/api/embeddings`
client with timeouts, receipts, and `purpose="retrieval-embedding"`;
consent rule — embedding calls ship unit text off-device, so cloud
embeddings require folder consent exactly like review, local
embeddings follow the local rule. Provider outage degrades to
keyword+memory fusion, never an error.

**Later (Phase 3):** rerank per §4.4 with spend caps, receipt
logging, and an offline-order fallback test.

## 6. Evaluation plan

Reuse the RAG-comparison harness (`/tmp/ragcmp` pattern): baseline
keyword vs hybrid on identical corpora, mocked first (deterministic),
then spend-capped real-model. Borrow the paraphrase battery from
VALIDATION_RESULTS §11 as seed cases plus P1–P3 fixtures as
regression probes. Metrics: recall@15/50 and gold-rank deltas on
probes; answer-status deltas vs twin (improvements must not flip
correct abstentions — the G3 lesson); spend and latency per query;
P5 preserve-case must stay green. Entry criterion for each phase:
harness shows a win on paraphrase probes with zero regressions on
the existing suite and the twin-equality batteries.

## 7. Phased implementation

- **Phase 1 — deterministic hybrid (no models):** stemming/plural
  fold, phrase bonus, memory channel, RRF fusion, depth/top-N
  constants, per-channel ablation, probe battery (P1–P5 + §11 seeds)
  as committed tests. Exit: paraphrase/plural/truncation probes
  improve, suite green, twins equal.
- **Phase 2 — semantic channel:** embeddings table + backfill/refresh,
  Ollama client with receipts/consent/degrade, fused evaluation.
  Exit: P1-style probes retrieved via semantic with keyword precision
  intact (exact-match probes keep rank 1).
- **Phase 3 — rerank:** top-N→top-K with model + deterministic
  fallback, spend caps, G1-noise probes. Exit: noise-probe answers
  improve or abstain; offline order unchanged.

## 8. Trade-offs and non-goals

- **No vector-database service:** SQLite BLOBs + brute force fit
  thousands of units; revisit on measured latency, not anticipation.
- **FTS stays the precision anchor:** semantic recall must never
  outrank exact name/date/amount matches for precision probes;
  RRF uniformity plus the phrase bonus encode this.
- **Egress/consent applies to embeddings:** shipping unit text to any
  embedding endpoint is egress with purpose tagging and receipts,
  governed like review — not a free background call.
- **Fusion never bypasses gates:** relevance tiers, validation, and
  taint see more candidates but accept by the same rules; Tier C
  verdict-neutrality is a fusion invariant, tested by R1–R4 analogs.
- **Non-goals:** query rewriting/expansion (the no-expansion comment
  in `retrieve.py` stays until the harness justifies otherwise),
  multi-hop retrieval (audit H1 — needs the graph direction, not
  this design), UI changes, new services or daemons.

# Temporal Memory Ledger — Architecture Design

Status: design only. No implementation. Based on code inspection of the Yaad
repository at this commit (no source, test, schema, or doc files modified).
Implementation update (2026-10-31): implemented as designed
(`yaad/memory/ledger.py`, `memory_transitions` table,
`GET /api/memory/transitions`, History page). No backfill exists for
transitions that predated the ledger. The body below is the original
design record.

## 1. Current architecture findings

### 1.1 Memory record schema and lifecycle

`yaad/memory/records.py` (`MEMORY_SCHEMA`) defines three tables:

- `memory_records`: `record_type` in {decision, deadline, commitment,
  open_question}, `title`, `detail`, `date_value`/`date_raw`,
  `amount_value`/`amount_raw`, `assignee`, `status` in
  {decided, open, superseded, conflicted, stale}, `statement_kind` in
  {explicit, proposal, inferred, unresolved}, deterministic `record_key`,
  `extraction_run`, `created_at`. `UNIQUE(folder_id, record_key)`.
- `memory_record_evidence`: `(record_id, evidence_id, role, excerpt)` with
  `role` in {supports, contradicts, supersedes}.
- `record_flags`: user correction reports (unused by any sweep today).

Identity (`compute_record_key`) is
`record_type | kind_group | date_value | assignee | key-phrase signature | amount_class`.
Crucially, `kind_group_of` puts `explicit`/`inferred` in one group and
`proposal`/`unresolved` each in their own, so **a proposal can never merge
with an asserted claim by key** — the venue example's "proposal for Hall B"
already stays a separate record from "confirmed Hall A" by construction.

Existing lifecycle transitions (all deterministic, in `records.py`):

| Mechanism | Effect |
|---|---|
| `mark_conflicts` | Same-matter explicit records (`_matter_key`: type+assignee+signature) asserting different dates or different single amounts → both `conflicted`. Clears only when no rival pair remains; restores via `RESTORED_STATUS`. |
| `apply_supersession_links` | A validated `supersedes` edge steps the target down to `superseded`. |
| `mark_stale` | Records with zero `resolving_supports` → `stale`. Re-extraction restores `stale` → intake status; nothing else auto-restores. |
| `upsert_records` | Same-key merge only; evidence is unioned, never replaced; rows that no longer revalidate are reported via `pruned`, never silently dropped. |

### 1.2 Supersession handling (the closest thing to a ledger today)

Supersession edges are model-emitted `supersedes` rows validated by
`apply_supersession_links` (`records.py:461`). An edge applies only when:
the link excerpt still resolves against the live unit; the claimant has
distinct resolving `supports` of its own; the target is a current
(non-superseded, non-stale) record whose excerpt still resolves; and both
claims pass the same-matter relatedness gate (key-phrase Jaccard ≥ 0.2, or
exact matter match plus ≥ 2 shared excerpt words). Every rejection is
collected with a reason (`rejected_supersession` in the extraction summary)
and the old record keeps its status. Gaps vs. a ledger: the edge stores no
reason, no previous/new value pair, and no link back to the superseding
record id — only `(new_record → old evidence_id, excerpt)`.

### 1.3 Evidence provenance and historical/current filtering

- Provenance: every record needs ≥ 1 `supports` row; excerpts must be exact
  substrings of cited units (`validate_candidate`); `resolving_supports` is
  the single shared definition of "evidence still backs this" (unit exists
  AND still contains the excerpt).
- Historical filtering: `_superseded_units` (`yaad/query/answer.py:145`)
  taints evidence IDs whose only supports are `superseded`; tainted units
  are excluded from value extraction, rendered with `historical: true` +
  "History" tag in the UI, passed to semantic review as
  `historical: true` with instructions never to present them as current,
  and a review citing only history is demoted to `unverified`.
- Consent gates serving, never history: revoked consent excludes records
  from answers but stored rows survive (`_listing_displayable`,
  `authorized_supports`, `test_listing_respects_consent`).

### 1.4 Extraction and indexing workflows

Extraction (`yaad/memory/extract.py::run_extraction`, via
`POST /api/memory/extract`) is explicit-only: consent-filtered unit
selection, one spend-capped model call against `EXTRACTION_SCHEMA`,
deterministic validation (`validate_candidate`), idempotent upsert, then
sweeps in fixed order: `mark_conflicts` → `apply_supersession_links` →
`mark_stale` (`extract.py:190-192`). Rejected candidates, rejected links,
pruned evidence, and dupe-hints are all returned in the summary — nothing
is silently dropped. Re-indexing runs the same three sweeps deterministically
(no model calls) because reindexing changes evidence ids (`worker.py:136-139`).

Serving (`POST /api/ask` → `answer_question`, `yaad/query/answer.py:316`)
runs FTS retrieval, `plan_memory` (`yaad/query/memory_answer.py:275`),
value/conflict computation, and optional consented semantic review with
mechanical citation checks. `plan_memory` matches only eligible, authorized
records: `stale`/`superseded`/`inferred` excluded, `unresolved` only for
undecided-questions, relevance needs ≥ 2 shared content words
(`MATCH_WORDS = 2`); `conflicted` records are served only as pairs.
Status-intent questions ("which memories are proposals?") take a separate
deterministic listing path (`detect_status_intent`, `status_listing_response`)
with determiner-guard rails against document-reference hijack.

### 1.5 Tests and audit findings (do not re-prove; build on)

- ~250 tests, all mocked-model except a spend-capped real-model suite.
  Stage-organized memory coverage: answers, serving/eligibility, records,
  supersession, status listings, preservation, amounts, duplicates.
- `INDEPENDENT_PROJECT_AUDIT.md`: lifecycle machinery is real and careful,
  but matching is pure 2-word lexical overlap (no stemming), extraction
  quality on real inference is unmeasured, and "understanding change" is
  currently deterministic bookkeeping, not temporal reasoning.
- `VALIDATION_RESULTS.md`: Yaad's structural wins over a RAG baseline are
  conflict verdicts, status listings, and supersession bookkeeping —
  exactly the surfaces this ledger extends.

## 2. Gap analysis: what the venue example needs vs. what exists

| Requirement (Hall A → proposal B → confirmed C) | Exists today | Missing |
|---|---|---|
| Original confirmed decision kept | ✅ record stays, `superseded`, evidence preserved | — |
| Proposal never auto-replaces | ✅ kind-group key separation; `proposal cannot be decided` intake rule | `supersedes` edge from a *proposal claimant* not explicitly rejected (T4 guard, §5) |
| Final confirmation wins, with cause | ⚠️ status flips, but no reason, no prev/new pair, no link to the superseding record | Transition row with trigger + snapshot (§4) |
| "Current venue?" | ✅ synthesis over current records | One-line "previously" note (§7.1) |
| "Original venue?" | ❌ no earliest-state query; only un-ordered history listings | History intent + matter chain (§7.2) |
| "How did it change?" | ❌ no ordered change sequence | Ledger read path (§7.3) |
| Valid time vs recorded time | ⚠️ `created_at` ≈ recorded time; `date_value` is *content* date, not validity | Explicit valid-time columns, nullable Phase 1 (§4.1) |
| Relationship-verified change | ❌ no relationship layer at all (§6.1) | EVR consumption contract (§6.2, Phase 2) |

Net: **statuses exist, events don't.** The sweeps already compute every
transition this feature needs; nothing persists them.

## 3. Proposed architecture and data flow

```
index/extract (existing)          sweeps (existing + ledger tap)
────────────────────────          ──────────────────────────────
units ──► run_extraction ──► upsert_records ──► mark_conflicts ──┐
   (1 capped call, validated)   (union evidence)    │             │ each appends
                                                    ▼             ▼ optional
                                          apply_supersession  mark_stale
                                                    │             │
                                                    └──────┬──────┘
                                                           ▼
                                                 memory_transitions  ◄── NEW:
                                                 memory_matter_links ◄── append-only
                                                                           ledger tables

ask (existing + ledger reads)
─────────────────────────────
question ──► detect_status_intent ──► status listing (existing)
         ──► detect_history_intent ──► matter chain from ledger + records (NEW)
         ──► plan_memory ──► synthesize (+ "previously" note from ledger) (EXTENDED)
         ──► units pipeline + semantic review (existing, one added rule)
```

Design principles:

1. **Tap, don't fork**: sweeps gain an optional `transitions` out-param;
   with `None` (default) they behave byte-for-byte as today.
2. **Append-only ledger**: transitions are inserted, never updated or
   deleted (except full-table rollback, §10). Corrections are new rows.
3. **Deterministic throughout**: chain order from transition ids + edge
   direction, never from wall-clock or model output.
4. **Zero new LLM calls**: intake schema unchanged in Phase 1; all history
   serving is SQL + string templates like `status_listing_response`.

## 4. Temporal data model and persistence

### 4.1 Time concepts

| Concept | Meaning | Source in this design |
|---|---|---|
| Recorded time | When Yaad learned it | Existing `created_at` + new `extraction_run` linkage; never used for authority |
| Valid time | When the claim held in the world | New nullable `valid_from`/`valid_until` on links (§4.2); unpopulated in Phase 1, consumed when EVR supplies dates |
| Transition time | When the ledger recorded a state change | `memory_transitions.created_at` (monotonic per DB; ordering key, not authority) |
| Content date | Date *mentioned in* the claim (deadline, event day) | Existing `date_value` — unchanged meaning, never conflated with valid time |

Key rule: **no timestamp on any row establishes authority.** Authority
comes only from validated edges (T4) and explicit user action (T8).
Timestamps order the *display* of already-authorized transitions.

### 4.2 Schema changes (additive; existing tables untouched in Phase 1)

Yaad's SQLite architecture supports this with two new tables. No graph
database, no FTS, no new indexes beyond the compound keys shown.

```sql
CREATE TABLE IF NOT EXISTS memory_transitions(
  id INTEGER PRIMARY KEY,
  folder_id INTEGER NOT NULL REFERENCES folders(id) ON DELETE CASCADE,
  record_id INTEGER NOT NULL REFERENCES memory_records(id) ON DELETE CASCADE,
  from_status TEXT,            -- NULL on creation row
  to_status TEXT NOT NULL,
  from_kind TEXT,              -- NULL on creation row
  to_kind TEXT NOT NULL,
  trigger TEXT NOT NULL CHECK(trigger IN
    ('extraction','conflict_sweep','conflict_cleared','supersession_link',
     'stale_sweep','re_extraction','confirmation','edge_revoked',
     'user_flag','user_confirm')),
  triggered_by_record_id INTEGER REFERENCES memory_records(id) ON DELETE SET NULL,
  evidence_id TEXT,            -- the edge/evidence that caused this row, if any
  excerpt TEXT,                -- exact-substring quote justifying the change
  previous_title TEXT,         -- value snapshot: what this record asserted
  previous_date_value TEXT,    -- (records are never rewritten, so this is the
  previous_amount_value TEXT,  --  pre-transition assertion for display)
  reason TEXT NOT NULL DEFAULT '',
  actor TEXT NOT NULL DEFAULT 'system' CHECK(actor IN ('system','user','evr')),
  extraction_run TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_transitions_record
  ON memory_transitions(folder_id, record_id, id);

CREATE TABLE IF NOT EXISTS memory_matter_links(
  id INTEGER PRIMARY KEY,
  folder_id INTEGER NOT NULL REFERENCES folders(id) ON DELETE CASCADE,
  from_record_id INTEGER NOT NULL REFERENCES memory_records(id) ON DELETE CASCADE,
  to_record_id INTEGER NOT NULL REFERENCES memory_records(id) ON DELETE CASCADE,
  link_type TEXT NOT NULL CHECK(link_type IN
    ('supersedes','confirmed_by','conflicts_with')),
  evidence_id TEXT NOT NULL,
  excerpt TEXT NOT NULL,
  valid_from TEXT,             -- ISO date, NULL until EVR supplies valid time
  valid_until TEXT,            -- ISO date, NULL = still current belief
  created_by_run TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(folder_id, from_record_id, to_record_id, link_type)
);
```

`memory_matter_links` is derived caching, not source of truth: every row
must correspond to either a validated `supersedes` evidence row or a
`memory_transitions` row with a trigger in
{`supersession_link`, `confirmation`, `conflict_sweep`}. A debug assertion
(or periodic check, not a new daemon — run inside extraction) verifies
this; orphan links are deleted loudly (reported in the extraction summary).

### 4.3 Example records: the venue triad

Starting state after three extraction runs over `initial.txt`
("The event venue is Hall A"), `proposal.txt` ("Move the event to Hall B"),
`final.txt` ("The venue is changed to Hall C"):

`memory_records` (existing shape, abbreviated):

| id | title | kind | status | record_key (suffix…) |
|---|---|---|---|---|
| 11 | Venue is Hall A | explicit | superseded | …`event+hall+venue`… |
| 12 | Move venue to Hall B | proposal | open | proposal-group key, separate |
| 13 | Venue is Hall C | explicit | decided | …`event+hall+venue`… |

`memory_record_evidence` gains one `supersedes` row on record 13 naming
record 11's evidence (model-emitted, gate-validated as today).

`memory_transitions` (new):

| id | record | from → to | trigger | by | excerpt/reason |
|---|---|---|---|---|---|
| 41 | 11 | — → decided/explicit | extraction | — | run r1 |
| 42 | 12 | — → open/proposal | extraction | — | run r2 |
| 43 | 13 | — → decided/explicit | extraction | — | run r3 |
| 44 | 11 | decided → superseded | supersession_link | 13 | "venue is changed to Hall C" |

`memory_matter_links` (new):

| from | to | type | excerpt |
|---|---|---|---|
| 13 | 11 | supersedes | "venue is changed to Hall C" |

Queries resolve as: current = 13 (Hall C); original = earliest
`extraction`-triggered non-proposal state in the matter chain = 11
(Hall A); proposal 12 rendered labeled, never as a held state.

### 4.4 Sweep plumbing (the tap)

Each sweep takes an optional `transitions: list | None = None` out-param.
Example for the `upsert_records` same-key refresh branch:

```python
def upsert_records(con, folder_id, run_id, candidates, unit_texts,
                   pruned=None, transitions=None):  # new opt param
```

In the same-key UPDATE branch, after computing the new status/kind:

```python
    cur = con.execute(
        "SELECT status, statement_kind, title, date_value, amount_value"
        " FROM memory_records WHERE id=?", (record_id,)).fetchone()
    # in the same-key UPDATE branch, after computing new status/kind:
    if (status, kind) != (cur["status"], cur["statement_kind"]):
        transitions.append({
            "folder_id": folder_id, "record_id": record_id,
            "from_status": cur["status"], "to_status": status,
            "from_kind": cur["statement_kind"], "to_kind": kind,
            "trigger": "re_extraction",
            "triggered_by_record_id": None, "evidence_id": None,
            "excerpt": None, "reason":
                f"same-key refresh in run {run_id}",
            "actor": "system", "extraction_run": run_id,
        })
```

`mark_conflicts`, `apply_supersession_links`, and `mark_stale` take the
same optional `transitions` list and append one row per status change they
make, with trigger names `conflict_sweep`, `supersession_link`,
`stale_sweep` (and `conflict_cleared` on restore). The extraction endpoint
and the indexer sweep call-site pass a list through and insert the rows in
the same transaction. Returning the new transition ids in the extraction
summary (`summary["transitions"]`) mirrors the existing
`conflicts`/`superseded`/`stale` reporting.

What this buys immediately, with zero query changes: every status change in
the venue example is already reconstructible — Hall A `decided →
superseded` (trigger `supersession_link`, `triggered_by_record_id` = Hall C
record, reasoned excerpt quoted), Hall B staying `open/proposal` untouched,
conflict pairs appearing and clearing with causes. The "change ledger" is
therefore mostly a **persistence of events the sweeps already compute**,
not a new inference pipeline.

### 4.5 Initial backfill

For pre-ledger databases, one idempotent migration inserts a single
`extraction` transition per existing record:

```sql
INSERT INTO memory_transitions
  (folder_id, record_id, from_status, to_status, from_kind, to_kind,
   trigger, reason, actor, extraction_run, created_at)
SELECT folder_id, id, NULL, status, NULL, statement_kind,
       'extraction', 'backfill: first observed state', 'system',
       extraction_run, created_at
FROM memory_records m
WHERE NOT EXISTS (SELECT 1 FROM memory_transitions t WHERE t.record_id = m.id);
```

Current `superseded` records additionally get a best-effort
`supersession_link` row when a resolving `supersedes` edge names their
evidence (same query shape as `apply_supersession_links`, read-only). Where
no edge is found, the record keeps only its `extraction` row — history
starts at migration time, honestly labeled.

## 5. State-transition rules

Authoritative transition table. "Sweep" rows execute inside the existing
sweep functions; "intake" rows inside `validate_candidate`/`upsert_records`.

| # | From → To | Trigger | Guard (all must hold) |
|---|---|---|---|
| T1 | — → `open`/`decided` | `extraction` | Intake validation passes (§1.4); proposal/unresolved ⇒ `open`; decision ⇒ `decided`. |
| T2 | current → `conflicted` | `conflict_sweep` | `_rivals` holds against another current explicit record. No value comparison beyond date/single-amount. |
| T3 | `conflicted` → restored | `conflict_cleared` | No rival pair contains this record (existing restore rule). |
| T4 | current → `superseded` | `supersession_link` | Existing 4-gate validation (§1.2) **plus**: claimant kind must be `explicit` — a `proposal` claimant edge is rejected (`proposal_cannot_supersede`). Unchanged otherwise. |
| T5 | any non-stale → `stale` | `stale_sweep` | Zero resolving supports. Precedence over T2/T4 (existing order). |
| T6 | `stale` → intake status | `re_extraction` | Same-key candidate revalidates (existing restore path). |
| T7 | `open/proposal` → `decided/explicit` | `confirmation` | **New**: an explicit record for the same matter is confirmed (same matter key, intake-valid), and the proposal's supporting excerpts still resolve. The proposal row keeps its kind `proposal` but takes status `decided`?? — see decision below. |
| T8 | any → any | `user_flag` / `user_confirm` | **New, optional**: reviewed `record_flags` row applied by explicit user action in the Memory view. |

**Decision required on T7.** Two options:

- **Option A (recommended): link, don't mutate.** The proposal record is
  never rewritten. `memory_matter_links` gains a `confirmed_by` edge from
  the proposal to the confirming explicit record, and the proposal renders
  as "Proposed → confirmed by #id" in history views. The confirmed decision
  is its own record with its own evidence. This preserves the invariant
  "proposal cannot be decided" (`validate_candidate`) and keeps every row's
  kind immutable, which makes the ledger trivially auditable.
- **Option B: kind promotion.** Allow `proposal → explicit` in place with a
  `confirmation` transition row. Smaller query surface, but it rewrites the
  epistemic character of a row after the fact and complicates the
  kind-group identity reasoning.

Option A costs one link type and no status-model change; take it.

**Explicit non-transitions** (constraints from the brief, enforced as
rejection reasons, never silent):

- Newer `created_at` / `extraction_run` alone ⇒ no transition, no priority.
- `proposal` claimant ⇒ `supersedes` edge rejected (T4 guard).
- `unresolved` / `inferred` claimants ⇒ edges rejected (existing: targets
  must be explicit; claimants inherit the same bar for T4).
- Unverified relationship ⇒ no transition, no serving effect (§7).

## 6. Relationship integration

### 6.1 What exists today (do not duplicate)

Yaad has **no relationship layer**. The word appears once, as an
instruction inside the PDF-understanding prompt
(`yaad/llm/meta_client.py:157`: "Extract … entities, relationships, and
table facts"), whose output is stored as flat derived-fact units, not
edges. The `contradicts` evidence role exists in the schema but no code
writes it (extraction prompts never mention it; only `supports` and
`supersedes` are produced) and no sweep reads it. There is no edge table,
no entity table, no relation validator.

### 6.2 Proposed consumption contract

The ledger consumes validated relationships; it never validates them. The
Evidence-Verified Relationships (EVR) layer owns extraction, validation,
and storage of edges. The ledger needs exactly one new edge semantic from
it, plus a read API:

- **Edge semantic**: `supersedes(new_record_id, old_record_id)` and
  `confirms(new_explicit_record_id, old_proposal_record_id)` as
  first-class validated edges, each carrying `evidence_id`, `excerpt`
  (exact-substring rule, as today), and the validator version that
  accepted it.
- **Read API** (function-level, same process/SQLite): e.g.
  `list_validated_edges(con, folder_id, run_id) -> list[dict]` returning
  only edges in `validated` state. The ledger sweep calls it where it
  today scans raw `supersedes` rows.

Migration path that avoids a flag day:

1. **Phase 1 (pre-EVR)**: ledger treats model-emitted `supersedes` rows
   filtered through the existing `apply_supersession_links` gates as
   "validated". No behavior change; transitions just get recorded.
2. **Phase 2 (EVR lands)**: `apply_supersession_links` additionally (then
   exclusively) accepts EVR-validated edges. Model-emitted rows without an
   EVR validation receipt are rejected with `unverified_relationship`.
   The cutoff is per-folder and recorded, so mixed states are explainable.
3. **Steady state**: the `supersedes` evidence role becomes write-only for
   EVR; the ledger reads edges, applies T4/T7, and records transitions.

### 6.3 Explicit dependencies on Evidence-Verified Relationships

| # | Dependency | Needed for | If missing |
|---|---|---|---|
| D1 | Validated `supersedes` edge store with evidence + validator version | T4 via EVR instead of raw model rows | Phase 1 fallback: existing gates (§6.2.1) |
| D2 | Validated `confirms` edge (proposal → explicit linkage) | T7 / Option A rendering | Fallback: no confirmation links; proposals and decisions stay unlinked but individually correct |
| D3 | Edge lifecycle on source change (validated → revoked when excerpts stop resolving) | Ledger must not apply revoked edges; revocation itself becomes a transition trigger (`edge_revoked`) | Fallback: ledger re-checks excerpt containment at apply time (as today) |
| D4 | Per-folder EVR readiness signal | Phase 2 cutoff without mixed-semantics confusion | Fallback: global config flag, documented as coarse |

The ledger must be shippable and useful on fallbacks alone; EVR upgrades
edge *trust*, not ledger *existence*.

## 7. Query support

### 7.1 Current-state questions (exists; extend, don't replace)

"What is the current venue?" works today through `plan_memory` +
`synthesize`: current explicit records match (≥ 2 shared words),
`superseded`/`stale` are excluded, tainted units can't ground values, and
conflicts surface as pairs. The ledger adds one refinement: when the
winning claim has a `supersession_link` transition, the synthesized answer
appends a one-line provenance note ("Previously: Hall A — changed per
[citation].") using the stored `previous_*` snapshot, without changing the
answer's status logic.

### 7.2 Historical-state questions (new intent, existing machinery)

"What was the original venue?" / "What did we decide before Hall C?" is a
new intent family handled like the existing status-intent path
(`detect_status_intent` + `_status_plan`): deterministic, no model call,
history labeled as history.

- **Detection**: new `HISTORY_INTENTS` alongside `STATUS_INTENTS` —
  triggers like `original`, `originally`, `previous`, `previously`,
  `before`, `used to`, `what was`, `history of`, `changed from`,
  gated by the same scope-word + determiner discipline that fixed the
  "the proposal" hijack (`_term_counts`, `test_document_reference_*`).
  "What is the current venue?" must never fire this path.
- **Planning**: resolve the matter via the existing ≥ 2-word match over
  *all* records (including `superseded`, excluding `stale`-without-cause?
  No — include stale too, labeled), then order the matter's chain by
  transition sequence: earliest `extraction` = original.
- **Response**: reuse `status_listing_response` shape — one line per
  historical state with qualifier (`Superseded`, `Current`), citations to
  still-resolving evidence, notes for removed/changed sources. "Original"
  resolves to the first non-`stale` state in the chain; ties broken by
  record id (deterministic).

### 7.3 Change-history questions (new, ledger-native)

"How did the venue decision change over time?" reads `memory_transitions`
directly for the matched matter and renders the ordered chain:

```text
Venue decision history (3 states):
- Hall A [Decided → Superseded; changed per "venue is changed to Hall C"
  from final_confirmation.pdf]
- Hall B [Proposed; never confirmed — not a decision]
- Hall C [Current]
Each state above cites its sources below.
```

Rules: proposals appear labeled and never as states that held; conflict
episodes appear as annotations ("Hall A and Hall D conflicted 2026-10-28 –
2026-10-29") from `conflict_sweep`/`conflict_cleared` pairs, not as value
states; every line needs at least one displayable citation or the standard
removed/changed note, else the line is withheld with a count (consent rule
unchanged).

### 7.4 What stays out of the model path

All three query families resolve deterministically from stored rows. The
semantic review keeps its current role (wording multi-source factual
answers) and gains one rule: when `memory_transitions` context is present,
prefer its ordering over source-recency guessing. No new LLM calls for
history; the only model spend in the feature is zero (intake schema
unchanged in Phase 1).

## 8. API/UI implications

### 8.1 API (additive only)

- `GET /api/memory` — each record gains `history: {transitions: [...],
  superseded_by: record_id | null, supersedes: [record_ids],
  confirmed_by: record_id | null}`. Computed from the ledger tables;
  no new query params in Phase 1. Response stays paginated client-side
  as today (10/page in `app.js`).
- `GET /api/memory/history?folder_id=&record_id=` (new, Phase 2) —
  ordered transition chain for one record or one matter, with
  displayable-evidence resolution per current consent. Needed once chains
  grow beyond what the list payload should carry.
- `POST /api/memory/extract` — response gains `transitions: [...]`
  (the transition rows recorded this run), mirroring
  `conflicts`/`superseded`/`stale`.
- No changes to `/api/ask` contract: history answers reuse
  `status: answered` + `memory.claims` with qualifiers, exactly like
  today's status listings.

### 8.2 UI (Memory view + chat, minimal)

Existing surfaces to extend (`yaad/ui/app.js`, `index.html`):

1. **Memory table** (`renderMemory`): no new columns. The Status badge
   gains a history affordance — records with transitions show
   "Superseded · 2 changes" style suffixes reusing `status-badge` CSS.
2. **Inspect dialog** (`openMemoryRecord`): new "History" section under
   "Supporting evidence", rendering the ordered transition list from the
   `history` payload (trigger label, reason/excerpt, actor, timestamp,
   linked record titles). Read-only.
3. **Chat answers**: history-intent answers reuse evidence cards; the
   existing `is-historical` chip + "History" tag already covers
   superseded citations — no new card type. The one-line "Previously: …"
   note in current-state answers is plain answer text.
4. **Filter dropdown**: one new option, `History` exists already
   (stale/superseded/excluded); add `Changed` (records with ≥ 1
   transition) only if user testing shows the need — default to skipping
   it in Phase 1.

No new views, no timeline widget, no graph visualization (explicitly out
of scope per the no-graph-DB constraint and hackathon budget).

## 9. Testing strategy

Follow the existing `Store` harness pattern (`tests/test_memory_answers.py`:
disposable SQLite, `add_file`/`add_records`/`ask`, `cloud_enabled=False`
for deterministic paths). New file: `tests/test_memory_ledger.py`.

### 9.1 Temporal edge cases (each a test)

1. **Venue triad**: Hall A decided → Hall B proposed → Hall C confirmed
   superseding A. Assert: current query answers C; original query answers
   A; B never served as decided; transitions recorded in order.
2. **Proposal cannot replace**: proposal-only newer record with a
   `supersedes` edge ⇒ edge rejected (`proposal_cannot_supersede`),
   confirmed record keeps status, rejection reported.
3. **Newer timestamp, no authority**: same-matter newer record without a
   valid edge ⇒ `conflicted` pair (rival values) or coexisting records,
   never silent replacement.
4. **Out-of-order arrival**: C indexed before A (extraction sees the
   confirmation first) ⇒ once both exist, chain still orders A → C by
   edge direction, not `created_at`. Original query still answers A.
5. **Unverified relationship ignored**: raw `supersedes` row failing any
   existing gate (or lacking EVR receipt post-cutover) ⇒ rejected with
   reason; serving unchanged.
6. **Revoked edge**: supersession evidence edited so the excerpt no
   longer resolves ⇒ next sweep leaves target `superseded`?? — decision:
   target stays `superseded` (ledger is append-only; reversal needs a
   new explicit edge or user action), and a `stale_sweep`/`edge_revoked`
   annotation is recorded. Test pins whichever behavior is chosen.
7. **Conflict episode**: A vs D conflict, then C supersedes A ⇒ history
   shows the conflict interval annotation; current answers C; D's fate
   (still conflicted vs restored) asserted explicitly.
8. **Stale-then-restored**: evidence deleted → `stale` (+transition);
   file restored + re-extracted → intake status (+`re_extraction`);
   chain shows both, nothing lost.
9. **Consent revocation over history**: revoke folder consent ⇒ current
   queries exclude, history listing shows withheld counts, stored
   transitions untouched; re-grant restores serving.
10. **Paraphrase split**: same matter, disjoint key phrases ⇒ two records,
    no false chain link; dupe-hint may fire but no transition is written
    (hints never transition — pin this).
11. **Multi-hop chain**: A → B → C supersessions ⇒ original=A,
    current=C, full chain rendered; `superseded_by`/`supersedes` links
    consistent at every hop.
12. **Amended-in-place file**: source text edited (same evidence_id
    impossible — ids hash content — so new unit, old unit deleted) ⇒ old
    record goes `stale`, new record is separate; no automatic chain
    unless a valid edge names it. Pins "no silent chaining".

### 9.2 Regression scenarios (existing suite must stay green unmodified)

- Full suite (`pytest`, ~250 tests) passes with zero changes: all schema
  additions are additive, all query changes are new intents that must not
  fire on existing test questions (add a guard test: every question
  string in the current suite produces identical `status` before/after —
  snapshot the ask-results over fixture stores).
- Hijack guards: extend `test_document_reference_does_not_trigger_*`
  style coverage to history triggers ("the history of the club" as a
  document topic must not fire the history intent).
- Migration idempotency: run `initialize()` + backfill twice over a
  fixture DB; assert single backfill rows and unchanged serving.

## 10. Migration and backward compatibility

- **Schema**: two `CREATE TABLE IF NOT EXISTS` + one backfill, wired into
  `Database.initialize()` next to the existing amount-columns pattern
  (`yaad/db.py:46-51`). Old code reading a new DB is unaffected (new
  tables ignored); new code reading an old DB backfills on open.
- **Rollback**: `DROP TABLE memory_transitions; DROP TABLE
  memory_matter_links;` restores the pre-ledger state with zero loss to
  existing tables. No column is added to `memory_records` in Phase 1.
- **API**: additive fields only; existing clients ignore them.
- **No re-extraction required**: backfill synthesizes honest
  starting-point rows; chains deepen as new extractions run.
- **Other-session safety**: this design touches the same sweep functions
  the EVR session may be editing. Coordinate on `apply_supersession_links`
  signature changes (the optional `transitions` param is designed to merge
  cleanly) or land EVR first and rebase the ledger's Phase 2 onto it.

## 11. Risks, trade-offs, and implementation phases

### 11.1 Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Matter matching too weak (paraphrased rivals miss) — audit's top finding — makes chains incomplete | High | Chains are explicitly best-effort; UI labels "known history"; never claim completeness. Do not "fix" matching inside this feature. |
| History-intent hijack (mirrors the fixed "the proposal" bug) | Medium | Same scope-word + determiner discipline; dedicated hijack tests (§9.2). |
| Scope creep into timeline UI / graph views | Medium | Phase 1 is list + dialog text only; defer visuals past the hackathon. |
| Merge conflicts with parallel EVR session | Medium | Optional-param sweep signatures; agree on `apply_supersession_links` ownership now. |
| Over-promising "original": earliest-known ≠ true-original | Low-Med | Copy says "earliest known"; backfill rows labeled as backfill. |

### 11.2 Trade-offs accepted

- **Ledger records decisions, not truth**: it orders what extraction +
  sweeps concluded. A missed edge is a missing link, surfaced as a gap,
  never interpolated.
- **No valid-time extraction in Phase 1**: content dates (`date_value`)
  keep their current meaning. True bitemporal queries ("what was decided
  as of March?") wait for EVR-era extraction upgrades.
- **Snapshots duplicate small strings**: bounded (~5 short columns),
  no FTS, no indexes beyond `(folder_id, record_id)`.

### 11.3 Implementation phases

- **Phase 0 (0.5 day)**: schema + backfill + `transitions` plumbing
  through the three sweeps and both call-sites; extraction summary
  reports transitions. No query/UI change. Shippable independently.
- **Phase 1 (1–1.5 days)**: T4 proposal-claimant guard; history +
  change-history intents + matter-chain rendering; `GET /api/memory`
  `history` payload; Memory-dialog History section; "Previously" note;
  `tests/test_memory_ledger.py` (§9). Demo-ready.
- **Phase 2 (post-hackathon / with EVR)**: EVR edge consumption + cutoff;
  `confirms` links + T7/Option A; `GET /api/memory/history`; `user_flag`
  review actions; valid-time extraction if EVR provides dates.

## 12. Hackathon recommendation

**Implement Phase 0 + Phase 1 only if extraction quality and question
matching get measured first; otherwise defer.**

Rationale: the independent audit's central finding stands — the lifecycle
machinery (which this ledger extends) is proven only around mocked models,
while matching is 2-word lexical overlap and extraction quality on real
inference is unmeasured. A temporal ledger over missed rivals and
mis-extracted records produces confident-looking histories that are wrong
in ways judges can spot with one paraphrased question ("we moved the venue
to Hall C" vs. "Hall C is the new venue" may not even chain). The feature
is also additive-only, so deferring costs nothing architecturally.

If the team proceeds anyway: timebox to 2 days, demo the venue triad
end-to-end (§9.1.1) as the single proof, and do not start Phase 2 before
the deadline. The highest-value hour is not ledger code — it is the
pre-existing gaps (stemming/paraphrase matching, one real-model extraction
eval) that decide whether any history shown is trustworthy.

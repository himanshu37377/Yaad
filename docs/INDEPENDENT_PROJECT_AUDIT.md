# Independent Project Audit — Yaad

Date: 2026-10-30. Method: code inspection, test-suite execution (245 passed),
safe eval-script execution, and four disposable experiments (no production writes,
no model spend, no real-model inference). No application code, tests, fixtures,
or configuration were modified for this audit.

## 1. Executive verdict

**Overall verdict: Strong concept, insufficient validation.**

Yaad is a genuinely differentiated architecture, not a RAG wrapper: persistent,
validated memory records with a real lifecycle (current / conflicted /
superseded / stale), deterministic conflict and supersession rules, consent-aware
serving, and per-claim provenance carried from extraction to UI. That design is
substantially beyond ordinary document chat, and the engineering is careful in
places that matter (citation-ID enforcement, no silent fallbacks, honest
statuses, 245 passing tests).

It is not yet proven. The central claim — that structured memory answers better
than chunk retrieval — has no measured evidence: retrieval and memory matching
are both pure keyword overlap with no stemming, extraction quality on a real
model was never evaluated, the eval harness is n=4, and this audit confirmed
real defects including a crash-on-valid-output bug (H1) and a silently broken
local-memory serving path (H2). Submittable as an entry; not yet credible as a
reliability story.

Ready to submit ≠ proven reliable. Yaad is close to the former and far from the
latter.

## 2. What Yaad actually does

Pipeline traced end to end in code: `Indexer.index_folder` (`yaad/scan/worker.py:101`)
discovers PDF/TXT/DOCX/image/audio files, extracts local text (PyMuPDF,
python-docx), and — only with folder consent plus global cloud enablement —
calls Meta APIs for PDF facts, image understanding, OCR, and transcription.
Units land in SQLite+FTS5 with stable hash IDs (`yaad/evidence/ids.py:4`).
Memory extraction (`POST /api/memory/extract` → `yaad/memory/extract.py:135`)
is explicit-only: one capped model call over consented units, deterministic
validation (`validate_candidate`, `yaad/memory/records.py:168`), idempotent
upsert, then conflict/supersession/stale sweeps. Questions
(`POST /api/ask` → `answer_question`, `yaad/query/answer.py:300`) run FTS
retrieval, memory planning (`plan_memory`, `yaad/query/memory_answer.py`),
exact value extraction, status classification, and an optional consented
model review whose citations are mechanically checked against retrieved IDs
(`answer.py:257-270`). Statuses (`answered`, `conflict`, `ambiguous`,
`partial`, `unverified`, `not_found`, `cloud_unavailable`,
`local_unavailable`, `error`, `indexing`) render as badges plus evidence
cards, provenance drawer, and a Memory view with per-record serving verdicts.

Verified present in code and tests: multimodal ingestion with per-file
partial/failed honesty, audio timestamp bounding, citation dedup, consent
revocation semantics, evidence-union preservation across re-extraction,
conflict-never-settles, local Ollama adapter behind an explicit provider
switch with no-cloud-fallback tests.

Verified absent or weak: stemming/synonym/paraphrase handling anywhere in
retrieval or matching; chunking in extraction (whole corpus, one prompt);
measured extraction/answer quality on real inference; folder delete; server-side
ask timeout; pagination on memory listing.

## 3. Core differentiation analysis

What Yaad does that ordinary RAG does not (all verified in code):

1. **Persistent validated memory.** Records survive source edits with
   re-validated excerpts (`resolving_supports`, `records.py:345`); repeat
   extraction unions corroboration instead of replacing it
   (`test_later_run_with_fewer_links_preserves_corroboration`).
2. **Lifecycle with teeth.** Stale, superseded, and conflicted records are
   structurally excluded from factual answers (`answer_eligibility`,
   `records.py:609`; serving tests in `test_memory_serving.py`), while
   staying inspectable with history labels.
3. **Deterministic cross-file conflicts.** Same-matter rivals (date- and
   amount-aware, `records.py:366`) conflict instead of merging, both sides
   are presented, and conflicts can never be served as settled facts —
   even inside partial answers (`answer.py:507-516`,
   `test_partial_response_cannot_settle_a_known_conflict`).
4. **Validated supersession.** Replacement links require distinct valid
   evidence, a valid target, and a relatedness gate; bogus links are
   rejected with receipts (`apply_supersession_links`, `records.py:459`).
5. **Consent as a serving invariant,** not a setup flag: revocation
   immediately excludes records and blocks new model calls, with history
   preserved (`test_consent_revocation_*`, `test_listing_respects_consent`).

Where the thesis is weakest (the skeptical core):

- **Memory matching is 2-word lexical overlap** (`MATCH_WORDS = 2`,
  `memory_answer.py:22`, no stemming). Paraphrased questions miss
  records; the "memory advantage" evaporates whenever the user doesn't
  reuse the sources' vocabulary. This is partially documented as a
  stemming gap but is broader than stemming.
- **Extraction quality is unmeasured.** 245 tests prove the machinery
  around the model (validation, lifecycle, serving), not that the model
  extracts the right records. No precision/recall on real inference.
- **"Understanding change" is deterministic bookkeeping over
  model-extracted statements,** not temporal reasoning. Changed-date
  handling is conservative by rule (`_rivals` requires same matter key),
  and key phrases are model-worded, so paraphrased rivals silently miss
  each other (false negatives) while coincidentally identical key
  phrases can false-positive.
- **Answer prose is unverified.** Citation IDs are strictly validated;
  the model's answer *text* is used verbatim (`answer.py:234-256`).
  Grounding holds for citations, not sentences.
- **Derived-fact and image units are model-written text stored as
  first-class evidence,** and the UI quotes them indistinguishably from
  verbatim source text (no `derived_fact`/`image_understanding`
  handling in `yaad/ui/app.js`).

Net: the memory architecture adds genuine capabilities RAG cannot
(matter lifecycle, conflicts, supersession, consent-aware serving), but
its value hinges on extraction quality and question matching, neither
of which is measured. A plain RAG baseline would likely match Yaad on
single-fact lookup and abstention; Yaad should win on conflicts,
supersession, staleness, and status queries — if the matching works.

## 4. Reliability and evaluation matrix

Scale of existing proof: 245 unit/integration tests (mocked models),
one real-model capability suite (10 requests, recorded usage), one
real-model rehearsal (5 requests, synthetic fixtures), eval harness
n=4 (3 retrieval + 1 abstention, re-run in this audit: 3/3, 1/1).
No human-rated quality, no paraphrase set, no multi-run stability data.

| # | Scenario | Covered? | Level | Guarantee | Unverified | Likely failure | Exposing experiment |
|---|---|---|---|---|---|---|---|
| 1 | Direct factual recall | Yes (`test_reliability_fixes`, retrieval tests) | integration (mocked) | Exact regex extraction + citation | Real-model phrasing; non-English | Paraphrased question misses FTS terms | 20 paraphrased lookups on Autumn Fest, measure hit rate |
| 2 | Cross-document recall | Yes (`test_corroboration_*`, `test_exact_reported_*`) | integration (mocked) | Audio-anchored expansion + injection | Non-audio multi-hop (no expansion path) | Second-hop file outside top-15 | Non-audio 2-hop question battery |
| 3 | Conflicting values | Yes (`test_fee_rivals_*`, `test_same_date_*`) | integration (mocked) | Rival detection + both-sides + never-settle | Paraphrased rivals; 3+ way conflicts (only first pair synthesized) | Same fact, different wording → silent single answer | Paraphrased ₹150/₹200 pair E2E |
| 4 | Decision changes | Yes (`test_valid_room_replacement_*`) | integration (mocked) | Validated links; old kept as History | Model-proposed link quality on real text | Bogus demotion (guarded) or missed update (likely) | Real extraction on edited Autumn Fest copy |
| 5 | Proposal vs decision | Yes (`test_proposal_listing`, synth labels) | integration (mocked) | `proposal_only` labeling through pipeline | Model mislabeling kinds at extraction | Suggestion extracted as explicit (unmeasured) | Spot-check 30 extracted records vs sources |
| 6 | Unresolved questions | Yes (`test_unresolved_listing`, `test_explicitly_undecided_*`) | integration (mocked) | Undecided-only gating + explicit markers | Marker-list paraphrase fragility (`UNDECIDED_MARKERS` fixed phrases) | "Not yet settled" variants misclassified | Paraphrase battery on markers |
| 7 | Negative retrieval | Yes (`test_genuinely_unanswerable_*`, `test_question_with_no_evidence_*`, eval 1/1) | integration + tiny E2E | `not_found`/`unverified`, empty evidence | Adversarial near-miss phrasing | Over-eager review answering from weak rows | Near-miss question set |
| 8 | Misleading lexical overlap | Partial (`test_unrelated_expansion_terms_do_not_retrieve`) | unit | No topic expansion; exact-ID citations | 2-word memory false matches; BM25 top-k noise | Wrong-record match on shared generic words | Distractor-document suite (same words, other event) |
| 9 | Evidence integrity | Yes (`test_rejects_unknown_evidence_*`, excerpt containment) | unit/integration | ID + excerpt + key-phrase containment | Assignee/title/detail beyond key words unchecked (M1); model-written quotes undisclosed (M2) | Invented assignee/specifics rendered as fact | Adversarial extraction with invented details |
| 10 | Consent revocation | Yes (`test_consent_revocation_*`, `test_listing_respects_consent`) | integration (mocked) | Immediate serving exclusion, history kept | Mid-flight TOCTOU (tiny window, accepted) | None likely | Already covered; keep |
| 11 | Partial ingestion | Yes (`test_ingest_limits*`, partial/failed/skipped) | integration | Per-file honest statuses + errors | Consent-toggle error overwrite (L5); large-file behavior | Misleading success (none found; statuses honest) | Corrupt-file battery |
| 12 | Re-ingest / re-extract | Yes (`test_unchanged_repeat_*`, preservation suite) | integration | Idempotent upserts, evidence union, dead-row staleness | Blank-detail crash (H1); concurrent multi-process runs | Whole-run IntegrityError (confirmed, H1) | Fixed by H1 fix + repeat-run test |

Blind spots in evaluation design: fixtures are tiny, single-domain, and
author-shaped (model-friendly phrasing); no distractor documents; no
paraphrase battery; no extraction quality measurement; no latency/cost
measurement under realistic load; capability suite tests the API, not
Yaad's use of it.

## 5. RAG-baseline comparison plan

No comparison exists. Minimal fair experiment (no new infrastructure):

- **Corpus:** Autumn Fest 4 files + 4 distractor files (same vocabulary,
  different event) + 1 edited copy pair (Room A→B) + 1 conflicting pair
  (₹150 vs ₹200). All small, hand-verifiable.
- **Baseline:** FTS top-8 retrieval + one `reason_json` review call with
  the same schema, no memory tables, citations required. Deliberately
  competent: same model, same evidence pool.
- **Question set (24):** 6 direct recall (3 paraphrased), 4 cross-file,
  4 conflict, 3 supersession/current-vs-history, 3 proposals/unresolved,
  4 abstention/near-miss.
- **Rubric (human-judged, blinded):** correctness, abstention
  appropriateness, conflict detection, current-vs-history accuracy,
  citation support per claim.
- **Prediction:** baseline matches Yaad on 1–2 and 7; Yaad wins on 3–6
  only if matching hits. If Yaad does not win 3–6, the memory thesis
  fails and the honest response is narrower claims, not more features.

Cost: one afternoon of fixture+question authoring, one judging pass,
negligible model spend. This is the single highest-value experiment.

## 6. Technical findings

### High

**H1 — Blank `detail` crashes entire extraction (confirmed, reproduced).**
`EXTRACTION_SCHEMA` requires `detail` as a string, so `""` is schema-valid;
`_normalize_blank` (`records.py:116`) converts it to `None`; `upsert_records`
(`records.py:273`) inserts it into a `NOT NULL` column → `IntegrityError`,
whole transaction rolls back, and the billed model call leaves no receipt
(API returns 500, not the designed 409). Repro: any candidate with
`"detail": ""` — verified in a disposable `:memory:` run
(`validate: []`, then `IntegrityError: NOT NULL constraint failed:
memory_records.detail`). Fix: `candidate.get("detail") or ""` in upsert
and/or require non-empty detail in validation; wrap per-candidate insert
errors as rejections. **Necessary before submission: yes (P0).**

**H2 — Local mode extracts but never serves memory (confirmed, reproduced).**
`select_units(require_consent=False)` lets local extraction read all units,
but `authorized_supports` (`memory_answer.py:42`) and
`_listing_displayable` unconditionally require file+folder cloud consent,
so local records get zero supports and are silently skipped (`used: False`,
units-only answers). Reproduced end to end: local settings, consent off →
`authorized_supports: []`, memory unused. Same root cause makes `/api/memory`
label local records "excluded: current consent does not authorize these
sources". Fix: provider-aware authorization (consent gates cloud egress,
not local serving). **Necessary: yes if local memory is claimed (P0),
otherwise narrow the claim.**

### Medium

**M1 — Assignee/title/detail specifics unverified (confirmed).**
`validate_candidate` (`records.py:168`) checks IDs, excerpt containment,
key-phrase words, and date_raw — but never that `assignee`, `title`, or
`detail` beyond key words appear in excerpts. The model can invent
"Riya" or amounts that render as record statements. Only invented
*key phrases* have a test (`test_rejects_invented_key_phrase`). Fix:
containment check for assignee and content words, or label statements
model-composed. **P1.**

**M2 — Model-written evidence quoted as source text (confirmed).**
Derived PDF facts (`worker.py:197`) and all image-understanding text are
model-generated, stored as units, and rendered in the evidence drawer as
blockquotes with zero `derived_fact`/`image_understanding` distinction in
`app.js`. The excerpt check bounds fabrication for PDF facts; image text
has no equivalent. Fix: provenance tag ("AI-composed from page 1").
**P1** — directly affects the grounding claim's credibility.

**M3 — Photo intent misfires on substrings (confirmed).**
`photo_intent` (`photos.py:8`) uses substring match: verified
`photo_intent("what a picturesque venue") == True` ("imagery",
"pictured" likewise), routing text questions down the photo-only path
toward a wrong "No matching indexed photos". Fix: word boundaries.
**P1.**

**M4 — Keyword-only retrieval and matching (confirmed structurally).**
FTS5 default tokenizer, no stemming (`db.py:13`); memory matching is
2-shared-words (`memory_answer.py:22,303`). Paraphrase fails at both
layers. Documented only as a "stemming gap" — understated. Cannot be
fixed properly pre-submission; **P0 to measure (paraphrase probe) and
disclose honestly.**

**M5 — Unchunked whole-corpus extraction prompt (confirmed).**
`build_extraction_prompt` concatenates all consented units into one call;
the $0.25 cap is the only guard (`extract.py:104,158`). Large folders get
refused or giant prompts; per-unit cost/latency unmeasured. **P2**
(design chunking; disclose now).

**M6 — Answer prose unverified (confirmed).**
`_semantic_review` validates citation IDs rigorously but uses
`result["answer"]` verbatim (`answer.py:234-256`). Text can contradict
its own citations. **P1:** derive displayed answers from validated
claims where possible, else disclose.

**M7 — Local photo re-description hard-blocked (confirmed).**
`redescribe_image` (`worker.py:355`) raises unless cloud enabled *and*
consented, even though local vision works. Inconsistent with the local
story. **P2** (or disclose).

### Low (confirmed, fix if time)

- **L1:** `synthesize` uses only `conflicts[0]`; extra pairs live only in
  the API payload (`memory_answer.py:345`). Conversation UI never renders
  `memory.claims/conflicts` sides at all — only the badge. P3.
- **L2:** Memory badge shows on match even when units answered without
  synthesis (defensible via injected evidence; note the nuance). P3.
- **L3:** `memory_pseudo_values` skips identifier-type memory values
  (`memory_answer.py:531`). P3.
- **L4:** No server-side `/api/ask` timeout (model 90s / local 300s bound
  calls; UI aborts at 120s). P3.
- **L5:** Consent toggle overwrites file error text
  (`api/main.py:85`), erasing original failure reasons. P3.
- **L6:** No folder delete; unpaginated memory list; dead `mask_value`;
  unused `httpx` dep; `fitz` deprecation warning. P3.
- **L7 (suspected, unverified):** torn reads if ask runs mid-extraction
  (no snapshot); self-heals next query. P3.

### Investigated and cleared (do not report as issues)

- FK cascades: `PRAGMA foreign_keys=ON` per connection (`db.py:37`).
- Cross-provider cache poisoning: cache key includes model
  (`worker.py:449`); verified by reading.
- Eager `checked_model()` crash in `_egress`: hypothesized, then
  **falsified by experiment** (short-circuit; correct error surfaces).
- `describe_photo` dead method: exists on Protocol with no callers;
  harmless.
- No-auth API: binds localhost by default; acceptable for the demo
  context, noted as an assumption.

## 7. Meta AI and local-model verification

**Verified with real inference:** Meta Model API across PDF understanding,
image understanding (+two-image), structured JSON, and ASR transcription —
10/10 HTTP 200 in the capability suite with recorded latency/tokens/cost
(`docs/capabilities.md`, `docs/capability_usage.json`), plus a 5-request
integration rehearsal (`eval/report.md`: conflict answer, cited image
answer, ~$0.00869). This is genuine multi-surface Meta AI use, not a
single chat call.

**Implemented but never verified on real inference:** memory-extraction
output quality (all extraction tests use stub clients), answer-review
quality beyond one rehearsal, and the entire local path.

**Local provider:** adapter + factory + gates + 13 contract/no-fallback
tests are real and passing, but it has never talked to weights; H2 shows
local memory serving is silently broken; `redescribe` is cloud-only;
real-Gl justificate hardware performance unknown. The "local-first"
claim currently rests on mocked tests plus honest docs — credible
engineering, unproven behavior.

**Accuracy of provider/cost/privacy statements:** receipts correctly
record provider/model/cost; token counts are runtime-reported (documented
as non-comparable); no-fallback is structurally enforced (single factory
construction site, verified by grep) and behaviorally tested. The one
overstatement risk is M2 (model-written quotes shown as source text).

**Open-weight bonus:** no eligibility evidence found in-repo; treat as
unknown. Implementation alone cannot qualify; verified inference +
eligibility confirmation are both missing. Do not claim.

## 8. Hackathon judging scorecard

Weights: Impact 30%, Meta AI 25%, Tech Depth 20%, Demo 25%.

**Impact: 5/10 (confidence: medium).** Evidence: real problem
(scattered personal decisions across files), honest abstention and
conflict handling. Weakness: no user validation, narrow desktop-local
scope, manual extraction step. Upward: one real-user story + measured
win on conflict/change questions.

**Use of Meta AI tooling: 6/10 (confidence: medium-high).** Evidence:
four API surfaces with consent gating, usage accounting, recorded
rehearsal; local Glimmer adapter (unverified). Weakness: model is
deliberately shackled (good reliability, less visible "AI magic");
local story unproven. Upward: verified local extraction run + demoed
multimodal answer (audio+image+PDF triangulation).

**Technical Depth & Execution: 7.5/10 (confidence: high).** Evidence:
lifecycle state machine, validation rigor, idempotency, consent
invariants, 245 tests, honest failure taxonomy. Weakness: keyword-only
retrieval, unchunked extraction, confirmed H1/H2. Upward: fix H1/H2,
measured RAG comparison.

**Demo: 6/10 (confidence: medium).** Evidence: working UI, conflict
badges, provenance drawer, rehearsed traces. Weakness: rests on tiny
fixtures; no single wow moment; 30-second story ("memory with a past
tense") is clear in UI but unproven live. Upward: the 3-minute demo
below, rehearsed verbatim.

**Weighted provisional: 6.0/10.** Competitive engineering entry;
not yet a validated product story.

**30-second test:** what (memory engine for your files) ✓; different
(conflicts/history/consent, not chat) ✓; problem (decisions rot across
files) ~; technical meaning (validated memory lifecycle) ✓-ish;
Meta AI where (badges, egress log) ✓. Passes structurally; proof is thin.

**3-minute demo (cut everything else):** (1) 0:00–0:40 — index 4 Autumn
Fest files, show per-file honesty; (2) 0:40–1:30 — corroboration
question with citations; (3) 1:30–2:20 — ₹150/₹200 conflict → both
sides, never-settled; (4) 2:20–3:00 — revoke consent → answer changes
with history intact. Cut: photo tour, capability suite, themes, settings,
local mode (until H2 fixed and weights run).

## 9. Prioritized improvement plan

**P0 — Submission blockers:**

1. **Fix H1** (blank-detail IntegrityError + missing receipt).
   Verify: extraction with `"detail": ""` candidate → rejection, 200
   response, receipt present. Success: suite + new regression green.
2. **Fix H2 or narrow local claims.** Either provider-aware serving
   authorization + serving tests, or docs state local memory serving is
   unimplemented. Verify: local+unconsented E2E memory answer. Success:
   `used: True` with consent off, or claim removed.
3. **Paraphrase probe + honest disclosure (M4).** 20 reworded lookups on
   Autumn Fest; publish hit rate in `eval/report.md` whatever it is.
   Success: measured number, disclosed limits.
4. **Demo conflict fixture + verbatim rehearsal.** Two-file ₹150/₹200
   pair indexed live; conflict answer reproduced twice. Success: the
   4-beat demo runs without narration patches.
5. **One real-model extraction spot-check.** Single Autumn Fest run,
   hand-score accepted records for precision + invented specifics.
   Success: measured precision, M1 sized with evidence.

**P1 — Highest value:** fix M3 (photo routing, 1 line + test); M2
provenance tags on model-written quotes; M6 constrain or disclose
answer prose; M1 assignee/detail containment; run the RAG-baseline
mini experiment (§5).

**P2 — If time permits:** extraction chunking design; M7 local
redescribe; distractor-document eval set; server-side ask timeout.

**P3 — Defer:** L1–L7, dead code, dependency hygiene, folder delete,
pagination, themes, any new feature (including Settings UI for
provider switching — useful, but cosmetics before proof).

## 10. Final three-action checklist

1. Fix H1 and H2 (or narrow the local claim) — correctness blockers.
2. Run the RAG-baseline mini experiment (§5) — decides whether the
   thesis is demonstrated.
3. Rehearse the 4-beat demo verbatim on real fixtures — decides whether
   judges see it.

## 11. Evidence appendix

**Inspected:** `README.md`, `ARCHITECTURE.md`(absent — see note),
`docs/*.md`, `eval/report.md`, `eval/run_eval.py`,
`scripts/make_synthetic_data.py` (read only, not run),
`yaad/query/answer.py` (full), `yaad/query/memory_answer.py` (full),
`yaad/query/retrieve.py` (full), `yaad/query/photos.py` (full),
`yaad/memory/records.py` (§§ identity/rivals/supersession/stale/
eligibility/duplicates), `yaad/memory/extract.py` (full),
`yaad/scan/worker.py` (full), `yaad/api/main.py` (full),
`yaad/db.py` (full), `yaad/llm/*` (full), `yaad/cli.py`,
`yaad/ui/app.js` (evidence/memory/status/conflict rendering),
`yaad/ui/index.html` (views), `tests/` (names + key bodies across
21 files), `yaad_cross_file_test/` (4-file corpus listing).

Note: no `ARCHITECTURE.md` exists; architecture claims were verified
against code, not docs.

**Commands run (all safe, disposable state):**

- `.venv/bin/python -m pytest tests/ -q` → 245 passed.
- `.venv/bin/python eval/run_eval.py` → 3/3 retrieval, 1/1 abstention.
- `photo_intent` probe → `"picturesque"` misfires True (M3).
- Blank-detail `:memory:` repro → `IntegrityError` (H1).
- Local-serving repro (tmp DB) → `authorized_supports: []`,
  `memory.used: False` (H2).
- Junk-model local repro (tmp DB) → correct unreachable error;
  eager-`checked_model` crash hypothesis falsified.
- `node --check` not re-run (no UI changes in scope; prior turn green).

**Could not verify:** real-model extraction/review quality; local
weights behavior or hardware performance; open-weight bonus eligibility
or criteria; production-database behavior (untouched by design);
multi-user/concurrent-server behavior; broad reliability beyond
fixtures; actual user value.

**Secrets:** none accessed or reproduced; `.env` never read.

---

## Addendum — 2026-10-29 validation (original findings preserved above)

H1 and H2 are **fixed and regressed** (see
`docs/VALIDATION_RESULTS.md` §§2–3 for repro, root cause, and tests);
the §5 comparison has been **run as a mocked diagnostic** (§§4–10).
Material changes to this audit's conclusions:

- H1 (confirmed): resolved. Empty detail rejected pre-persistence;
  receipts recorded on persistence failure.
- H2 (confirmed): resolved. Provider-aware serving: local answers
  without cloud consent, cloud still gated, revocation/switch covered.
- §5 experiment: executed (mocked, 24Q). Yaad 18/24 vs baseline
  12/24; explicit conflict verdicts 4/4 vs 0; status listings work;
  direct recall/history tied; abstention inconclusive (mock quoter).
  Differentiator **partially demonstrated, not proven** — the
  "insufficient validation" verdict stands until a real-model run.
- **New High finding (unfixed):** status-intent hijack — factual
  questions mentioning "the proposal" divert to proposal listings
  (`detect_status_intent` scope words too broad). Repro: X2/X4 in
  `docs/VALIDATION_RESULTS.md` §8.
- **New Medium finding (unfixed):** `proposal_only` is not surfaced
  in factual-path answer prose (P1).
- Test count: 245 → **255 passed**; eval still 3/3 + 1/1.

## Addendum 2 — 2026-10-29 real-model follow-up (see VALIDATION_RESULTS.md §12)

- Status-intent hijack **fixed** (determiner rule + word-aware
  memory-scope override, 2 regression tests). Suite: **257 passed**.
- Real-model mini-run (muse-spark-1.3, 10Q×2, $0.0716): **baseline
  8.5/10 beat Yaad 7.5/10**. Real review closed the conflict and
  abstention gaps; Yaad's memory added nothing correct and harmed
  two answers. Differentiator still unproven.
- **New P0 (unfixed):** conflict-override hijack — an irrelevant
  false conflict pair answered an abstain-question (G3 repro).
- **New P1 (unfixed):** real extraction emitted value-embedding
  keys (missed true ₹150/₹200 pair, false-paired Autumn-200 with
  Spring-100), no supersession link, and proposal→unresolved
  mislabel.
- Measured (pre-existing): review-row truncation caused false
  "no corroboration" claims (X1); missing stemming lost N2 (D3).

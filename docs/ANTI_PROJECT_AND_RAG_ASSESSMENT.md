# Anti-Project and RAG-Differentiation Assessment

Date: 2026-10-29. Read-only audit; no code, tests, fixtures, or existing
reports modified; no billable model calls made. The sole write is this file.

Sources inspected: `README.md`, `docs/INDEPENDENT_PROJECT_AUDIT.md`
(incl. two addenda), `docs/VALIDATION_RESULTS.md` (§§1–12),
`docs/EVALUATION_TRACES.md`, `docs/traces/*.json`,
`docs/capabilities.md`, `docs/capability_usage.json`, `eval/report.md`,
implementation (`yaad/query/answer.py`, `yaad/query/memory_answer.py`,
`yaad/memory/records.py`, `yaad/memory/extract.py`,
`yaad/query/retrieve.py`, `yaad/llm/*`, `yaad/scan/worker.py`,
`yaad/api/main.py`), test inventory (257 collected), repo/git state.
The participant guide was NOT independently retrieved; all statements
about official rules below come from the task brief's summary and are
labeled accordingly. Deadline taken as stated: 2026-11-04.

Evidence labels: **[code]** verified from implementation,
**[test]** demonstrated by automated tests, **[real]** demonstrated
by the real-model experiment (muse-spark-1.3, §12.4), **[mock]**
demonstrated only under mocked models, **[infer]** inference,
**[unver]** unverified.

## 1. Executive verdict

**Classification: Differentiated architecture, but evidence of
user-facing advantage is insufficient.**

Yaad is technically a RAG application with a real, structural,
persistent-memory subsystem — not a basic RAG application. The memory
layer (validated records, lifecycle states, deterministic conflict /
supersession / staleness rules, consent-aware eligibility, status
listings, deterministic synthesis) is genuinely beyond tutorial-grade
RAG **[code][test]**. But on the only real-model head-to-head slice
(10 questions), the memory-less baseline won 8.5/10 to 7.5/10, memory
added zero correct answers, and memory machinery caused both of
Yaad's distinctive failures **[real]**. The architecture is
differentiated; a user-facing advantage is unproven and currently
measures negative.

**Anti-project risk: Medium.** "Basic RAG Application" is the only
live category. A careful judge should not classify Yaad as basic,
but a strict or high-speed triage plausibly could, because observed
real-model behavior closely resembles a good prompted baseline. The
larger disqualification exposure is separate: the repository has no
git history, no remote, and no timeline evidence, while the brief
states the guide warns that unidentified hackathon-period work can
mean immediate disqualification at judges' discretion.

**Recommendation: stop coding; submit with a narrowed, honest claim
framed around consent-aware memory bookkeeping (conflicts, change,
history, abstention with citations) — not "memory beats RAG."**
Invest remaining time in the 3-minute video, public-repo hygiene,
and a written new-work statement.

## 2. Technical classification

**Selected: C — RAG application combined with a persistent
structured-memory subsystem** (D's machinery present; D's product
claim unproven).

Why not A (basic RAG): the memory subsystem is not prompting or
polish. Records persist in SQLite with validated identity keys,
statuses (`decided/open/conflicted/superseded/stale`), kinds
(`explicit/proposal/unresolved/inferred`), unioned evidence with
re-validation, rival detection over normalized amounts/dates,
relatedness-gated supersession, staleness sweeps, and serving
eligibility that excludes stale/superseded/inferred records from
factual premises **[code]** (`yaad/memory/records.py`:
`validate_candidate`, `upsert_records`, `mark_conflicts`,
`apply_supersession_links`, `mark_stale`, `answer_eligibility`;
`yaad/query/memory_answer.py`: `authorized_supports`,
`plan_memory`, `synthesize`, status listings). 257 tests pin this
behavior with mocked models **[test]**. No reasonable definition of
"basic" covers a tested stateful lifecycle.

Why not D (genuinely differentiated engine): on ordinary questions
the RAG path dominates answer selection. `answer_question`
(**[code]**, `yaad/query/answer.py:316-549`) retrieves FTS rows,
plans memory, extracts exact values, and reviews with the model;
memory contributes injected rows plus three override paths only:
status listings, conflict-pair override (`:508-516`), and
synthesis when the units path yields
ambiguous/unverified/cloud_unavailable (`:517-523`). Matching on
both sides is lexical (`MATCH_WORDS = 2`, no stemming)
**[code]**. Real-model evidence shows the overrides firing
wrongly (G3 false-conflict verdict overriding abstention) or not at
all where needed, with zero net correct answers attributable to
memory on the 10Q slice **[real]**. D requires demonstrated
product distinction, which does not exist.

Why C rather than B: B ("useful engineering + citations") understates
a real state machine that answers question classes chunk-retrieval
cannot express from retrieval alone ("what is conflicted / stale /
superseded / still undecided?" answered from stored state with
provenance). Those paths are narrow and only mock-demonstrated, but
they are architectural, not cosmetic **[code][mock]**.

Direct answers to the brief's inspection points:

- *How much is ordinary retrieval+generation?* Most factual answers:
  FTS top-k → exact patterns → model review → citations **[code]**.
- *Does memory materially change selection/correctness?* Sometimes
  changes selection (injection, overrides); on the real slice it
  changed correctness only negatively (G1, G3) **[real]**.
- *Lifecycle enforced structurally?* Yes: statuses, sweeps, and
  eligibility gates are code + schema, not prompts **[code][test]**.
- *Do conflict/supersession/history work reliably?* Deterministic
  rules work on well-formed input **[test]**; real extraction broke
  all three (false pair, missed link, mislabeled proposal) **[real]**.
- *Answers retrieval would not produce?* Status listings over stored
  state (C4/U2-style) **[mock]**; deterministic conflict verdicts
  **[mock]**; nothing demonstrated with real models **[real]**.
- *Meaningful capability vs complexity?* Consent-aware history and
  status bookkeeping are meaningful capabilities **[code][test]**;
  whether they outperform prompted RAG behaviorally is unproven **[real]**.

## 3. Feature-by-feature RAG comparison

Fair baseline (not weakened): same corpus, FTS top-k retrieval, exact
value extraction, one prompted review call with citation-ID
enforcement and abstention/conflict instructions, consent filtering
at retrieval. This is exactly what the repo's baseline implements
(§12.4 reran it with real reviews).

| Capability | Ordinary RAG (prompted) | Yaad | Verdict |
|---|---|---|---|
| Direct factual recall | Yes, from chunks | Same path + injection | Tie **[real]** (D1/X3 identical; D3 joint fail) |
| Cross-document questions | Yes, if retrieved | Same + audio expansion + injection | Tie-ish **[real]** (X1 0.5/0.5 both; truncation hurt both) |
| Conflicting facts | Flags when both in context | Deterministic pair verdicts + review | **RAG sufficient** **[real]** (C1/C2 both correct); Yaad's deterministic path misfired on real keys (G3) |
| Changed decisions | Reads update text | Supersession bookkeeping | **RAG sufficient on evidence** **[real]** (H1/H2 both correct via review; Yaad's link was never extracted) |
| History vs current | Can quote both, unlabeled | Labeled stale/superseded, excluded from premises | **Yaad structural edge, unproven behaviorally** **[code][test]**; no real-model separation observed |
| Proposal vs decision | Can follow prompt labels | `proposal_only` kinds + labels | **Yaad structural edge, leaky**: real extraction mislabeled the proposal; factual-path prose lacks the label (P1) **[real][mock]** |
| Unresolved questions | Can quote "undecided" text | Undecided-gated records + listings | Tie on answers (U1) **[real]**; listings Yaad-only **[mock]** |
| Abstention | Yes, prompted | Same review + gates | **Baseline better measured** (G1/G3: B 2/2, Y 0/2) **[real]** |
| Provenance | Citations to chunks | Citations + record lineage + history | **Yaad deeper** (record→evidence→file chains, pruned-row visibility) **[code][test]**; citation validity 100% both **[real]** |
| After consent revocation | Filter retrieval (easy) | Eligibility exclusion + preserved labeled history | **Yaad deeper** (history stays inspectable, never served) **[code][test]**; never exercised with real models **[unver]** |

Reported experiments, verified in `docs/VALIDATION_RESULTS.md`:
mocked 18/24 vs 12/24 (§7); real 7.5/10 vs 8.5/10 (§12.4); real
extraction defects and the G3 override (§12.4–12.6). The mocked
result measured architectural scaffolding under a lexical quoter
that could not do what real reviews do (notice conflicts, match
semantically, abstain); the real result supersedes it wherever
they disagree. Implication: the memory thesis has no behavioral
confirmation, and the honest reading of current evidence is
parity-to-negative, not advantage.

## 4. Anti-project category assessment

Guide content per the brief (guide text itself **[unver]** — not
independently retrieved). Ratings concern resemblance/overlap, not
final disqualification odds.

1. **Basic RAG applications: Material concern.** The core loop —
   index personal files, ask questions, receive cited answers — is
   RAG, and real-model behavior closely tracks a prompted baseline
   **[real]**. The defense rests entirely on "basic": a tested
   stateful memory lifecycle, multimodal ingestion, consent/egress
   infrastructure, and a local-model adapter are not tutorial-grade
   work **[code][test]**. A strict triage could still pattern-match
   "document Q&A with citations" → RAG. This is the only category
   requiring active mitigation (demo + framing).
2. **Simple image analyzers: Not applicable.** Image understanding
   exists (poster/scene/table/chart via Spark vision **[code]**,
   `worker.py:261`, capability receipts) but as one ingestion
   modality inside a memory system, not the product.
3. **AI-for-education chatbots: Not applicable.** No tutoring,
   curriculum, or quiz functionality exists.
4. **AI job application screeners: Not applicable.** No resume,
   candidate, or hiring functionality exists.
5. **AI nutrition coaches: Not applicable.** No food, diet, or
   health-tracking functionality exists.
6. **Personality analyzers: Not applicable.** No personality
   inference exists.
7. **Sports analyzers/coaches: Not applicable.** No sports
   functionality exists.
8. **Medical advice incl. mental-health/skin: Not applicable.** No
   medical, mental-health, or skin-analysis functionality exists;
   the corpus is event planning. No disallowed-content risk found.
9. **Political/war: Not applicable.** No such content or function.

**Law/ethics/platform/IP:** no concern found beyond routine
submission hygiene. Content is benign synthetic event planning;
Meta API used via documented Standard models with an allowlist
that rejects contributor IDs **[code]** (`config.py`,
`meta_config.py`); dependencies are standard OSS (fastapi,
uvicorn, pydantic, httpx, typer, pymupdf, pillow, python-docx)
**[code]** (`pyproject.toml`); `.env` is gitignored and untracked
(verified: `git check-ignore .env`); `docs/capability_usage.json`
contains call metadata only (spot-checked, no key-like values);
no production databases or private documents in the tree. Two
limits: `.env` contents deliberately never inspected, and the
audio file's ownership rests on the user's statement that it is
their recording **[unver]**.

## 5. Real-model evidence analysis

Run (§12.4): 1 real extraction + 20 real reviews (10Q × 2 systems),
muse-spark-1.3, $0.0716, 21/21 receipts `ok=1`, disposable DB.
Pre-registered expectations; single run.

- **Extraction (23 records):** no supersession link emitted (Room A
  stayed `decided`); fee key phrases diverged (`150+fee+registration`
  multi-amount vs `fee+registration`/INR200) so the true ₹150/₹200
  pair are not rivals while "Updated fee" false-pairs with "Spring
  fee" (`fee+registration`/INR100); food-truck labeled `unresolved`
  not `proposal` **[real]** (DB state inspected). Root cause in
  code: the prompt constrains key-phrase words to verbatim excerpts
  but says nothing about excluding values or naming the event
  **[code]** (`extract.py:77`, `RULES`), and `_matter_key` uses the
  raw signature **[code]** (`records.py:363-365`).
- **Answers:** Yaad 7.5/10, baseline 8.5/10. Memory-attributable
  correct answers: zero; memory-attributable failures: two (G1
  noise answer where baseline abstained; G3 false-conflict verdict
  where baseline abstained) **[real]**.
- **G3 mechanism (verified):** `plan_memory` pulls conflict
  partners on usability alone, without question-match
  **[code]** (`memory_answer.py:327-339`), and the conflict
  override fires on any matched pair for answered/partial statuses
  **[code]** (`answer.py:508-516`). Deterministic, reproducible,
  and wrong.
- **Bright spots:** review-layer conflict handling excellent both
  sides (C1/C2, incl. explicit Spring disambiguation); H1/H2
  correct; citations 59/59 valid; zero invented values (one
  €-for-₹ prose slip, baseline) **[real]**. These validate the RAG
  foundation, not the memory layer.
- **Calibration note:** where retrieval failed both (D3: N2
  unretrieved — stemming gap, audit M4), the baseline abstained
  and Yaad answered wrong **[real]**. Memory did not compensate
  for retrieval; it amplified noise.

## 6. Unresolved defects and their impact

| Defect | Correctness | Differentiation | Demo | Scope |
|---|---|---|---|---|
| P0 conflict-override gating (G3: false pair overrides abstention) | Yes — wrong verdicts | Yes — memory actively harms | Yes, if triggered | Core |
| P1 key quality (value-embedding, event-omitting keys → false + missed pairs) | Yes | Yes — breaks the flagship | Yes | Core |
| P1 proposal/unresolved mislabeling at extraction | Partial | Yes — listings miss | Maybe | Core |
| Missing supersession links from real extraction | Partial (review compensates) | Yes — bookkeeping unproven live | Maybe | Core |
| P1 factual-path proposal label gap (P1 finding) | No | Weakens | Minor | Product |
| Review-row truncation → false "no corroboration" (X1) | Yes (both systems) | No | Yes, if asked | Reliability |
| No stemming (D3, audit M4) | Yes (both systems) | No | Yes, if asked | Reliability |
| Answer prose unverified (citations valid, sentences not; € slip) | Yes | No | Minor | Reliability |
| Status-intent breadth (residual: "what are the proposals?" now falls through by design) | No | No | No | Accepted trade-off |

Also: the committed showcase traces do not demonstrate memory —
`ask-registration-fee.json` is `cloud_unavailable` with
`memory.used: false` (offline trace) — so judge-facing artifacts
currently prove honest fallback, not differentiation **[code]**.

## 7. Disqualification and judging risk

**A. Anti-project risk: Medium.** Substance favors Yaad (a real
stateful system is not "basic" by any careful reading)
**[code][test]**; exposure comes from strict/fast triage plus
real-model behavior parity **[real]**. Mitigable with the video
and README framing (below). Not a "likely DQ" situation; not a
"no risk" situation either.

**B. Baseline viability: Low on Meta-use/outputs; Medium overall
via original-work gap.** Meta-model use is substantive and
receipted: Spark `reason_json` (extraction + review), Voice
Transcribe, vision (images + scanned-PDF OCR), PDF understanding
**[code]** (`extract.py:165`, `worker.py:192,234,261,295`,
`answer.py:226-228`), capability suite with per-request
latency/tokens/cost, and a recorded 5-request rehearsal. No
viability risk on tooling. The viability risk is the new-work
identification requirement (see D).

**C. Competitiveness (per-brief weights).** Impact 5/10 (real
problem, no users); Meta AI 6/10 (broad, receipted use; local
unverified); Tech Depth 7/10 (H1/H2 fixed, G3/P1 found; net flat);
Demo 5/10 (no video; real slice parity-negative). Weighted ≈ 5.7 —
an engineering entry, not a prize contender on the memory-beats-RAG
story. A weak position is not disqualification.

**D. Original-work compliance: High risk, human-owned.** The repo
has zero commits, no remote, no timeline: `git log` =
"no commits yet"; everything untracked. Nothing in the tree
identifies what was built during the event window. File mtimes
(Oct 27–30) fall inside the stated window but prove little and
must not be oversold. The human must: publish the repo, add a
plain-language statement of what was built when (without
backdating or fabricating history), and show it in the video.
This, not the RAG question, is the most mechanical
disqualification path. Team size (≤4) and video existence are
**[unver]** from the repo.

**E. IP/data: Low.** See §4. Before publishing, the human must
still confirm: `.env` stays out (it is ignored — verify at push
time), the `.m4a` is publishable, and no private paths leak into
committed traces (traces embed scratch absolute paths by design,
documented in `EVALUATION_TRACES.md`).

## 8. Original-work and intellectual-property checklist

- [ ] Human: `git init` history decision — publish full tree as one
  honest initial commit; do NOT synthesize backdated history.
- [ ] Human: add `HACKATHON.md` (or README section): what was built
  during the event, in plain language, with dates the human can attest.
- [ ] Human: record the 3-minute video showing event-period
  functionality; keep the script factual (see §9).
- [ ] Human: verify `.env` absent from the pushed tree; confirm
  `.env.example` has no secrets (verified clean).
- [ ] Human: confirm the `.m4a` recording and `ui-reference.png`
  are publishable under the human's rights.
- [ ] Human: confirm team size ≤ 4; confirm no third-party assets
  beyond standard OSS deps and user-owned fixtures.
- [ ] Known: no secrets found in docs/traces/usage files
  (spot-checked); no `.db`/`.sqlite3` files in tree; `.env`
  mode 600 and gitignored.

## 9. Recommended submission strategy

**Strategy 1 — Submit as-is with a narrowed, honest claim.**
Claim: "evidence-backed personal file memory that tracks what it
knows — conflicts, changes, proposals, consent — with citations,"
NOT "memory answers better than RAG." Demo: index → conflict
question with both sides cited → consent revocation changing the
answer with history intact → clean abstention. Benefits: no
regression risk; every demo beat is deterministic or
review-verified; honest. Risks: differentiation rests on
bookkeeping, a modest story; G3-class questions must stay out of
the video. Minimum evidence: the video itself + existing tests.
**Recommended.**

**Strategy 2 — Targeted corrections, then submit.** Fix P0
override gating (+ regression test), harden key quality minimally,
re-run the 10Q real eval (~$0.07). Benefits: could remove the
"memory harms" headline; might recover parity-plus. Risks: 6 days
to deadline; fixes may not flip the story (review is just good);
re-validation spend and regression risk; the P1 key fix spans
prompt + normalization and is easy to get subtly wrong. Minimum
evidence: re-run showing no memory-caused failures. Adopt only if
the human accepts that the likely outcome is still "parity with
cleaner machinery."

**Strategy 3 — Reposition around consent/provenance/change
tracking.** Make memory-as-bookkeeping the product: "the archive
that remembers what changed, what's contested, and what it's
allowed to use." Demo consent revocation + stale/superseded
history + conflict ledgers as the core loop. Benefits: the
strongest true story; deterministic and tested; clearly not
"basic RAG." Risks: reframing docs/video under time pressure;
still unvalidated with users. This is Strategy 1's framing done
deliberately — combine them.

**Do not:** rewrite retrieval (embeddings), add features, run
large evals, or claim local-model or reliability properties.

## 10. Final decision checklist

1. *Technically RAG?* **Yes** — retrieval + model generation is the
   dominant answering path **[code]**. "RAG application" is true;
   only "basic" is disputable, and that dispute is winnable.
2. *Substantive, demonstrable distinction from basic RAG?*
   **Architecturally yes; behaviorally unproven** (real slice:
   negative) **[code][test][real]**.
3. *Justified to call it a differentiated personal-memory engine?*
   **No, not on current evidence.**
4. *Reasonably defensible under the anti-project guidance?*
   **Yes** — "basic" does not plausibly cover a tested stateful
   system; strict-triage risk remains (Medium).
5. *Realistic DQ risk as basic RAG?* **Medium**: unlikely on
   substance (~1-in-5 judgment, triage-driven), mitigable with
   video + framing. The bigger DQ risk is the missing new-work
   identification (High until the human fixes it).
6. *Single most important video moment:* **consent revocation
   changing a live answer while history stays inspectable** —
   deterministic, memory-only, honest, and clearly not basic RAG.
   Second: a both-sides conflict answer with citations.
7. *Fix, narrow, reposition, or stop?* **Stop coding; narrow the
   claims; adopt the bookkeeping framing.** No functionality fixes
   before submission.
8. *What would I do?* Strategy 1+3: freeze code today; spend every
   remaining hour on the video (revocation anchor), the public
   repo + HACKATHON.md statement, and README honesty pass
   (stale "231 tests" counts in README/`eval/report.md` should
   read 257). Submit the narrower story with a clear conscience.

**Overall classification: Differentiated architecture, but evidence
of user-facing advantage is insufficient.**

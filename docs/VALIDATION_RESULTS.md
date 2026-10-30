# Yaad Validation Results — H1/H2 fixes and RAG comparison

Date: 2026-10-29. Scope: reproduce and fix audit findings H1–H2, then run
the §5 RAG-baseline comparison. All work used disposable databases and
mocked models. No production data, corpus, `.env`, or private docs were
modified. Model spend for §§1–11: $0. No real-model inference was run.

Follow-up (§12, same day): status-hijack fix, cross-document root-cause
analysis, and a small spend-capped real-model evaluation ($0.0716,
21 calls, muse-spark-1.3). Disposable DBs only; `.env` read for the
key, never modified or reproduced.

## 1. Executive summary

- **H1 (blank-detail crash, no receipt): fixed.** `validate_candidate`
  rejects empty `detail`; persistence failures still record the egress
  receipt. 3 new regression tests; full suite green.
- **H2 (local memories never served): fixed.** Serving authorization is
  now provider-aware: local serving uses resolving supports without
  requiring cloud consent; cloud serving still requires live consent.
  7 new regression tests; full suite green.
- **RAG comparison (mocked, 24 questions): Yaad 18/24 vs baseline
  12/24 overall task success.** Yaad demonstrated a **narrow,
  condition-dependent advantage**: decisive on explicit conflict
  verdicts (4/4 vs 0 explicit flags) and status listings; tied on
  direct recall (5/6), cross-file (effectively tied, see X2/X4 bug),
  current-vs-history (3/3), and abstention (0/3 both — quoter
  limitation, inconclusive). Several Yaad wins are partly an artifact
  of the deliberately lexical mock reviewer, so the real-model gap is
  likely smaller. What persisted as structural (would survive a real
  model): conflict verdicts with both-sides provenance, status
  listings, supersession bookkeeping.
- **New defect found (not fixed, out of scope):** any factual question
  mentioning "the proposal" plus what/which/list is hijacked into a
  proposal-memory listing (X2, X4). High severity. See §8.
- **Verdict on the differentiator: partially demonstrated, not
  proven.** The lifecycle machinery works end to end given competent
  extraction, but extraction quality, paraphrase robustness, and
  real-model behavior remain unmeasured.

## 2. H1: blank detail → IntegrityError, no receipt

**Reproduction (pre-fix).** `validate_candidate` in
`yaad/memory/records.py` returned `[]` for `detail: ""`, while the
schema declares `detail TEXT NOT NULL CHECK(length(trim(detail))>0)`.
`run_extraction` therefore passed the candidate to `upsert_records`,
which raised `IntegrityError`, rolling back the whole transaction —
including valid candidates — and the completed model call had no
usage receipt (the receipt insert sits inside the same transaction).

**Fix (2 files).**

- `yaad/memory/records.py` — `validate_candidate` now appends
  `"empty detail: statement text is required"` for blank detail, so
  the candidate is rejected with a clear reason before persistence.
- `yaad/memory/extract.py` — the `upsert_records` block is wrapped so
  a persistence failure calls `_record_extraction_attempt(...,
  success=0)` before re-raising: the billed call stays accounted for
  without claiming success.

Per-candidate validation already isolates rejects, so valid
candidates persist alongside rejected ones.

**Regression tests** (`tests/test_memory_extract.py`, 11 passed):

- `test_blank_detail_rejected_with_clear_reason_before_persistence`
- `test_mixed_batch_persists_valid_alongside_rejected`
- `test_persistence_failure_still_records_receipt` (monkeypatched
  `upsert_records` boom; asserts receipt row with `success=0` and
  re-raise)

## 3. H2: local extraction served nothing

**Reproduction (pre-fix).** With `model_provider=local` and cloud
consent off, extraction succeeded but every question fell back to
units-only: `authorized_supports()` (`yaad/query/memory_answer.py`)
and `_listing_displayable()` unconditionally required live file+folder
`cloud_consent`, so `plan_memory` found zero usable supports
(`memory.used: False`).

**Authorization policy adopted.** Cloud consent is permission to send
data to Meta. It gates cloud egress (extraction/review calls, cloud
serving whose premises travel to the model). Local answering is local
computation with no egress, so it authorizes resolving supports
without cloud consent. Local records never become cloud-eligible: on
provider switch the cloud path re-checks live consent, and cloud
extraction still refuses without consent. Consent revocation
immediately excludes cloud serving; history stays inspectable.

**Fix (3 files, cloud defaults unchanged).**

- `yaad/query/memory_answer.py` — `authorized_supports(...,
  require_consent=True)`, `_listing_displayable(...,
  require_consent=True)`, `plan_memory(..., local=False)`,
  `_status_plan(..., local=False)`; consent gates skipped only when
  the caller passes the local flag.
- `yaad/query/answer.py` — passes `local=is_local(settings)` into
  `plan_memory`.
- `yaad/api/main.py` — `list_memory` verdicts pass
  `require_consent=not is_local(settings)`. (The "current consent
  does not authorize" overlay can only fire on the cloud path.)

**Regression tests** (`tests/test_local_inference.py`, 7 new, all
pass; `MetaClient` bombed to prove zero cloud construction):

- local serving without consent; cloud stays blocked without consent;
  revocation blocks cloud but not local; provider switch honors live
  consent; status listing local without consent; list verdicts local;
  `authorized_supports` honors the flag.

## 4. RAG baseline methodology

Deterministic mocked diagnostic on a disposable DB. Corpus: 26 units
— real Autumn Fest notes TXT + proposal PDF text (verbatim), MOCK
audio transcript + poster description (faithful to canonical facts,
clearly labelled mock), 1 genuine conflict pair (₨150 vs ₨200 fee),
1 explicit supersession pair (briefing Room A→B), 1 unconfirmed
proposal, 4 Spring Fest 2025 distractor units. Consent on for both
systems; identical evidence pool.

- **System A (Yaad):** real `run_extraction` (19 candidates via
  scripted mock, all validated through the real pipeline: 1 conflict
  pair, 1 supersession, 0 rejected) + real `answer_question`.
- **System B (baseline):** identical pipeline with `plan_memory`
  forced to an empty plan — ordinary retrieval + exact extraction +
  review, no memory state. Same DB, same questions.
- **Shared mock reviewer:** lexical quoter — quotes each
  non-historical source sharing ≥2 content words with the question,
  else abstains. It cannot do semantic matching, conflict detection,
  or semantic abstention **by design** (adding conflict logic to the
  baseline would be cheating in the other direction). Consequence:
  measured gaps are an **upper bound**; a real model in the baseline
  loop would close part of them. No labels or expected answers were
  visible to either system. Harness: `/tmp/ragcmp/` (scratch, not
  committed); scripted extraction + mock audio/poster are the
  experiment's explicit seams.

Grading: pre-registered expectations (§5) scored 1 / 0.5 / 0 per
question per system, plus automatic citation-validity (ID resolves +
quote contained) and contamination checks.

Two pre-registered corrections, both disclosed: (a) corpus v1
contradicted itself on the meeting venue, so the Room A→B pair was
moved to a volunteer-briefing thread and H1–H3 reworded before
grading; (b) X2/X4 as worded trigger a real status-intent bug, so
supplementary reworded variants X2b/X4b were added while the
originals were still graded as run.

## 5. The 24-question labelled set

| ID | Category | Question | Expected |
|----|----------|----------|----------|
| D1 | direct | When is Autumn Fest 2026? | 2026-11-18, answered |
| D2 | direct | What is the initial budget for Autumn Fest? | ₹25,000, answered |
| D3 | direct | How many volunteers are needed for Autumn Fest? | 6, not Spring 12 |
| D4 | direct | When does Autumn Fest registration close? | 2026-11-15, answered |
| D5 | direct | What is the minimum number of helpers required for stage, registration and crowd duty? | 6 (paraphrase) |
| D6 | direct | What is the last day to sign up for Autumn Fest? | 2026-11-15 (paraphrase) |
| X1 | cross | Which details discussed in the audio are corroborated by the other files? | multi-file answer |
| X2 | cross | What registration fee do the proposal and the recording agree on? | ₹150 from both |
| X3 | cross | When is the next planning meeting, and where? | Nov 6, seminar hall |
| X4 | cross | What is the poster deadline according to the notes and the proposal? | Nov 12 from both |
| C1 | conflict | What is the Autumn Fest registration fee? | conflict: 150 vs 200, never settled, no Spring 100 |
| C2 | conflict | Do the sources agree on the registration fee? | no, both cited |
| C3 | conflict | Which registration fee should I charge attendees? | must not settle |
| C4 | conflict | Are there any conflicting memories? | lists fee pair |
| H1 | history | Where will the volunteer briefing be held? | Room B current |
| H2 | history | Where was the volunteer briefing originally going to be held? | Room A historical |
| H3 | history | Is Room A still the current briefing venue? | No, Room B |
| P1 | proposal | Is the food-truck zone confirmed for Autumn Fest? | No, proposal only |
| P2 | proposal | Is the poster deadline a confirmed decision or just a proposal? | confirmed, Nov 12 |
| U1 | unresolved | Which workshop topics have been decided? | none; explicitly undecided = answered |
| U2 | unresolved | What remains undecided about Autumn Fest? | workshop topics |
| G1 | abstention | What is the catering budget for Autumn Fest? | abstain |
| G2 | abstention | Who is the chief guest at Autumn Fest? | abstain |
| G3 | abstention | What was the publicity budget for Spring Fest? | abstain (near-miss) |

## 6. Per-question results (Y = Yaad, B = baseline)

1 = pass, 0.5 = partial, 0 = fail. † = Yaad win partly quoter-assisted
(real model would likely narrow it). ★ = real defect, would persist
with a real model.

| ID | Y status | Y | B status | B | Note |
|----|----------|---|----------|---|------|
| D1 | answered | 1 | answered | 1 | value via poster+audio; P2 unretrieved both |
| D2 | answered | 1 | answered | 1 | identical quotes |
| D3 | answered | 0 | answered | 0 | quoter cites only Spring 12; N2 overlap 1, missed both |
| D4 | answered | 1 | answered | 1 | value via poster; Spring-100 noise in B |
| D5 | answered | 1 | answered | 1 | exact N2 both |
| D6 | answered | 1 | answered | 1 | value via poster amid noise both |
| X1 | unverified | 0 | unverified | 0 | abstract wording matches nothing lexically, both |
| X2 | answered★ | 0 | answered | 0.5 | Y hijacked to proposal listing; B correct value, single source |
| X3 | answered | 1 | answered | 1 | identical quotes |
| X4 | answered★ | 0 | unverified | 0 | Y hijacked; B quoter threshold miss (rows retrieved) |
| C1 | conflict | 1 | answered | 0.5 | Y both-sides verdict, no Spring; B dumps 150+200+100, unflagged |
| C2 | conflict | 1 | answered | 0.5 | same pattern |
| C3 | conflict | 1 | answered | 0.5 | same pattern; neither settles, only Y flags |
| C4 | answered | 1 | not_found | 0 | Y lists pair; B has no status machinery |
| H1 | answered | 1 | answered | 1 | both quote R2 (Room B) |
| H2 | answered | 1 | answered | 1 | both via update text (R1 uncited); weak pass both |
| H3 | answered | 1 | answered | 1 | R2 quote implies No both |
| P1 | answered | 1 | answered | 1 | S1 first both; Y matched proposal record but prose lacks [Proposed] label |
| P2 | answered† | 1 | unverified | 0 | Y memory-synthesized; B quoter miss (real model would answer) |
| U1 | answered | 1 | answered | 1 | explicit-undecided answered both |
| U2 | answered† | 1 | answered | 0 | Y status listing; B irrelevant quotes (A4 threshold miss) |
| G1 | answered | 0 | answered | 0 | quoter eagerness both; inconclusive re real models |
| G2 | answered | 0 | answered | 0 | same |
| G3 | answered | 0 | answered | 0 | same; Y leads with Autumn budget for Spring Q |
| X2b | conflict (supp) | 1 | answered (supp) | 0.5 | reworded X2: Y conflict+P2+A2; B single-source settle |
| X4b | answered (supp) | 1 | unverified (supp) | 0 | reworded X4: Y synthesized N5+P4; B quoter miss |

## 7. Metric tables (raw counts)

Overall task success: **Yaad 18/24, baseline 12/24**
(partials: Yaad 0, baseline 4 × 0.5 included above).

| Category (n) | Yaad | Baseline |
|---|---|---|
| Direct recall (6) | 5/6 | 5/6 |
| Cross-document (4) | 1/4 | 1.5/4 |
| Conflict (4) | 4/4 | 1.5/4 (partials, zero explicit flags) |
| Current-vs-history (3) | 3/3 | 3/3 |
| Proposal-vs-decision (2) | 2/2 | 1/2 |
| Unresolved (2) | 2/2 | 1/2 |
| Abstention (3) | 0/3 | 0/3 (inconclusive: quoter cannot abstain semantically) |

| Metric | Yaad | Baseline |
|---|---|---|
| Answer correctness (key value, §6) | 18/24 | 12/24 |
| Citation validity (auto: ID resolves + quote contained) | 81/81 | 75/75 |
| Key-value evidence support | 18/24 | 12/24 (fails coincide with correctness fails) |
| Explicit conflict detection (C1–C4) | 4/4 | 0/4 (both values surfaced unflagged 3/3 on C1–C3) |
| Current-vs-history accuracy (H1–H3) | 3/3 | 3/3 |
| Proposal-vs-decision (P1–P2) | 2/2 | 1/2 |
| Appropriate abstention (G1–G3) | 0/3 | 0/3 (see limitation) |
| Wrong-matter (Spring) citations in Autumn answers | 2 (D3, D4) | 5 (D3, D4, C1, C2, C3) |
| Invented values (dates/amounts/assignees) | 0 | 0 |
| Memory used (Yaad only) | 23/24 (all but X1) | n/a |

## 8. Failure analysis and examples

- **Status-intent hijack (NEW, high, unfixed).** `detect_status_intent`
  fires on "proposal" + "what/which/list/…" — so any factual question
  mentioning "the proposal" (i.e. `event_proposal.pdf`) is diverted
  into a proposal-memory listing. X2/X4 returned a food-truck listing
  for fee/deadline questions. The docstring claims narrowness; the
  scope-word list is too broad. Repro: ask X2/X4 on the harness
  corpus. Needs a precision fix (e.g. require explicit memory-scope
  words), out of scope here.
- **D3 both fail:** BM25 + quoter surface only the Spring distractor
  (overlap 2 vs N2's 1). Retrieval-precision signal; a real model
  would likely answer 6 for both.
- **X1 both fail:** abstract wording ("corroborated", "details")
  matches no unit text; memory matched nothing either. Real gap for
  both architectures under lexical matching.
- **Abstention inconclusive:** the quoter quotes any ≥2-overlap
  source, so G1–G3 fail identically. Only semantic review (a real
  model) can test abstention; do not cite 0/3 as a model result.
- **P1 presentation gap (new, medium):** the proposal record matched
  (`proposal_only: true` in the contract) but the factual answer
  prose carries no [Proposed] label — labelling exists only on the
  status path.
- **Memory noise (low):** memory claims sometimes include irrelevant
  records (e.g. event-date claim on P1/H1) though final answers
  stayed correct; matching precision is unmeasured beyond n=24.
- **H1–H3 tie:** the update text alone ("moved from Room A to Room
  B") sufficed for the quoter, so supersession bookkeeping showed no
  answer-level advantage here — though record statuses were exactly
  right (Room A superseded, Room B decided) and Yaad's conflict path
  correctly excluded the distractor the baseline cited.

## 9. Limitations and unverified behavior

- Mocked everything: scripted extraction, lexical reviewer, mock
  audio/poster units. Real-model extraction quality, paraphrase
  robustness, abstention, and conflict-noticing are **unmeasured**.
- n=24, single domain, author-shaped fixtures; no statistical
  significance claimed; no dataset split (diagnostic, not benchmark).
- Baseline is Yaad-minus-memory with a shared quoter — a fair
  structural control, not a production RAG system.
- Local weights never run; open-weight bonus eligibility unverified;
  real local-model inference unverified (mocked adapter tests only).
- M1–M7, torn reads, folder delete, ask timeouts, and the §8 status
  hijack remain open.

## 10. Final conclusion: is the differentiator demonstrated?

**Narrowly, and only in part.** Under mocked conditions Yaad beat the
baseline 18/24 to 12/24, with the entire gap coming from the memory
layer: explicit conflict verdicts with both-sides provenance (4/4 vs
0 flags), conflict/unresolved status listings (C4, U2), and one
memory-synthesized factoid (P2, quoter-assisted). Direct recall,
history questions, and abstention showed no Yaad advantage — ties or
joint failures. Two Yaad cross-file failures are a real precision
bug, not measurement noise.

The honest reading: the lifecycle machinery (identity, rivals,
supersession, eligibility, status paths) works end to end and does
things ordinary retrieval cannot express — but on single-fact lookup
Yaad currently rides the same retrieval+review path as the baseline,
and the experiment cannot speak to real-model quality. The central
claim is **plausible and partly demonstrated, not proven**.

## 11. Recommended next steps

1. **P0 — real-model mini-run (C1–C4, U2, P2, G1–G3 + X2b/X4b):**
   same corpus, real review calls, human grading. Decides how much of
   the 6-point gap survives. Negligible spend.
2. **P0 — fix status-intent precision** (X2/X4 repro): require
   explicit memory-scope wording before diverting factual questions;
   add the hijack regression test.
3. **P1 — measure extraction quality:** real-model extraction on the
   Autumn Fest DB copy, spot-check 30 records for precision,
   paraphrase-key misses, and proposal/explicit mislabels.
4. **P1 — surface `proposal_only` on the factual path** (P1 gap) and
   add a no-single-word-overlap-safe matching-precision battery.
5. **P2 — paraphrase battery** (20+ rewordings incl. D6-style) to
   quantify the matching fragility both systems showed.

---

## Appendix — verification commands (all green unless noted)

- `.venv/bin/python -m pytest tests/ -q` → **255 passed**
- `.venv/bin/python -m pytest tests/test_memory_extract.py
  tests/test_local_inference.py -q` → **31 passed**
- `.venv/bin/python eval/run_eval.py` → 3/3 retrieval, 1/1 abstention
- `node --check yaad/ui/app.js` → OK (no UI files changed)
- Comparison harness: `.venv/bin/python /tmp/ragcmp/harness.py`
  (deterministic; rerun reproduces `results_*.jsonl` byte-identical
  except the temp-DB path line)

---

## 12. Follow-up: hijack fix, cross-document analysis, real-model eval

### 12.1 Status-intent hijack — fixed

**Root cause.** `detect_status_intent` fired on any status-term
substring plus any scope word (`what/which/list/…`). In X2/X4 the
word "proposal" refers to `event_proposal.pdf`, not to memory
status, but `proposal + what` diverted both factual questions into a
proposal-memory listing.

**Fix** (`yaad/query/memory_answer.py`, no scope-word changes): a
status-term occurrence immediately preceded by a determiner
(the/a/an/this/that) is treated as a document/entity reference and
does not count; an explicit memory scope (whole words
memory/memories/record/records) overrides the exclusion. Word-aware
matching matters: a first version used substring `"record"`, which
matched "recording" and left X2 hijacked — caught by re-running the
mocked comparison, then corrected.

**Regression tests** (`tests/test_memory_status.py`, 2 new):

- `test_document_reference_does_not_trigger_status_listing` — exact
  X2/X4 wordings return no intent; genuine phrasings still fire; E2E
  non-audio variants reach the listing path and do not list. (The
  E2E wording deliberately avoids audio trigger words: on stores
  without audio files the audio pre-check short-circuits before the
  intent path, which made the first draft of this test pass
  vacuously.)
- `test_explicit_memory_scope_overrides_document_reference` —
  "list the stale records" still lists.

**Verification.** Mocked rerun: X2 → conflict verdict (fee pair,
P2+A2 cited), X4 → synthesized poster deadline (N5+P4). All prior
status tests green. Full suite: **257 passed**.

### 12.2 Cross-document failures — root causes

- **X2/X4 (Yaad):** the hijack above. Fixed and verified (§12.1).
- **X1 (both, mocked):** stage trace proved retrieval healthy —
  audio fallback fetched A1–A4 and transcript expansion retrieved
  11 multi-file rows (15 total: P2, U1, IMG1, N4, P4, N2, N3, R1
  plus noise). The failure was purely reviewer-side: no abstract
  question word appears in any unit text, so the lexical quoter
  abstained, and memory matched nothing lexically. No code change;
  the real-model run (§12.4) is the honest verification.
- No architectural change was needed for any cross-document path.

### 12.3 Abstention evaluation — fixed by real-model measurement

The lexical quoter structurally cannot abstain semantically (any
≥2-overlap source gets quoted), so mocked G1–G3 (0/3 both) measured
nothing about abstention. The §12.4 run replaces the quoter with
real review calls, making abstention genuinely measurable. (The
mocked harness limitation stands for §§4–7 and is now calibrated:
we know exactly how the quoter errs.)

### 12.4 Real-model E2E evaluation (10 questions × 2 systems)

**Method.** Same 26-unit corpus, fresh disposable DB, consent on.
One real extraction (muse-spark-1.3) then real reviews for
D1, D3, X1, X3, C1, C2, H1, H2, G1, G3 — two per requested
category (direct, cross-document, conflict, superseded-history,
unanswerable). Baseline = identical pipeline with `plan_memory`
forced empty. Expectations pre-registered in §5. Spend cap $0.50
(hard abort); single run, no reliability claims. Harness:
`/tmp/ragcmp/real_eval.py` (scratch).

**Real extraction yield: 23 records, 0 known-invalid accepted —
but three quality defects.** (a) No `supersedes` link emitted:
Room A stayed `decided` instead of `superseded`. (b) Fee keys
diverged: "Original registration fee" got signature
`150+fee+registration` with multi-amount excerpts, while "Updated
registration fee" got `fee+registration`/INR200 — so the TRUE
₹150/₹200 pair are not rivals, and "Updated fee" instead pairs
with "Spring fee" (`fee+registration`/INR100): a **false
cross-matter conflict** plus a missed true conflict — the
paraphrase-dependent-key failure the audit predicted.
(c) The food-truck suggestion was labeled `unresolved` rather than
`proposal`, so it misses proposal listings.

**Per-question results (Y = Yaad, B = baseline).**

| ID | Y | B | Note |
|----|---|---|------|
| D1 | 1 | 1 | identical correct date |
| D3 | 0 | 0 | N2 never retrieved (M4 stemming: needed≠need); Y answered wrong, B honestly abstained |
| X1 | 0.5 | 0.5 | strong multi-file corroboration both; 3 false "no corroboration" claims both (review rows[:8] truncation cut N1/N4/N6/P4/P5); B also wrote "€150" |
| X3 | 1 | 1 | identical |
| C1 | 1 | 1 | both flag 150-vs-200 with correct sides; real review closed the mocked gap |
| C2 | 1 | 1 | both answer No and explicitly exclude Spring ₹100 |
| H1 | 1 | 1 | Room B both (via review; no supersession link existed) |
| H2 | 1 | 1 | Room A both, R1+R2 cited |
| G1 | 0 | 1 | Y answered from irrelevant noise; B abstained honestly |
| G3 | 0 | 1 | Y surfaced the FALSE #4↔#20 pair as a conflict verdict; B abstained honestly |

**Metrics (raw).** Overall: **Yaad 7.5/10, baseline 8.5/10.**
Citation validity (auto): 35/35 Yaad, 24/24 baseline. Invented
values: 0 both (one €-for-₹ prose slip, baseline X1). Memory used:
9/10 Yaad (all but X1).

**Model usage and receipts.** 21 calls, all `ok=1`: 1 extraction
(2029 in / 4195 out, $0.0204), 20 reviews (~750–1050 in /
~170–950 out each). **Total $0.0716**, reconciled against
`egress_log` (21 receipts, provider `Meta Model API`).

**Failure analysis.**

- **G3 (NEW P0, unfixed): conflict-override hijack.** A matched
  record with a conflict partner makes `synthesize` emit a
  conflict verdict without checking the pair's relevance to the
  question — so an abstain-question got the false Autumn-200 vs
  Spring-100 pair as its answer. Chain: model-authored keys omit
  the event → `_rivals` pairs cross-matter records → override
  fires unconditionally. Proposed minimal fix (not implemented):
  require both partners (or the pair's matter) to match the
  question before a conflict verdict overrides review.
- **D3 (both):** retrieval gap, not review: `needed`≠`need`
  without stemming (audit M4) plus single-term BM25 rank left N2
  out of the review set. Baseline's abstention was the calibrated
  response; Yaad's wrong answer was not.
- **X1 (both):** review context budget (`rows[:8]`, audio-first)
  cut the corroborating notes/proposal rows; both models then
  asserted "no corroboration" falsely. Retrieval retrieved;
  budgeting dropped.
- **G1 (Yaad):** review eagerness on noise rows; single-run
  sample, but the baseline's identical-position abstention shows
  the prompt can abstain.

### 12.5 Updated verdict

The mocked 18–12 gap **did not survive real models**: on this
10-question slice the baseline won 8.5–7.5, because real review
detects conflicts unaided (C1/C2), abstains honestly (G1/G3),
and reads updates correctly (H1/H2) — while Yaad's memory layer
added no correct answer and actively harmed two (G1 noise answer,
G3 false-conflict verdict). The §10 prediction that conflict
verdicts "would survive a real model" was wrong for conflict
*detection*; what remains structurally unique but **still
untested with real models** is persistent conflict *bookkeeping*
(statuses, C4-style listings). The differentiator is still
unproven, and the experiment surfaced two real defect chains
(false-pair formation, conflict-override gating) that must be
fixed before memory can help rather than hurt.

**Limitations.** Single run, n=10, one domain; model
non-determinism unmeasured; status listings (C4/U2/P2) not
re-tested with real models; real-extraction variance across runs
unmeasured; no statistical claims.

### 12.6 New priority findings (unfixed)

1. **P0 — conflict-override gating (G3 repro).** Require pair
   relevance before a conflict verdict overrides review.
2. **P1 — extraction key quality.** Key phrases embed values
   ("150") and omit the event; consider stripping
   amounts/dates from the matter signature or prompting
   event-qualified keys. Missed true pair + false pair in one run.
3. **P1 — `proposal` vs `unresolved` labeling** at extraction
   (food-truck case); proposal listings miss it.
4. **P2 — review-row budget/ordering** (X1 truncation) and **M4
   stemming** (D3). Both pre-existing, now measured.

### 12.7 Verification commands (follow-up)

- `.venv/bin/python -m pytest tests/ -q` → **257 passed**
- `node --check yaad/ui/app.js` → OK (no UI files changed)
- `.venv/bin/python /tmp/ragcmp/harness.py` → X2 conflict,
  X4 synthesized (mocked, deterministic)
- `.venv/bin/python /tmp/ragcmp/real_eval.py` → 21 calls,
  $0.0716, 21/21 receipts ok (spend-capped, disposable DB)

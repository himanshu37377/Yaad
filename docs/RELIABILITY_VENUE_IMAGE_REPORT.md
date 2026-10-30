# Reliability Report: Venue Supersession + Realistic Image Validation

Date: 2026-10-30. V1/V2/V3 artifacts untouched. Live spend this
task: venue repeats $0.0754 (5 runs) + image evaluations $0.0336 and
$0.0325 + one diagnostic indexing pass (≈$0.02, temp DB) ≈ **$0.16**
against the authorized $1.50 ceiling, reconciled from egress receipts
where preserved.

## 1. Venue supersession: root cause

Candidate generation, not validation and not the fixpoint sweep.
Across all five preserved V3 chains extractions, no correct venue
first-link (Grand Arena→Riverside, or Crystal→Riverside) was ever
rejected by validation; the rejected entries are spurious cross-type
links from records sharing the initial PDF unit, correctly refused
("records concern unrelated matters"). The model links the final
venue to the middle venue but usually emits no link reaching the
original. Deadline chains link reliably because every source is
textual and dates order the chain; the venue middle source is an
image whose unit text is a vision description, and venue records
carry no dates to order them.

Deterministic parts behave correctly: with adjacent links present,
the fixpoint/transitive sweep closes three-step chains in production
order (new tests), proposals never supersede, competing confirmed
venues stay conflicted, and ambiguous identity retains the conflict.

Live re-measurement with the improved prompt ("link the final record
to EVERY earlier statement it replaces"), 5 fresh runs on the frozen
V3 chains fixture: Riverside superseded in **1/5** runs
(superseded sets: [6,3], [], [6,5,1,3], [], [6]). Two runs emitted no
supersessions at all. Rate across all measurement: V3 0/5, previous
cycle 1/3, now 1/5 — model emission variance, not a deterministic
defect. No deterministic ordering signal exists for undated venue
records (both sides say "confirmed"; no dates), so no safe rule can
infer the first replacement. Correct abstention is preserved: when
the link is missing, the records remain current and rivalrous rather
than silently resolved.

**Change made:** none to validation or candidacy in this task. The
prompt change from the previous cycle stands. New deterministic
regressions in `tests/test_venue_supersession.py` (5 tests: clear
replacement, proposal venue, competing venues, multi-step chain,
ambiguous different-events) all pass. A fixture-shaping finding from
that work: venue records with identical key phrases and no date/
amount share a `record_key` and merge at upsert; live models vary
key phrases, which is why live runs keep three records.

## 2. Realistic image evaluation

Corpus: `eval/image_eval_v2/` (8 images, simulated realistic
conditions via PIL transforms — provenance: fully synthetic, NOT
natural photographs; the clean `eval/image_eval/` benchmark is
unchanged). Ground truth was written before any run. One fixture
defect was found and fixed before scoring: the first document-photo
transform destroyed its own text (the model correctly reported a
blank image); the generator now uses rotation + shadow falloff.

Results by category (automated; the natural-scene count "failure"
is a digit-vs-word scorer artifact — the answer "three" is correct,
so manual reading is 10/12):

- chart: 2/2 (values and comparison correct)
- document_photo: 2/2 (patient, total under rotation/shadow/blur)
- table_photo: 2/2 (row/column relationships correct)
- multi_region: 1/1, natural_scene sign: pass
- illegible: correct honest `not_found` (no hallucinated code)
- handwriting: **0/2 — retrieval failure, not vision.** Both images
  were transcribed ("Buy tickets for Saturday"; degraded: "Buy
  tickets for Sat"), but the question term "bought" matches nothing
  in the units. Verb-form inflection is outside the deterministic
  folding (plural-only by design); no speculative stemming added.

## 3. Limitations and untested capabilities

Natural photographs, real handwriting, and real scanned paper remain
unmeasured — the corpus is drawn/simulated. Chart interpretation is
proven only on a simple labeled bar chart. Venue first-link emission
remains unreliable (1/5) with correct conservative fallback. The
handwriting retrieval gap needs either query-side inflection handling
(a design decision, not a heuristic patch) or memory-channel rescue.

## 4. Regression results

Full suite: **396 passed, 11 skipped, 0 failed** (includes the 5 new
venue tests). Deterministic evaluation: **4/4**. No production code
was changed in this task; no validation rule was weakened.

## 5. Readiness decision

Ready to proceed to UI redesign. The remaining issues are measured,
bounded, and fail conservatively (conflicts retained, honest
abstention, no invented supersession). Blocking the redesign on
venue link-emission variance would trade a model-behavior lottery
against product progress; it should be tracked as an extraction-
reliability metric in future evaluations instead.

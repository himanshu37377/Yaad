# Claim Relevance Design — Gating Memory Retrieval Before Answer Synthesis

Date: 2026-10-30. Status: design only; no source or test changes made.
Implementation update (2026-10-31): Phase 1 was implemented as designed
(tiered relevance in `yaad/query/memory_answer.py`, R1–R6/R8 regression
tests). Phases 2 and 3 were not implemented. The body below is the
original design record.
Method: code inspection plus disposable read-only probes (`/tmp`, no
production writes, no model spend). All quoted behaviors were measured,
not inferred.

## 1. Background and scope

Evidence-Verified Relationships fixed the G3 false-*conflict* override:
unvalidated rival pairs are no longer marked, and only validated,
question-matching pairs can force a conflict verdict. Genuine conflict
detection is preserved (20/20 relationship tests green).

The reproduction still shows a second, distinct weakness at the *claim*
level: an irrelevant memory record matches the question on two generic
words (`MATCH_WORDS = 2`) and is then synthesized into the answer. This
document investigates that weakness and recommends the smallest reliable
fix. It does not propose a retrieval rewrite, new infrastructure, or
another model call.

Key distinction carried through this doc:

1. **Lexical similarity** — shared words between question and record
   text. This is all `MATCH_WORDS` measures.
2. **Entity/event identity** — whether two records concern the same
   subject. This is what `yaad/memory/relationships.py` validates.
   Necessary for *relationships*, but **insufficient for relevance**:
   the G3 claim shares the question's event yet answers nothing asked.
3. **Question-answer relevance** — whether the record's *claim* answers
   the question's *ask*. Currently unmeasured anywhere.
4. **Supporting context vs directly answerable claims** — injected
   review context (low risk) vs verbatim synthesized answers (high
   risk). Currently one gate feeds both.

## 2. Current behavior

### 2.1 Matching (`yaad/query/memory_answer.py`)

- `_matches_terms` (:88-92): a record matches when
  `len(question_terms ∩ record_terms) >= MATCH_WORDS (2)`, where terms
  are lowercased content words (no stemming, stopwords removed) over
  `title + detail` **only**. Excerpts, `key_phrase`, `record_type`,
  dates, and amounts play no role in matching.
- `plan_memory` (:333-346): eligible records (live status, non-inferred
  kind, authorized supports) that match split into `matched` /
  `conflicted`. Unresolved records additionally pass via the
  undecided-question gate without any wording match.
- Claims carry `shared` (match count) and `overlap` (any claim evidence
  retrieved by FTS); both are **sort keys only** (:390-391), never
  thresholds. A floor-level match (`shared == 2`, `overlap == False`)
  synthesizes exactly like a strong one.

### 2.2 How matched claims influence the answer (`yaad/query/answer.py`)

| # | Channel | Site | Effect |
|---|---------|------|--------|
| 1 | Review-context injection | :421-423 | Up to 4 claim support rows are **prepended** ahead of retrieved rows in `best[:12]`; semantic review reads `(review_rows or best)[:8]`, so injected rows can displace retrieved rows from review context. |
| 2 | Pseudo-values | :435 + `memory_pseudo_values` | Claim `date_raw` / statement amounts join the local value/conflict computation whenever the claim's evidence row is in `best` (injected rows qualify). |
| 3 | `not_found` synthesis | :412-418 | Zero retrieved rows + any matched claim → the claim *is* the answer. |
| 4 | Conflict override | :511-516 | Requires `memory["conflicts"]`; claims alone cannot trigger it. (Now validated + both-match gated.) |
| 5 | Weak-status synthesis | :520-526 | Claims + status in {ambiguous, unverified, cloud_unavailable} → `synthesize` joins up to 3 claim statements **verbatim** as an `answered` response. |
| 6 | Unavailability suppression | :486 | Any matched claim (`memory_meta["used"]`) skips the honest `cloud/local_unavailable` explanation. |
| 7 | Attribution | :452 | `record_ids` on evidence cards. UI only; the conversation view renders just the `memory.used` badge (`yaad/ui/app.js`). |

Correctly out of reach for claims: settled `answered`/`partial`
baselines (only conflicts override those, :511), and the status-listing
path (stored state, no thresholds by design).

## 3. Failure analysis (measured)

### F1. G3-claim: background-word match → verbatim synthesis (channel 5)

- Question terms: `{publicity, budget, spring, fest}`. Record text
  ("Spring Fest registration fee"): `{spring, fest, registration,
  fee}`. Overlap `{spring, fest}` = 2 ≥ 2 → **match**.
- The question's operative words (`publicity`, `budget`) are entirely
  unmatched; the record's operative words (`registration`, `fee`) are
  entirely unasked. The match is purely on shared **background** (event
  name). Key-phrase matter overlap (`fee+registration` ∩ Q) = **0**.
- Units path yields `cloud_unavailable` offline; memory-less twin
  agrees. With the matched claim, channel 5 fires: status `answered`,
  answer `Spring Fest registration fee`, values `[]` (the statement
  does not even contain a value). **Q1 answered:** two unweighted
  shared words, with no matter/ask alignment and no corroboration
  requirement, admit the record; ranking without thresholds then
  promotes it to the answer.

### F2. Pseudo-value flip: irrelevant claim overrides a correct answer (channel 2)

- Twin (no records): single unit `The registration fee is Rs 150` →
  `answered`, `amount: Rs 150`.
- With an irrelevant matched record whose *statement* (not its unit)
  contains `Rs 999`: `conflict`, values `[Rs 150, Rs 999]`.
- The phantom rival enters solely through `memory_pseudo_values`
  (statement regex + injected row in `best`). **Q5 answered (i): yes —
  deterministically, offline.** A variant where the claim's unit is not
  even retrieved shows the same flip, proving injection + pseudo-values
  jointly manufacture a conflict from one irrelevant record.

### F3. Invented-title synthesis with zero retrieved rows (channel 3 + audit M1)

- Record title/detail `Zebra budget alpha` (words absent from every
  unit; title/detail specifics are unverified — audit M1), key
  `draft due`, excerpt from a draft unit.
- Question `zebra budget alpha?` retrieves **zero rows** yet matches the
  record 3 ≥ 2 → `answered`, `Zebra budget alpha`, memory used.
- The same unverified words both match the question *and become the
  answer*, with no retrieval corroboration at all. Twin: `not_found`.

### F4. Review-context displacement (channel 1, structural + observed)

- Injected rows sit ahead of retrieved rows in `best[:12]` and the
  `[:8]` review window. Four irrelevant injected rows can evict genuine
  rows from semantic review. Real-model evidence already exists:
  VALIDATION_RESULTS §12.4 G1 — Yaad answered from irrelevant noise
  where the baseline abstained.

### Why relationship validation cannot fix this

`relationships.py` answers "do these records concern the same
subject?" F1/F2/F3 are not relationship failures: no pair is involved
(F1, F3) or the pair is never formed (F2). The missing check is between
*record and question*, not record and record — strictly harder and
lexically underdetermined, hence the conservative tiering below.

## 4. Proposed relevance policy

**Two-tier eligibility** in `plan_memory`, reusing stored fields only:

- **Tier C (contextual)** — current `text_match` (≥2 shared words).
  Feeds injection (#1) and attribution (#7) only. Preserves recall for
  semantic review and every existing injection test.
- **Tier A (answerable)** — Tier C **plus key-phrase matter overlap ≥ 1**:
  at least one word of the record's stored matter signature
  (`record_key` part 4, digit artifacts dropped — the validated
  `key_phrase` content words) appears in the question's content terms.
  Only Tier A claims feed pseudo-values (#2) and synthesis (#3, #5).
- **Ambiguous relevance (Tier C only)** behaves as memory-unused for
  synthesis: units answer/abstention stands, evidence preserved, no
  verdict change — mirroring the relationship-validation posture.
- **Exemption:** undecided-path admissions (`unresolved` records on
  undecided-questions) stay Tier A without matter overlap: they sit at
  matter 0 by construction and are already narrowly gated on both
  sides. Status listings are exempt (no thresholds by design).
- **Exposure:** per-claim `relevance: {tier, shared, matter_overlap}`
  in the contract (additive, mirroring `conflict_validations`);
  `memory.used` keeps its current broad meaning (any match) so the UI
  badge and existing tests are untouched.

Why matter overlap, and why ≥ 1: it is the only deterministic signal in
existing fields that blocks F1/F3 (matter 0) while keeping every
legitimate synthesis fixture green — four of which (proposal,
venue-change, repeated-extraction, local-seed) sit at exactly 1 (see
Appendix). Alternatives fail calibration: raising the absolute floor
breaks floor-level legitimate matches (`shared == 2` is normal);
coverage ratios break the multi-claim fixture (second claim covers 2/4
question terms); retrieval-overlap requirements break the
unretrieved-corroboration feature and do not block F1 anyway (same
vocabulary retrieves the claim's unit); value/intent alignment does not
block F1 (the claim carries an amount) and is Phase 2 material.

Effect on the failures: F1 → contextual only, twin-identical
`cloud_unavailable`; F2 → no pseudo-value, `answered` preserved; F3 →
contextual only, `not_found` preserved (matter 0 despite 3 shared
title words — the M1 hole closed at the relevance layer without
touching extraction); F4 → unchanged in Phase 1 (see Phase 2).

## 5. Pseudocode

```python
MATTER_DIGITS = str.isdigit  # drop value-embedding artifacts ("150", "000")

def matter_words(record) -> set[str]:
    sig = str(record.get("record_key") or "").split("|")
    if len(sig) not in (5, 6):
        return set()
    return {w for w in sig[4].split("+") if w and not w.isdigit()}

def relevance_tier(record, terms, *, undecided_match) -> str:
    if undecided_match:                      # narrow gate on both sides already
        return "answerable"
    if matter_words(record) & terms:
        return "answerable"
    return "contextual"

# plan_memory: after existing text_match filter
claims_A, claims_C = [], []
for group in _group_records(matched, usable):
    claim = _claim_from_group(group, usable, terms, retrieved)
    # group tier = best member tier; keep per-record tiers for audit
    ...
plan["claims"] = claims_A                    # synthesis + pseudo-values
plan["context_claims"] = claims_C            # injection + attribution only
plan["support_rows"] = fetch(wanted(A + C))[:INJECT_CAP]   # unchanged

# synthesize / memory_pseudo_values: consume plan["claims"] (+ conflicts) only
# contract_claims: {"claims": A, "context_claims": C, "conflicts": ..., ...}
```

Notes: `record_key` signatures are computed at intake from validated
`key_phrase` content words, so no schema change and no re-extraction is
needed; pre-amount 5-part keys parse identically. Grouping stays as-is
(identity-validated); a group's tier is its best member's.

## 6. Regression scenarios (would prove the fix)

- **R1 (F1/G3-claim):** unmarked Autumn/Spring fee records; publicity
  question → status `cloud_unavailable`, byte-identical to the
  memory-less twin; `memory.claims == []`, fee record in
  `context_claims` with `tier: contextual`.
- **R2 (matter gate):** any Tier-C-only store → `synthesize` never
  called with Tier-C claims (assert via contract shape + twin equality
  across ambiguous/unverified/cloud_unavailable/not_found baselines).
- **R3 (F2/pseudo flip):** single-value units (`answered`, Rs 150) +
  irrelevant Rs-999 claim → `answered` preserved; `Rs 999` absent from
  `values`.
- **R4 (F3/invented title):** title-only match, zero retrieved rows →
  `not_found` preserved; record contextual, never synthesized.
- **R5 (no over-gating):** every existing synthesis fixture still
  synthesizes — eligible-answer, multi-source injection, stale-excluded,
  superseded-yields, proposal-labeled, unresolved-gated, paraphrase-dupe
  (1 claim/1 value), repeated-extraction union, multi-claim (2 claims/
  2 values), fee-rivals conflict. Guarded by the current suite plus one
  aggregate test pinning Tier A for the Appendix fixtures.
- **R6 (undecided exemption):** `what is still undecided?` still
  answers from the unresolved record despite matter 0.
- **R7 (displacement, Phase 2):** stubbed review client records context;
  irrelevant injected rows never evict gold retrieved rows from the
  `[:8]` review window.
- **R8 (auditability):** response carries per-claim `relevance.tier`
  and `matter_overlap`; extraction/sweep outputs unchanged (relevance
  is serve-time only).

## 7. Trade-offs and non-goals

- **Thin margin by construction:** four legitimate fixtures sit at
  matter 1; paraphrased keys (`due` vs `deadline`) score 0 and will
  conservatively lose synthesis (recall loss, same paraphrase
  fragility as audit M4; direction favors abstention over wrong
  answers). Mitigation: key-quality work (VALIDATION_RESULTS P1)
  directly strengthens this gate.
- **Key-quality dependence:** value-embedding keys are neutralized by
  digit-dropping; event-omitting keys reduce overlap (hurts recall,
  never precision).
- **F4 displacement unaddressed in Phase 1:** injection keeps current
  recall deliberately; capping/reordering review context is Phase 2.
- **No stemming, no IDF, no synonyms:** kept out to stay minimal and
  deterministic; filed as future work, not this fix.
- **Non-goals:** retrieval rewrite, embedding similarity, another
  model call, extraction/M1 changes, UI changes (UI reads only
  `memory.used`), status-listing changes, `used`-badge redefinition.

## 8. Implementation phases

- **Phase 1 — tiered eligibility (the recommended fix):** `matter_words`
  + `relevance_tier` helpers; split `plan["claims"]` /
  `plan["context_claims"]`; synthesis + pseudo-values consume Tier A
  (+ validated conflicts) only; contract exposes tiers; R1–R6 + R8.
  Estimated surface: `memory_answer.py` + tests. No schema change, no
  new egress, no threshold retuning.
- **Phase 2 — context hardening:** review-window reservation for
  retrieved rows (R7); value/intent alignment as an additional Tier A
  signal for value-intent questions; M1 title/detail containment at
  intake (removes F3's root cause instead of gating it).
- **Phase 3 — measured retrieval upgrades (only with eval):** stemming,
  rarity weighting, paraphrase battery from VALIDATION_RESULTS §11;
  each gated on the RAG-comparison harness showing a real-model win.

## 9. Appendix — matter-overlap calibration (measured)

Question terms vs stored key-phrase signature words, digit artifacts
dropped. Rule `≥ 1` separates every legitimate synthesis fixture from
the failures; any stricter rule breaks the four fixtures at exactly 1.

| Fixture | Key signature ∩ Q | n | Tier |
|---|---|---|---|
| G3 Spring-fee vs publicity Q (F1) | {} | 0 | contextual ✓ |
| Invented-title vs title-only Q (F3) | {} | 0 | contextual ✓ |
| Eligible draft answer | draft, due | 2 | answerable ✓ |
| Proposal labeled | review | 1 | answerable ✓ |
| Venue superseded-yields | venue | 1 | answerable ✓ |
| Repeated-extraction union | draft | 1 | answerable ✓ |
| Local-seed serving | draft | 1 | answerable ✓ |
| Multi-claim contingency | amount, budget | 2 | answerable ✓ |
| Multi-claim total | budget, total | 2 | answerable ✓ |
| Stale-excluded survivor | booked, hall | 2 | answerable ✓ |
| Unresolved-gated (exempt) | {} | 0 | answerable (exemption) ✓ |

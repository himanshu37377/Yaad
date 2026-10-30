# Real-model Evaluation V3 — complete report

Run: 2026-10-30, exactly one live execution, frozen V3 fixtures
(hash-verified in the dry run immediately before). Configuration:
`muse-spark-1.3` (PDF/vision/extraction/review),
`muse-voice-transcribe-1.0`, consent on, snapshot arms with
alternating order, 5 extraction repeats on `chains`/`amounts3`.
No production code or V1/V2 artifact was modified.

## Spend (reconciled from preserved egress receipts)

Unique calls, de-duplicated across snapshot copies: 6 indexing
($0.0150), 14 extraction ($0.0866), 38 semantic reviews ($0.0698).
**Actual total $0.1714** against the $0.84 estimate and $1.50 ceiling.

## Automated results (manual grading kept separate below)

- Full: **20/26** — failures U1, H5, H6, M2, D1, G2.
- Baseline: **19/26** — failures H5, H6, M1, M2, D1, D2, G2.
- H5/H6/D2 baseline failures are the designed memory-less
  difference. On like-for-like questions the full system's net
  automated edge is M1 (quoted→confirmed resolution) and D2
  (undecided listing).

Category reading (full): slot grid and slot gate pass (S1–S4, K1 —
the V2 wrong-slot failure class is closed for amount/date/person
traps tested here); three-step current values pass (H1–H3);
three-step history is partial (H4 passes; H5/H6 below); quoted→
confirmed amounts pass for current value (M1); genuine conflict
holds with both sides cited (D3); abstentions pass except U1.

Manual means (separate rubric, `manual_grades.json`):
**full 0.85, baseline 0.77.**

## Extraction reliability (5 runs each, reported individually)

Amounts (candidacy target): all 5 runs matched current **and**
quoted-superseded expectations; records 1 and 2 superseded in every
run. V2's 0/5 amount-linking failure is closed by the
confirmed-vs-quoted candidacy + validator path on this fixture.

Chains: current venue/budget/deadline and middle deadline matched
in **5/5** runs; deadline chain now supersedes transitively.
Original venue (Riverside) superseded in **0/5** runs — superseded
counts per run were 4, 2, 2, 2, 3, and the venue chain's first link
remains the unreliable step. Record counts 10–11 per run.

## Confirmed improvements over V2

1. Amount supersession: 0/5 → 5/5 (candidacy).
2. Three-step deadline chain resolves; A3-class conflict gone (H3).
3. Slot gate: budget/deadline/person traps no longer answered from
   wrong-attribute records (S2, K1, K3; U3 in V2 terms now passes).
4. Photo ranking: bicycle photo first in both arms (G2 photos order).
5. Undecided listing works (D2 full); snack undecided content honest.

## Remaining failures (stage-attributed)

1. **U1 (full only):** "Who is the Harbor Fest chief guest?" was
   answered with budget+venue claims. The slot gate covers
   amount/date slots; person/attribute-free questions still admit
   any same-event claim. Stage: relevance. Needs an asked-attribute
   check beyond amount/date, or stricter Tier A when the question
   names no attribute the claim carries.
2. **H5/H6 (both automated; full partial manually):** "What changed?"
   lists transitions but names winners only by record id, so the
   current value (Crystal Pavilion) never appears; the superseded
   listing misses Riverside because the venue chain's first link
   still does not supersede (0/5). Stage: extraction linking (venue)
   + ledger answer presentation.
3. **M2 (both):** "original hall hire cost" returns the current
   18000 (full) or conflict (baseline). Superseded records are
   excluded from serving claims, so amount history questions have
   no answer path. Stage: relevance/history serving.
4. **D1 (both):** undecided snack budget gets status `answered`
   (honest prose) instead of `unverified` — the unresolved-status
   fix only covers the synthesis path; the review path still
   settles the status. Stage: synthesis/status policy.
5. **G2 (both, automated only):** ranking is correct (bicycle
   first) but the listing answer text names no photo, so the text
   scorer fails it. Evaluation-design artifact, as in V2.

## Verdict against the success criteria

Yaad's advantage is now measurable beyond V2's narrow slice: it
resolves quoted→confirmed revisions the baseline leaves conflicted,
tracks three-step deadline/venue/budget histories, gates wrong-slot
records, and preserves genuine conflicts and abstentions. The
remaining weaknesses are concentrated in (a) venue first-link
extraction reliability, (b) history questions about superseded
amounts, (c) status qualification on the review path, and
(d) attribute-free question gating. No fixes were made in this task.

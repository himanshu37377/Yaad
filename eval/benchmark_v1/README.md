# Benchmark v1 (frozen, offline)

72 authored cases across 8 categories, one fest+academic domain
(Spring Fest run by the Cultural Council, with poster session, library
grant display, and stall bookings):

| category | n | folder |
|---|---|---|
| paraphrase | 10 | `corpus/paraphrase/` |
| near-dup entities | 8 | `corpus/near_dup/` |
| contradiction | 10 | `corpus/contradiction/` |
| temporal | 10 | `corpus/temporal/` |
| multi-doc | 8 | `corpus/multi_doc/` |
| missing-evidence | 8 | `corpus/missing_evidence/` |
| injection | 6 | `corpus/injection/` |
| multimodal | 12 | `corpus/multimodal/` |

## Frozen rule

`cases.json` (`frozen: true`) and every file under `corpus/` are frozen.
`manifest.json` holds sha256 per fixture; `freeze_hash` in `cases.json`
covers the case list plus the manifest. The runner and the offline
pytest suite refuse to run when anything drifts. **Never edit these
files after seeing results** — publish a dated v2 instead.

Calibration note (honest history): expectations were authored before
the first run; one guard (`N05` must_not `R. Rao`, a substring of the
correct answer `Dr. Rao`, hence unsatisfiable) was corrected to
`librarian` during calibration and the freeze hash regenerated. The
recorded hash is the delivered v1 freeze.

## Run (offline, $0)

```sh
python eval/benchmark_v1/run.py
```

Default mode builds one temporary database per folder, seeds 2-4
memory records per folder (current facts plus superseded generations
where history taint applies), runs three deterministic arms with cloud
disabled, and writes `results/transcript.jsonl`, `results/scores.json`,
`results/report.md`. Zero model calls: folder consent is on (so memory
serving works) but `cloud_enabled=False`, and the run aborts if any
egress receipt is logged.

- Arm `pipeline`: the real bounded `run_pipeline` (deterministic).
- Arm `keyword`: keyword-channel retrieval + local answer path.
- Arm `hybrid`: keyword+memory fused retrieval + local answer path.

`--live --max-spend` flags exist with a spend gate but resolve to a
refusal path: live model runs are reserved for later benchmark use.

## Scoring

`benchmark_score.py` (self-contained; normalizers reimplemented from
`eval/real_eval_v2/score.py`, no cross-eval imports; named to avoid a
top-level `score` module collision with the real-eval scorer):

- correctness overall + by-category;
- retrieval Recall@1/5, MRR, hard-negative pass (file level);
- citation resolves / quote-contained / must-cite-complete;
- abstention rate + false-supported rate over missing-evidence cases.

Rubrics: amounts/dates exact after normalization; conflict and
multi-doc need both sides in answer+cited-quotes plus `must_cite_files`
(deterministic arms cite both sides in evidence; a live semantic review
would verbalize them); temporal-current fails if a stale value is
presented as current in the answer; injection/abstention graded on the
status set plus `must_not_contain` over the answer text (evidence
quotes may verbatim contain retrieved injection text — that is cited
evidence, not instruction-following).

Multimodal fixtures are text units carrying `[file-kind: pdf|image|
audio]` labels; no binary fixtures are generated. Each fixture starts
with a folder-unique header line so evidence ids never collide across
folders.

## Offline results (freeze `5583a64c...`, 2026-11-03)

Current `results/`: `keyword` 72/72, `hybrid` 72/72, `pipeline` 58/72
(contradiction 9/10, injection 6/6, missing-evidence 8/8, multi-doc 8/8,
multimodal 11/12, near-dup 4/8, paraphrase 9/10, temporal 3/10).
Retrieval R@1 0.847, R@5 1.0; abstention 1.0, false-supported 0.0.
- History: the first offline run scored pipeline 10/72 (only the 8
  missing-evidence abstentions + 2 injection rows) because
  first-sentence candidates could not produce values. Memory-aware
  candidate generation plus the integration fixes below raised it to
  58/72 with frozen fixtures untouched; remaining misses concentrate
  in near-dup entities (4/8) and temporal-current (3/10). The benchmark
  pytest gates full answer quality on the supported answer arms and
  leak-freedom/error-freedom on the experimental pipeline arm.
- Integration fixes during the port (runner/seeds only; frozen
  fixtures untouched): one seed reworded past the name-grounding
  validator ("The sources disagree"); the pipeline classifier gained
  morphology-closure overlap plus the close/closes fold pair (J03);
  the pipeline verify stage gained a relevance gate (claims must
  share matter with the question; fixes J05-class false answers).

## Live use

`run.py --live --max-spend X [--folders a,b]` runs two model arms
into a separate results dir (default `results_live/`), reusing
these fixtures and this freeze hash:

- Arm `model`: the real `run_pipeline` with model stages on
  (`muse-spark-1.3`, temperature 0). Simple questions typically
  fire only the verify stage; deeper ones exercise classify and
  chronology stages. Per-case pre-flight $0.02 (upper).
- Arm `production`: cloud indexing + live extraction (capped
  $0.10/folder) + hybrid answering with automatic semantic
  review. Per-case pre-flight $0.02, per-folder extraction
  pre-flight $0.06.

Spend discipline: every step is pre-flighted against the remaining
budget and the product-level `cloud_spend_cap_usd` is set to the
cap, so both the harness and the product gates bind. An arm stops
with a recorded reason rather than exceed the cap; `--live`
without `MODEL_API_KEY` refuses (exit 3, $0). Per-case
`spend_usd` (ledger delta) and `model_calls` land in the
transcript; live arms are reported separately, never merged into
offline scores.

## Live results (same freeze, 2026-11-03, `muse-spark-1.3`)

- `model`: 10/72 (first run 18/72; the +8 came from model
  confirmation overriding the relevance gate — fixed, rerun,
  J05 back to abstention). Model verification adds no measured
  answer improvement over deterministic on value-questions;
  first-sentence candidates are the binding constraint.
- `production`: 62/72 (all 10 misses individually analyzed during
  development; 5 real minor status-label issue, 4 rubric artifacts,
  1 judgment call).
- Every arm, live and deterministic: abstention 1.0,
  false-supported 0.0. Full transcripts in `results_live/` and
  `results_live_model2/`. Total live spend $0.431.
- Later runs, same freeze (final tallies in
  `docs/SUBMISSION_EVIDENCE.md` §2): model arm 55/72 then 56/72 with
  the J06 fix confirmed (`results_live_model3/`, `results_live_model4/`);
  production 63/72 (`results_live_prod_full2/`), contradiction folder
  10/10 (`results_live_prod_contra/`), temporal folder 6/10 then 9/10
  after the supersession fix (`results_live_prod_temporal2/`,
  `results_live_prod_temporal3/`).

## Limits (read before citing scores)

- Authored fixtures, tiny-n (72 cases, 25 fixture files): scores are a
  frozen baseline, not a generalization claim.
- Offline arms are deterministic; model answer quality is measured
  only by the live arms (separate results, same freeze).
- Corpus is tuned so the deterministic pipeline can answer (single
  fest domain, distinctive wording); real folders are noisier.
- Injection grading covers the answer text only, not cited quotes.
- History serving covers amount/date slots only (memory M2 path).

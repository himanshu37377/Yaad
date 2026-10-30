# Evaluation V2 (prepared, not yet run live)

V2 is independent from V1: `eval/real_eval/` artifacts are never read
for writing and never modified. **No live calls have been made.**

## Scope

Supersession lifecycle (three-step chains), memory advantage on
history/status/proposal questions, amount and supersession-link
reliability across 5 repeated extractions, multimodal retrieval
(including the exact L5 question), citation validity **and**
completeness, unsupported-claim grading, honest abstention, and live
regressions for the three post-V1 fixes.

## Artifacts

- Fixtures: `scripts/make_real_eval_v2_data.py` -> `data/` (frozen by
  `data/manifest.json` SHA-256; byte-identical re-generation).
- Expectations: `expected.json` (24 questions, 5 folders), written
  before any run.
- Runner: `run_real_eval_v2.py` (dry-run default, zero API calls;
  `--live --max-spend` requires separate approval).
- Scorer: `score.py` (normalized dates/amounts; stale mentions are
  informational; quote-containment checked).

## Preservation (before temp DB deletion)

`results/` receives per-database egress receipts, every extraction
run's summary (accepted + rejected with reasons), full memory record
and transition dumps, unit texts, complete answer objects, a settings
snapshot, the automated report, the extraction-reliability report,
and a manual-grading template. Automated and manual results are never
merged into one number.

## Manual grading rubric (separate from automated)

Score each answer 1 / 0.5 / 0: correct current value and honest status
(1); partially correct or correct value with wrong qualification
(0.5); wrong, settled-on-stale, or unsupported (0). Attribute every
failure to one stage: source / units / extraction / validation /
retrieval / synthesis. Vision and transcription detail are graded
here only. The 5 extraction runs are reported individually; n=5 on
two folders is variance visibility, not statistical reliability.

## Commands

```sh
python scripts/make_real_eval_v2_data.py
python eval/real_eval_v2/run_real_eval_v2.py   # dry run, zero calls
# Only after separate explicit approval:
python eval/real_eval_v2/run_real_eval_v2.py --live --max-spend 2.00
```

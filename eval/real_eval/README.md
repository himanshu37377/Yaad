# Real-model evaluation (live run 2026-10-30; see `results/`)

Measures actual Meta model quality through Yaad's complete pipeline.
Deterministic tests do not measure this. **Live run completed
2026-10-30; see `results/` (transcript, report).** `results/` is
local-only (gitignored); regenerate with an approved `--live` run.

## Dataset

`scripts/make_real_eval_data.py` generates frozen synthetic fixtures in
`data/` (hash manifest in `data/manifest.json`):

- `lifecycle/`: initial PDF decision (Riverside Hall, 20000 INR,
  March 1 2027) → revision poster image (Grand Arena, 25000 INR) →
  proposal audio (John to confirm) → final text confirmation
  (Grand Arena, 25000 INR, April 2 2027), plus a library-grant
  distractor. Audio speech uses macOS `say`; without it, transcription
  is reported as *not measured*, never zero.
- `conflict/`: two confirmed catering amounts (8000 vs 9500 INR) with
  no supersession evidence. Expected: `conflict`, never settled.

Expected answers live in `expected.json`, written before any run.
Never edit them after seeing results; publish a dated v2 instead.

## Models and cost

| Capability | Model |
|---|---|
| PDF understanding, vision, extraction, review | `muse-spark-1.3` |
| Transcription | `muse-voice-transcribe-1.0` |

Coding-agent usage (Muse) is separate and not part of this spend.
`run_real_eval.py` dry-run prints an upper-bound estimate (≈20 text
calls + 30 s audio, well under $1). Actual spend is reconciled from
`egress_log` receipts after a live run.

## Arms

One index pass per folder, then a database snapshot. **Full**: snapshot
as-is. **Baseline**: copy with memory tables cleared (same indexed
evidence, units-only pipeline). Same questions, same review settings.

## Commands

```sh
python scripts/make_real_eval_data.py          # offline fixture generation
python eval/real_eval/run_real_eval.py         # dry run: ZERO API calls
# After review and explicit approval only:
python eval/real_eval/run_real_eval.py --live --max-spend 1.00
```

## Metrics

Extraction precision/recall and proposal-vs-confirmed classification;
vision content accuracy; transcription key-detail accuracy;
cross-modal association; conflict precision/recall; supersession
correctness; answer correctness, citation validity (IDs resolve),
unsupported claims, abstention. Each failure is attributed to a stage:
source → units → records → validation → retrieval → synthesis.

## Report (2026-10-30 live run)

Full **5/8**, Baseline **3/8** (deterministic substring scorer; failures
L2/L3/L5 vs L1–L5). See `results/report.md` for stage-attributed
failures, config, and limits (single synthetic domain, TTS audio as an
ASR upper bound, one run so model variance unmeasured). `results/` is
local-only (gitignored).

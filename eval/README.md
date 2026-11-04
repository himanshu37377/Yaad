# Yaad evaluations

Map of the harnesses in this directory. Small offline checks run without
credentials; anything live needs explicit spend approval per pack README.

## Harnesses

- `run_eval.py` — tiny offline check on `generated/` fixtures: PDF/text
  retrieval 3/3, abstention 1/1. Not an accuracy claim.
- `run_suites.py` + `suites/` — frozen dry-run suites (`suite_academic`,
  `suite_multimodal`, `suite_project`), offline and deterministic;
  traces land in `suites/results/`. Live model grading is NOT run here.
- `blind_grades.py` — `prepare`/`merge` helper for blinded manual grading
  of transcripts (also covered by `tests/test_eval_blinding.py`).
- `meta_ablation.py` — offline 3-arm orchestration ablation with mocked
  models (deterministic-only vs Meta-assisted vs + entailment check).
- `retrieval_ablation.py` — offline channel ablation (keyword vs
  +memory vs +semantic) on a tiny frozen corpus in a temp DB.
- `benchmark_v1/` — frozen 72-case benchmark (offline + live arms).
  All 9 `results*/` dirs are committed evidence; start at
  [`benchmark_v1/README.md`](benchmark_v1/README.md).
- `adversarial_answers/` — 8 frozen adversarial cases;
  `results_deterministic.json` scores 7/8 (A8 adjudicated, see
  `docs/SUBMISSION_EVIDENCE.md` §2).
- `paraphrase_recall/` — semantic-channel probe with a committed
  real-weight run (see its README).
- `real_eval/`, `real_eval_v2/`, `real_eval_v3/` — real-model packs;
  live runs completed 2026-10-30. Expectations are committed;
  `data/` + `results/` are local-only (gitignored).
- `real_eval_hostel/` — hostel-logistics pack (25 frozen questions);
  prepared, no live run yet. See its README.
- `image_eval/`, `image_eval_v2/` — image fixtures only; no runner or
  scored runs yet.
- `report.md` — historical snapshot (2026-10-30/31), superseded by the
  README verification block. Kept as the audit record.

## Reproducing locally

`data/` and `results/` (except the committed benchmark/suites/small-JSON
evidence above) are gitignored. Regenerate fixtures first:

```sh
python scripts/make_synthetic_data.py
python scripts/make_capability_fixtures.py
python scripts/make_real_eval_data.py
python scripts/make_real_eval_v2_data.py
python scripts/make_real_eval_v3_data.py
python scripts/make_hostel_eval_data.py
python scripts/make_image_eval_data.py
python scripts/make_image_eval_v2_data.py
```

Generators refuse to overwrite existing fixtures. Audio fixtures use
macOS `say` where available; without it, transcription is reported as
*not measured*, never zero. Live runs additionally need `MODEL_API_KEY`,
`YAAD_CLOUD_ENABLED=true`, and the per-pack `--live --max-spend` flags.

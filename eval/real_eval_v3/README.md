# Evaluation V3 (live run completed — see results/report.md)

V3 validates five fixes over the earlier rounds: slot-aware Tier A,
transitive supersession, confirmed-vs-quoted candidacy, unresolved
status, and photo ranking. One live execution on 2026-10-30 (frozen
fixtures, hash-verified): full arm 20/26 automated, manual mean 0.85
vs baseline 0.77, reconciled spend $0.1714. Earlier V1/V2 rounds were
superseded during development and are not published here.

Methodology is identical to V2: one index pass per folder, snapshot
arms (full vs memory-cleared baseline), identical settings, alternating
ask order, 5 extraction repeats on `chains`/`amounts3`, and full
preservation (egress receipts, extraction summaries with rejected
candidates, record/transition dumps, complete answers, settings)
before temporary databases are deleted. Automated and manual grading
stay separate.

Live execution is disabled by default. It requires `--live` plus
`--max-spend` at or below the approved **$1.50 ceiling**; anything
above is rejected before any call.

```sh
python scripts/make_real_eval_v3_data.py
python eval/real_eval_v3/run_real_eval_v3.py   # dry run, zero calls
# Only after separate explicit approval:
python eval/real_eval_v3/run_real_eval_v3.py --live --max-spend 1.50
```

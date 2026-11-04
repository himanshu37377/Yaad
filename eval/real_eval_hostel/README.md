# Hostel-logistics eval pack (prepared, no live run yet)

Independent domain from V3 (its directory is never touched): 25 frozen
questions over 4 folders (`complaints`, `fees`, `mess`, `rooms`) in
`expected.json`, covering direct, paraphrase, cross-document,
genuine-conflict, supersession, abstention, image, undecided-listing,
and memory-status categories.

## Fixtures and commands

```sh
python scripts/make_hostel_eval_data.py   # -> data/ (frozen manifest)
python eval/real_eval_hostel/run.py       # dry run: ZERO API calls
# After review and explicit approval only (mirrors V3, $1.50 ceiling):
python eval/real_eval_hostel/run.py --live --max-spend 1.50
```

`data/` and `results/` are local-only (gitignored). The runner reuses
`eval/real_eval_v3/run_real_eval_v3.py` unmodified via module-global
overrides; repeat folders are `fees` x3.

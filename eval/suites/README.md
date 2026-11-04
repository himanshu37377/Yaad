# Frozen dry-run suites

Three frozen suites (`suite_academic.json`, `suite_multimodal.json`,
`suite_project.json`) exercised offline and deterministically by
`../run_suites.py`: each suite corpus is built in a temp DB, the
deterministic answer pipeline runs with no model calls, and retrieval
traces plus claim checks land in `results/*_dryrun.json` (committed).

Live model grading is NOT run here; `../blind_grades.py` consumes the
traces for blinded manual grading.

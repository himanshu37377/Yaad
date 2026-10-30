# Final Architecture Audit — Yaad

Date: 2026-10-30. Method: full-code inspection, complete test suite,
deterministic evaluation harness, and targeted adversarial probes on
disposable databases. No live Meta API calls were made; no production
data was touched. Baseline before fixes: 362 passed / 2 failed + 9
errors (all sandbox loopback-bind failures in
`tests/test_local_inference.py`); evaluation 4/4.

## Executive assessment

**Ready with minor fixes** (applied below). The architecture is
technically sound and genuinely differentiated: persistent structured
memory with evidence-verified relationships, tiered claim relevance,
temporal ledger, deterministic hybrid retrieval, and consent-gated
multimodal answering — all verified in code and deterministic tests.
The positioning "multimodal personal knowledge system built on an
advanced retrieval architecture" is defensible. Real-model quality
remains the largest unmeasured risk; the prepared evaluation in
`eval/real_eval/` has not been run live.

## Findings and fixes

| Sev | Finding | Evidence | Fix |
|---|---|---|---|
| P1 | Extraction prompt never asked for `supersedes` links, starving validated supersession of real-model input | `yaad/memory/extract.py` RULES mentioned no replacement linking while the schema allowed it | Prompt now teaches explicit replacement links; stubbed production-order test supersedes end-to-end |
| P1 | A claimant that superseded its conflict partner stayed `conflicted` (invisible) until the next sweep | New regression in `tests/test_memory_extract.py` failed with `conflicted != open` | `apply_supersession_links` restores the winner in the same pass when no validated partner remains; ledger logs `conflict_cleared` |
| P1 | Repository hygiene: no commits; `.gitignore` missed `.DS_Store`, egg-info, `*.sqlite3`, the real voice recording | `git status` (all untracked); `scripts/make_cross_file_fixtures.py` calls the `.m4a` "an existing real audio recording" | `.gitignore` completed; recording excluded pending documented consent; README notes it |
| P1 | 11 local-inference tests failed/errored wherever loopback binding is sandboxed, masking real regressions | `PermissionError` on `bind(("127.0.0.1", 0))` | Fixture/probes skip only on `PermissionError`; assertions unchanged where binding works (now 11 skipped, 0 failed) |
| P2 | A folder under a hidden parent path indexed zero files silently | `discover()` checked every absolute path part | Only folder-relative parts are checked; regression in `tests/test_ingest_limits.py` |
| P2 | `hybrid_search` could crash if a unit vanished between ranking and fetch | `rows[eid]` accessed before the existence guard | Scores filtered to resolving rows first; phantom-candidate regression |
| P2 | UI showed raw `partial` status; `AskResponse` schema omitted `partial`/`local_unavailable` | `statusLabel` map; `yaad/schemas.py` | Label + schema literal completed |
| P2 | `scripts/make_capability_fixtures.py --help` rewrote fixtures | No argument parsing | `argparse` parses first; `--help`/bad flags write nothing (tests in `tests/test_script_guards.py`) |
| P3 | Memory-view UI does not surface ledger transitions (chat "What changed?" does) | `GET /api/memory/transitions` has no UI caller | Left as known limitation; demo uses the chat path |
| P3 | `mask_value` exists server-side but masking is UI-only | `yaad/privacy/masking.py` | Documented limitation, unchanged |

No P0 (critical security/data-integrity) defect was found: the server
binds to 127.0.0.1, cloud egress is double-gated (folder consent +
global switch) with per-call receipts, and `.env` is properly ignored
with no committed secrets (there are no commits yet — create the first
commit and remote before submission so hackathon-period work is
identifiable).

## Differentiation verdict

Beyond basic RAG, verified in behavior: validated conflict verdicts
naming both values with sources; supersession that preserves history
and answers "what changed" from an append-only ledger; Tier C context
that can never change a verdict; honest abstention statuses. The demo
lifecycle (PDF → image → audio proposal → confirming text) runs
through one pipeline with citations at each step. Claims must stay
scoped to deterministic behavior until the real-model evaluation runs.

## Remaining risks

1. Real-model extraction/vision/ASR/review quality unmeasured
   (evaluation prepared, awaiting approval; estimate ≈ $0.23).
2. Pure-paraphrase retrieval still misses (no embeddings, by design).
3. Single-prompt whole-folder extraction refuses very large folders
   at the spend cap instead of chunking.
4. First Git commit + new-work statement still needed for submission.

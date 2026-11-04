# Yaad evaluation traces

Byte-exact outputs captured from real runs, with one redaction: `file`
paths originally embedded the generation machine's scratch directory and
were replaced with portable `<project-root>/sample_files/…` placeholders,
because Yaad stores absolute source paths. All other values are unedited.

All traces below were generated on 2026-10-30 with cloud disabled
(`YAAD_CLOUD_ENABLED=false`, no API key), so model spend was $0.

## How to regenerate

```sh
mkdir -p /tmp/traces/corpus
cp sample_files/event_proposal.pdf sample_files/event_poster.png \
   sample_files/organizer_notes.txt \
   "sample_files/College_Event_Deadlines_and_Workshop_Topics.m4a" \
   /tmp/traces/corpus/
export YAAD_DB_PATH=/tmp/traces/yaad.sqlite3 YAAD_CLOUD_ENABLED=false
yaad add-folder /tmp/traces/corpus
yaad index                    # -> docs/traces/index-corpus.json
yaad ask "What is the registration fee?"       # -> docs/traces/ask-registration-fee.json
yaad ask "What was the keynote speaker's name?" # -> docs/traces/ask-abstention.json
```

Only the four core Autumn Fest files are copied (the two workshop
notices in `sample_files/` postdate these traces). The `sample_files/`
originals are never modified. For the current six-file demo, see the
screenshots in `README.md`.

## Trace 1 — offline index (`index-corpus.json`)

```json
{
  "indexed": 2,
  "unchanged": 0,
  "skipped": 0,
  "failed": 0,
  "partial": 2,
  "capped": 0
}
```

Proves: PDF text layer and TXT index fully offline; PNG (needs vision)
and M4A (needs transcription) report `partial`, never fake success.

## Trace 2 — gated synthesis with citations (`ask-registration-fee.json`)

Status is `cloud_unavailable`, yet the payload carries two evidence cards
(`event_proposal.pdf`, `organizer_notes.txt`, stable `ev_*` IDs) and two
exact-extracted amounts (`verified_text`). Proves: retrieval and exact
extraction run locally; multi-source synthesis refuses to guess without
review and says exactly why; citations are preserved for manual checking.

## Trace 3 — clean abstention (`ask-abstention.json`)

Status `not_found` with empty evidence/values and `memory.used: false`.
Proves: genuinely unanswerable questions abstain instead of confabulating.

## Trace 4 — synthetic harness (inline)

```sh
python scripts/make_synthetic_data.py
python eval/run_eval.py
```

```
PDF/text retrieval evidence match: 3/3 (n=3)
Abstention: 1/1 (n=1)
Development data only; no model output or real-user data included.
```

Tiny offline check only; it is not a reliability claim.

## Not included here (and why)

- `answered` / `conflict` payloads need either single-file exact matches or
  a consented Meta review. The review path is covered by `eval/report.md`
  (cloud rehearsal: 5x HTTP 200, conflict answer, ~$0.00869) and by
  `tests/test_memory_workflow.py::test_semantic_answer_requires_consent_and_rejects_unknown_citations`
  with a mock client. Reproduce with a key via the capability-suite command
  in the README.
- Memory-extraction traces need folder consent, a key, and the $0.25
  spend cap (`POST /api/memory/extract`). Covered by
  `tests/test_memory_extract.py` and `tests/test_memory_api.py`.
- Per-request Meta latency/tokens/cost: `docs/capabilities.md` and
  `docs/capability_usage.json` (no prompts, contents, or credentials).

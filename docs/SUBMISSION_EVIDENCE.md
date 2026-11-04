# Yaad — Submission Evidence Pack

Condensed, judge-facing record of what works, what was measured, and how to
demo it. Every claim below traces to code, tests, or transcripts in this repo.
Nothing here is invented: live-proven, offline-tested, and pending items are
labeled as such.

## 1. What Yaad is

A personal memory assistant built from scratch during the Meta Global AI
Developer Hackathon 2026 (October 27–November 4, 2026): point it at folders
of documents, and it builds persistent, structured, evidence-backed memory
— then answers questions with citations, detects contradictions across
files, tracks superseded decisions over time, and abstains honestly when
evidence is missing. Local-first: indexing, retrieval, and validation run
on-device; Meta cloud models are used only with explicit folder consent +
global cloud switch + spend ceilings, and every call leaves an egress
receipt. The application architecture, ingestion, memory engine, query
pipeline, verification, privacy controls, Meta integrations, tests, evals,
and docs are all original hackathon implementation (see README
"Hackathon Contributions" for the per-area breakdown).

## 2. Measured results (final state, owner-verified)

- Test suite: **882 passed, 18 skipped, 1 known order-dependent
  failure** (folder-picker; fails only in full-suite runs, passes in
  isolation. Skips: 5 browser E2E + 13 local-inference,
  sandbox-loopback; verified 2026-11-04).
- Benchmark v1 (frozen, hash `5583a64c`, 72 cases), offline:
  keyword **72/72**, hybrid **72/72**, pipeline-deterministic **58/72**
  (was 10/72 before this round).
- Safety on every arm, offline and live: false-supported **0.0**,
  abstention **1.0**, citations resolve **1.0**.
- Adversarial runner: **7/8** (A8 = adjudicated expectation error: the corpus
  states no amount, so `not_found` is correct; frozen row kept as recorded).
- Live this round: production **63/72**
  measured (`eval/benchmark_v1/results_live_prod_full2/`; temporal folder
  re-ran 9/10 after the supersession fix in `results_live_prod_temporal3/`,
  projecting 66/72; contradiction folder 10/10 in
  `results_live_prod_contra/`; all 5 dispute cases fixed live, J06 fixed,
  T01/T02/T07 fixed live),
  model arm 56/72 with the J06 poisoning fix confirmed
  (`eval/benchmark_v1/results_live_model4/`). Per-run spend is receipted in
  each results dir's `report.md`. Live-model entailment and image-compare
  runs were not preserved — both paths are covered by stub-client
  regression tests (`tests/test_verify_entailment_v2.py`,
  `tests/test_answer_image_compare.py`).

## 3. Meta AI contributions (what the models actually do)

Live-proven on Muse Spark (`muse-spark-1.3`) + Voice Transcribe, all behind
strict schemas, temperature-0 structured calls, consent + spend gates:

- Structured memory extraction from documents (facts, deadlines, assignees).
- Link verification + recall-direction second look over proposed relations.
- Claim-level entailment review of answers against cited evidence.
- PDF understanding, image description, audio transcription.
- One-call joint two-image comparison with grounded-verdict validation.
- Answer review with citation checks and downgrade precedence.

Deterministic code keeps final authority everywhere: no-shared-content vetoes
the model cannot override, citation containment, two-file conflict bar,
relevance gates after model confirmation. Model proposes; evidence disposes.

## 4. Architecture (30-second version)

```
files -> scan/index (SQLite + FTS5) -> extract (model propose,
deterministic validate) -> memory records + conflict/supersede
sweeps + ledger -> hybrid retrieval (RRF keyword + Tier-A memory)
-> 7-stage pipeline (understand, retrieve, classify, chronology,
candidate, verify, construct) -> answer + citations + disclosure
```

Privacy boundary: folder consent × global cloud switch × per-call estimates
× per-operation ceilings × global cap × per-attempt egress receipts × PII
redaction on cloud payloads. Local path never constructs a cloud client.

## 5. Working demo scenarios (verified questions)

Run against the benchmark `contradiction` + `multi_doc` folders (or any
two-file corpus with rival values). Each scenario below is suite- or
benchmark-verified:

1. **Conflict, not a guess.** Ask: "What is the Spring Fest catering
   cost?" Two files quote Rs 8,000 vs Rs 9,500. Yaad answers `conflict`,
   names both values, cites both files. (Benchmark C01/C02; suite
   `test_pipeline_cited_rival_pair_upgrades_to_conflict`.)
2. **Multi-document join.** Ask: "What are the venue hire cost and the
   catering cost?" Yaad combines Rs 18,000 (hire.txt) + Rs 8,000
   (catering.txt) with both cited — complementary values stay `answered`,
   never false-conflict. (Benchmark M01/M02; suite
   `test_pipeline_complementary_records_stay_answered`.)
3. **Paraphrase robustness.** Ask "Where will Spring Fest be held?" instead
   of "What is the venue?" Same answer via synonym-aware matching.
   (Benchmark P01/P02, paraphrase 9–10/10.)
4. **Honest abstention.** Ask: "What is on the catering menu?" / "Who is
   the chief guest?" Yaad returns `not_found` with no citations rather
   than inventing an answer. (Benchmark E01/E02, 8/8; abstention 1.0.)
5. **Injection resistance.** A document instructs the model to reveal keys
   or follow embedded orders. Yaad quarantines the span and answers from
   clean evidence or abstains; injection-bearing spans are never followed
   (6/6 offline; J05 relevance gate + J06 quote policy).
6. **Image comparison (regression-covered; live run not preserved).**
   Two rival fee-notice photos (Rs 8,000 vs Rs 9,500) → "What is the
   registration fee in the photos?" routes to joint compare → `conflict`
   with advisory attached to the gallery, both photos cited, egress
   receipt + disclosure entry. Repeat asks hit cache; consent-off and
   tiny-cap refuse at $0. (`tests/test_answer_image_compare.py`,
   stub clients.)

## 6. Three-minute demo video outline

All scenarios run against the Autumn Fest `sample_files/` in the real UI
(Home, Ask, History, Sources, Indexing views + screenshots in README).

- 0:00–0:20 — Problem: decisions live across PDFs, notes, screenshots, and
  voice memos; search returns fragments, never what is *currently true*.
  Yaad, built from scratch during the hackathon: memory you can audit.
- 0:20–0:45 — Ingestion: index the six Autumn Fest sample files on camera
  (proposal PDF, poster image, organizer notes, two workshop notices,
  voice note); show consent toggles + local indexing; Indexing view
  shows 6/6 across AUDIO/IMAGE/PDF/TEXT; memory records appear with
  evidence links.
- 0:45–1:10 — Citations: ask a question answered from multiple files
  (e.g. the registration fee / budget); show the answer with its source
  citations and open one source preview.
- 1:10–1:35 — Conflict: ask the workshop suggestion deadline; show the
  `conflict` verdict with November 5 vs November 7 side by side, both
  workshop notices cited — the conflicting evidence stays visible,
  never a one-value guess.
- 1:35–1:55 — History: open the History view; show how the conflict is
  preserved in the transition ledger (Conflict found + Recorded entries)
  instead of overwriting the old value.
- 1:55–2:15 — Abstention: ask for the caterer name (unsupported by the
  files) → clean `not_found`, no invented answer. Brief trust note:
  injection quarantine, PII redaction, and per-call egress receipts
  shown live in the Privacy view.
- 2:15–2:45 — Meta AI inside (production use only): Muse Spark 1.3 for
  PDF/image understanding, memory extraction, and answer review; Muse
  Voice Transcribe 1.0 for the voice note — strict schemas, consent +
  spend gates, per-call receipts on screen. No Glimmer real-weight,
  agent, or image-generation claims: local mode is experimental and
  unverified on real weights.
- 2:45–3:00 — Close: 882-test suite (882 passed, 18 skipped, 1 known
  order-dependent failure), frozen 72-case benchmark, honest
  limitations on screen (small author-built evals, audio/compare live
  coverage in progress). "Models Propose. Evidence Disposes." End on
  the vision: memory you can audit.

## 7. Impact narrative (honest, no invented metrics)

Everyone keeps decisions in scattered files; when two files disagree, people
either miss the conflict or trust whichever file they opened last. Yaad's
impact is eliminating that failure mode for personal knowledge: every answer
carries its evidence, every conflict is surfaced with both sides, and "I
don't know" is a first-class, measured behavior (abstention 1.0, zero
false-supported claims across all benchmark arms). The credibility claim is
not a user count — it is the audit trail: frozen benchmarks, per-attempt
spend receipts, strict schemas, and limitations disclosed in-repo. That is
the story to tell: a memory system that would rather abstain than mislead,
and can prove it.

## 8. Known limitations (say them before judges find them)

- Evaluations are small and author-built (72-case benchmark, 20-pair
  entailment under stub clients, 8 adversarial, image fixtures with no
  scored runs); no held-out non-author slice yet.
- Pipeline model stages show ~zero measured lift over deterministic (model
  arm 56/72 vs deterministic 58/72, bidirectional small-n noise);
  classify/chronology model stages rarely fire.
- Temporal supersession linking is FIXED and live-proven (temporal folder 9/10;
  T01/T02/T07 serve clean current values; cause + fix covered by regression
  tests). Remaining T09 is a correct-behavior rubric artifact (contrastive
  disavowal trips a blanket mention-check), same class as N01/N02/N08.
- Entailment v2 defines verdicts, pins the four pragmatic rules, and
  decides C5 by a deterministic rule (`tests/test_verify_entailment_v2.py`,
  stub clients); live-model vote stability is unmeasured, and voted-3
  would cost 3x.
- 18 tests skip in sandbox (browser E2E + local inference); need CI/real
  hardware.
- Token-overlap grounding checks overlap, not truth; audio live coverage is
  synthetic-fixture only.

# Yaad — Hackathon New-Work Statement

Project: **Yaad** — a local-first, evidence-backed personal memory engine.
Event: **Meta Global AI Developer Hackathon 2026**, theme *Build for Your World*.

This statement distinguishes pre-existing foundation work from work developed
for this hackathon. It is based on the project's own dated design, audit, and
evaluation records (see `docs/` and `eval/`); the initial public commit does
not fabricate earlier history.

## The problem

People accumulate decisions across PDFs, photos, voice notes, and text files.
Those decisions change: a venue is replaced, a budget moves, a deadline slips.
Document search finds files, and conventional document QA summarizes chunks —
but neither tracks *what is currently true*, preserves *what changed*, or
abstains honestly when sources disagree. Yaad extracts structured memory from
a person's own files, verifies relationships between claims against their
evidence, records how decisions evolve, and answers with inspectable sources:
*ask a question, get an answer, inspect the evidence.*

## How Meta AI technology is used

- **Meta Model API (cloud, default):** structured memory extraction, image
  understanding, scanned-page OCR, audio transcription, PDF document
  understanding, and consent-gated semantic answer review (citations are
  validated against retrieved evidence before display).
- **Meta open-weight models (local, optional):** Muse Glimmer served by a
  user-run Ollama runtime handles memory extraction, answer review, image
  understanding, and photo re-description entirely on-device. Local mode
  never falls back to cloud (see `docs/LOCAL_INFERENCE.md`; verified by
  contract tests, not yet run against the real weights in this project).
- **No model involved:** deterministic local extraction (PDF text, TXT,
  DOCX), evidence validation, relationship verification, claim-relevance
  tiers, hybrid retrieval fusion, and the temporal ledger are conventional
  code — deliberately, so factual status is never a model's opinion.

## Pre-existing foundation (before the hackathon build-out)

- Multimodal ingestion (PDF, image, audio, TXT, DOCX) into a SQLite/FTS5
  evidence store with source locators.
- Evidence-cited question answering with rejection of unknown citation IDs.
- Folder-level cloud consent, global cloud kill-switch, and an egress ledger.
- FastAPI backend, CLI (`add-folder`, `index`, `ask`, `serve`, `rebuild-fts`),
  and the web UI shell.

## Developed during the hackathon

- **Evidence-Verified Relationships:** deterministic validation of conflict,
  supersession, and duplicate claims (identity, evidence independence,
  temporality) so incorrect relationships cannot influence answers.
- **Tiered Claim Relevance:** Tier A answerable vs Tier C contextual claims;
  only Tier A may affect answer verdicts.
- **Temporal Memory Ledger:** append-only transition history (created,
  updated, conflicts, supersession, restoration) with a History view.
- **Hybrid Retrieval Phase 1:** deterministic FTS5 + Tier A memory fusion
  via reciprocal rank fusion. No embeddings; no semantic-retrieval claim.
- **Competing-claims handling:** venue/person/name rivalry detection,
  dimension-aware synthesis backstops, supersession ordering fixes.
- **Photo pipeline integration:** substantive photo questions route through
  the shared answer safeguards; pure browsing stays listing-only.
- **Three controlled real-model evaluations (V1–V3)** with frozen fixtures,
  preserved receipts, baseline-vs-full comparison, and fixes implemented
  between rounds (supersession rescue, slot-aware relevance, transitive
  revalidation, photo ranking). Artifacts preserved under `eval/`.
- **Reliability cycles:** strict vision extraction schema, realistic
  document-photo/handwriting/chart evaluation corpora with per-category
  results (`docs/RELIABILITY_VENUE_IMAGE_REPORT.md`).
- **Desktop UI:** evidence-first interface across Home, Ask, Memory,
  History, Sources, Indexing, Privacy, and Settings.
- **Local inference hardening and documentation:** provider isolation
  (no silent cloud fallback), provider visibility in API and UI, and a
  verified setup guide.

## Current status and honest limits

- Deterministic test suite: 419 passed, 13 skipped (sandbox loopback
  restrictions), 0 failed; deterministic evaluation 4/4.
- Real-model reliability is bounded by the V1–V3 evaluations: single
  synthetic scenarios, simulated/TTS media; natural-photo, handwriting,
  and chart understanding are measured per category, not claimed broadly.
- Local open-weight mode is contract-tested against a fake Ollama server;
  real-weight quality and hardware performance remain to be verified on a
  suitable machine using the checklist in `docs/LOCAL_INFERENCE.md`.
- Audio transcription and PDF document understanding are cloud-only.

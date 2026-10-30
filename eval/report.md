# Evaluation report

Local checks re-verified 2026-10-30. The cloud rehearsal below is the 2026-10-28 run.

## Local end-to-end checks

- 231 automated tests passed, covering folder picker behavior, synthetic PDF page/excerpt citations and idempotence, scanned-PDF consent gating, image-understanding search, valid and invalid audio timestamps, cloud consent gating, retries for partial files, cross-file corroboration, citation deduplication, memory extraction/validation/conflicts/supersession/serving, consent revocation, rejection of model citations outside the retrieved evidence set, malformed structured output handling, and UI regressions. No test makes network calls.
- Python compilation (via test imports) and `node --check yaad/ui/app.js` passed.

## Meta API capability checks

- The latest selected suite run returned HTTP 200 for all 10 synthetic API requests: 3 PDFs, 5 image/screenshot/table/chart cases (including a two-image comparison), 1 structured JSON request, and 1 audio transcription.
- Local structured-output validation and mocked routing/error checks passed. Video was rejected as unsupported.
- Text calls used Muse Spark 1.3. The audio call used Muse Voice Transcribe 1.0 and returned one timestamped transcript turn. Per-request latency, tokens, and cost estimates are printed by the suite at run time.
- That run's estimated cost was approximately $0.02063. Audio token usage was not returned; its estimate uses duration.

## Yaad integration rehearsal

Two clean temporary-database rehearsals used the same synthetic folder with explicit cloud consent. The final run indexed two synthetic PDFs, a synthetic image, and a synthetic English WAV recording: 4 indexed, 0 partial, 0 failed, and 10 evidence units. The two PDF deadlines produced a `conflict` answer with source links. The image question produced an `answered` result with one validated source citation. All five external requests returned HTTP 200: two PDF understanding calls, image understanding, audio transcription, and semantic answer review. Latest-run latencies were 4.1s, 21.0s, 2.5s, 3.3s, and 1.8s. Total estimated usage was about $0.00869; the semantic review returned 311 input and 287 output tokens.

## Limits

These are contract and synthetic-workflow checks, not broad accuracy or reliability measurements. Scanned-PDF OCR failure cases, long-document truncation, real-user file diversity, conflict handling beyond exact date/amount/identifier patterns, and human-rated semantic answer quality still need evaluation. Video remains unsupported.

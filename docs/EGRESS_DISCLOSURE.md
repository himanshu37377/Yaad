# Egress Disclosure: What Leaves the Device and Why

Yaad minimizes cloud-bound payloads, but minimization has a hard limit:
**raw media bytes cannot be minimized before transcribe/describe.** This
document states that limit and the controls around it.

## What cannot be minimized

- **Images sent for understanding/OCR** (`vision_extract`): the model must
  see the pixels to describe visible content or transcribe visible text.
  Cropping or downsampling would destroy the evidence being indexed.
- **Audio sent for transcription** (`transcribe`): the model must hear the
  waveform to produce turns. There is no text to redact before the first
  transcript exists.
- **PDFs sent for understanding** (`understand_pdf`): the model receives
  the file bytes plus the text-layer pages available for citation.

For these three paths the payload is the media itself: full bytes, full
duration, full pages. No redaction step runs before the call, because any
redaction would require the very analysis the call performs.

## The controls: consent + egress_log

Since the bytes cannot be minimized, two controls govern them instead:

1. **Live folder consent.** Every cloud media call requires
   `folders.cloud_consent = 1` evaluated at call time, plus
   `cloud_enabled` in app settings. Index-time file flags that go stale on
   revocation never authorize egress. Local runs (`YAAD_MODEL_PROVIDER=local`)
   send nothing off-device and do not require cloud consent.
2. **egress_log receipts.** Every cloud call writes a receipt row: purpose
   (`image understanding and visible text`, `audio transcription`,
   `PDF document understanding`), provider, model, byte count, content kind,
   status, token usage, and estimated cost. Failed and spend-capped calls
   write receipts too, since they may still have been billed.

## What IS minimized

- **Text evidence payloads** (memory extraction, answer review over
  retrieved excerpts) pass through `yaad.privacy.masking.redact_text`
  before any cloud call: spaced/dashed digit groups, long digit runs,
  alphanumeric IDs, email local parts, and `+`-prefixed phone numbers are
  masked last-4-visible. Validation and grounding always use the original
  unit texts; only the model-bound copy is minimized.
- **Model-facing prompts** in both cloud and local clients carry an
  untrusted-data instruction: retrieved documents and images are data,
  never instructions.

## Related controls

- `yaad.privacy.masking`: `redact_text`, plus the evidence-side injection
  screen (`scan_evidence_for_injection`, `quarantine_evidence`,
  `force_insufficient`) the query pipeline calls to drop tainted units
  before a model call and force an insufficient-evidence outcome when
  every cited unit was quarantined.

# Meta API capability checks

Test date: 2026-10-29
Mode: Meta Model API Standard models; synthetic fixtures only; video excluded.
The report stores capability metadata, latency, token usage, and cost estimates; it does not store prompts, file contents, responses, API keys, or auth headers.

- **Local HTTP/routing error checks (mocked; no network): PASS** — {"HTTP status and error code preserved": true, "contributor model rejected": true, "malformed response rejected": true, "standard model default": true, "timeout propagated without retry": true, "video unsupported": true}
- **PDF: deadline_notice_a.pdf: FAIL** — model `muse-spark-1.3`, endpoint `/responses`, format `application/pdf inline data URL / input_file`, HTTP 200, 6298 ms, input/output/cached tokens 2682/384/0, estimated cost $0.004985.
  Constraint/error: invalid_or_truncated_json_output (response reached its 384-token output limit)
- **PDF: deadline_notice_b.pdf: PASS** — model `muse-spark-1.3`, endpoint `/responses`, format `application/pdf inline data URL / input_file`, HTTP 200, 6632 ms, input/output/cached tokens 2682/324/0, estimated cost $0.004730.
- **PDF: synthetic_admit_card.pdf: PASS** — model `muse-spark-1.3`, endpoint `/responses`, format `application/pdf inline data URL / input_file`, HTTP 200, 6101 ms, input/output/cached tokens 2690/379/0, estimated cost $0.004973.
- **Image scene understanding: FAIL** — model `muse-spark-1.3`, endpoint `/responses`, format `PNG inline data URL / input_image`, HTTP 200, 3949 ms, input/output/cached tokens 351/320/0, estimated cost $0.001799.
- **PDF: deadline_notice_a.pdf: PASS** — model `muse-spark-1.3`, endpoint `/responses`, format `application/pdf inline data URL / input_file`, HTTP 200, 6406 ms, input/output/cached tokens 2682/118/0, estimated cost $0.003854.
- **Image scene understanding: PASS** — model `muse-spark-1.3`, endpoint `/responses`, format `PNG inline data URL / input_image`, HTTP 200, 3280 ms, input/output/cached tokens 351/370/0, estimated cost $0.002011.
- **Screenshot text understanding: PASS** — model `muse-spark-1.3`, endpoint `/responses`, format `PNG inline data URL / input_image`, HTTP 200, 3080 ms, input/output/cached tokens 663/76/0, estimated cost $0.001152.
- **Two-image comparison: PASS** — model `muse-spark-1.3`, endpoint `/responses`, format `two PNG input_image blocks`, HTTP 200, 2719 ms, input/output/cached tokens 693/212/0, estimated cost $0.001767.
- **Structured output + Pydantic: PASS** — model `muse-spark-1.3`, endpoint `/responses`, format `Responses text.format json_schema; Pydantic validation`, HTTP 200, 2251 ms, input/output/cached tokens 41/106/0, estimated cost $0.000502.
- **Synthetic speech transcription: PASS** — model `muse-voice-transcribe-1.0`, endpoint `/asr/transcribe`, format `mono, 16-bit PCM WAV at 24 kHz; multipart transcription`, HTTP 200, 2760 ms, input/output/cached tokens None/None/None, estimated cost $0.000208.
- **PDF: deadline_notice_a.pdf: PASS** — model `muse-spark-1.3`, endpoint `/responses`, format `application/pdf inline data URL / input_file`, HTTP 200, 38967 ms, input/output/cached tokens 2682/112/0, estimated cost $0.003828.
- **PDF: deadline_notice_b.pdf: PASS** — model `muse-spark-1.3`, endpoint `/responses`, format `application/pdf inline data URL / input_file`, HTTP 200, 7733 ms, input/output/cached tokens 2682/126/0, estimated cost $0.003888.
- **PDF: synthetic_admit_card.pdf: PASS** — model `muse-spark-1.3`, endpoint `/responses`, format `application/pdf inline data URL / input_file`, HTTP 200, 38968 ms, input/output/cached tokens 2690/130/0, estimated cost $0.003915.
  Validation note: Valid JSON was returned inside a Markdown code fence; validator updated to parse fenced JSON.
- **Image scene understanding: PASS** — model `muse-spark-1.3`, endpoint `/responses`, format `PNG inline data URL / input_image`, HTTP 200, 13492 ms, input/output/cached tokens 351/277/0, estimated cost $0.001616.
- **Screenshot text understanding: PASS** — model `muse-spark-1.3`, endpoint `/responses`, format `PNG inline data URL / input_image`, HTTP 200, 4938 ms, input/output/cached tokens 663/149/0, estimated cost $0.001462.
- **Screenshot table extraction: PASS** — model `muse-spark-1.3`, endpoint `/responses`, format `PNG inline data URL / input_image`, HTTP 200, 2212 ms, input/output/cached tokens 663/130/0, estimated cost $0.001381.
- **Chart reading: PASS** — model `muse-spark-1.3`, endpoint `/responses`, format `PNG inline data URL / input_image`, HTTP 200, 2640 ms, input/output/cached tokens 668/81/0, estimated cost $0.001179.
- **Two-image comparison: PASS** — model `muse-spark-1.3`, endpoint `/responses`, format `two PNG input_image blocks`, HTTP 200, 2264 ms, input/output/cached tokens 693/189/0, estimated cost $0.001669.
- **Structured output + Pydantic: PASS** — model `muse-spark-1.3`, endpoint `/responses`, format `Responses text.format json_schema; Pydantic validation`, HTTP 200, 1898 ms, input/output/cached tokens 41/111/0, estimated cost $0.000523.
- **Synthetic speech transcription: PASS** — model `muse-voice-transcribe-1.0`, endpoint `/asr/transcribe`, format `mono, 16-bit PCM WAV at 16/24 kHz; multipart /asr/transcribe`, HTTP 200, 5214 ms, input/output/cached tokens None/None/None, estimated cost $0.000209.
- **PDF: deadline_notice_a.pdf: PASS** — model `muse-spark-1.3`, endpoint `/responses`, format `application/pdf inline data URL / input_file`, HTTP 200, 4822 ms, input/output/cached tokens 2682/147/2659, estimated cost $0.001052.
- **PDF: deadline_notice_b.pdf: PASS** — model `muse-spark-1.3`, endpoint `/responses`, format `application/pdf inline data URL / input_file`, HTTP 200, 5026 ms, input/output/cached tokens 2682/176/0, estimated cost $0.004100.
- **PDF: synthetic_admit_card.pdf: PASS** — model `muse-spark-1.3`, endpoint `/responses`, format `application/pdf inline data URL / input_file`, HTTP 200, 5649 ms, input/output/cached tokens 2690/117/2659, estimated cost $0.000935.
- **Image scene understanding: PASS** — model `muse-spark-1.3`, endpoint `/responses`, format `PNG inline data URL / input_image`, HTTP 200, 4221 ms, input/output/cached tokens 351/339/0, estimated cost $0.001880.
- **Screenshot text understanding: PASS** — model `muse-spark-1.3`, endpoint `/responses`, format `PNG inline data URL / input_image`, HTTP 200, 3829 ms, input/output/cached tokens 663/114/0, estimated cost $0.001313.
- **Screenshot table extraction: PASS** — model `muse-spark-1.3`, endpoint `/responses`, format `PNG inline data URL / input_image`, HTTP 200, 2453 ms, input/output/cached tokens 663/143/0, estimated cost $0.001437.
- **Chart reading: PASS** — model `muse-spark-1.3`, endpoint `/responses`, format `PNG inline data URL / input_image`, HTTP 200, 1604 ms, input/output/cached tokens 668/96/0, estimated cost $0.001243.
- **Two-image comparison: PASS** — model `muse-spark-1.3`, endpoint `/responses`, format `two PNG input_image blocks`, HTTP 200, 2622 ms, input/output/cached tokens 693/239/0, estimated cost $0.001882.
- **Structured output + Pydantic: PASS** — model `muse-spark-1.3`, endpoint `/responses`, format `Responses text.format json_schema; Pydantic validation`, HTTP 200, 2184 ms, input/output/cached tokens 41/101/0, estimated cost $0.000481.
- **Synthetic speech transcription: PASS** — model `muse-voice-transcribe-1.0`, endpoint `/asr/transcribe`, format `mono, 16-bit PCM WAV at 16/24 kHz; multipart /asr/transcribe`, HTTP 200, 2886 ms, input/output/cached tokens None/None/None, estimated cost $0.000209.
- **PDF: deadline_notice_a.pdf: PASS** — model `muse-spark-1.3`, endpoint `/responses`, format `application/pdf inline data URL / input_file`, HTTP 200, 4498 ms, input/output/cached tokens 2682/157/0, estimated cost $0.004020.
- **PDF: deadline_notice_b.pdf: PASS** — model `muse-spark-1.3`, endpoint `/responses`, format `application/pdf inline data URL / input_file`, HTTP 200, 4422 ms, input/output/cached tokens 2682/149/0, estimated cost $0.003986.
- **PDF: synthetic_admit_card.pdf: PASS** — model `muse-spark-1.3`, endpoint `/responses`, format `application/pdf inline data URL / input_file`, HTTP 200, 4254 ms, input/output/cached tokens 2690/155/0, estimated cost $0.004021.
- **Image scene understanding: PASS** — model `muse-spark-1.3`, endpoint `/responses`, format `PNG inline data URL / input_image`, HTTP 200, 3723 ms, input/output/cached tokens 351/419/0, estimated cost $0.002220.
- **Screenshot text understanding: PASS** — model `muse-spark-1.3`, endpoint `/responses`, format `PNG inline data URL / input_image`, HTTP 200, 1866 ms, input/output/cached tokens 663/83/0, estimated cost $0.001182.
- **Screenshot table extraction: PASS** — model `muse-spark-1.3`, endpoint `/responses`, format `PNG inline data URL / input_image`, HTTP 200, 2434 ms, input/output/cached tokens 663/166/0, estimated cost $0.001534.
- **Chart reading: PASS** — model `muse-spark-1.3`, endpoint `/responses`, format `PNG inline data URL / input_image`, HTTP 200, 2026 ms, input/output/cached tokens 668/112/0, estimated cost $0.001311.
- **Two-image comparison: PASS** — model `muse-spark-1.3`, endpoint `/responses`, format `two PNG input_image blocks`, HTTP 200, 1936 ms, input/output/cached tokens 693/206/0, estimated cost $0.001742.
- **Structured output + Pydantic: PASS** — model `muse-spark-1.3`, endpoint `/responses`, format `Responses text.format json_schema; Pydantic validation`, HTTP 200, 1324 ms, input/output/cached tokens 41/84/0, estimated cost $0.000408.
- **Synthetic speech transcription: PASS** — model `muse-voice-transcribe-1.0`, endpoint `/asr/transcribe`, format `mono, 16-bit PCM WAV at 16/24 kHz; multipart /asr/transcribe`, HTTP 200, 3615 ms, input/output/cached tokens None/None/None, estimated cost $0.000209.

## Known limits

- A `PASS` confirms an API contract/request path for a synthetic fixture, not broad accuracy or reliability.
- Video is out of scope and is not included in discovery or tests.
- Meta exposes token usage in responses and the account dashboard; this tool cannot read the account credit balance.
- No max_output_tokens parameter is sent; estimated costs reflect actual returned usage when available.
- The suite ledger is local to this repository and does not account for other API keys, Playground usage, or earlier exploratory requests.
- This report summarizes all calls recorded in the local suite ledger; that ledger excludes other API keys, Playground usage, and earlier exploratory requests.

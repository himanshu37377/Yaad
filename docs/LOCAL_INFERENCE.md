# Local inference (experimental)

> Status: implemented and covered by automated contract tests against a
> fake Ollama server. **Not yet verified against the real model weights
> on real hardware in this project.** The checklist at the end of this
> guide is how to confirm it on your own machine; until then, treat
> local-mode quality (not the no-cloud-fallback behavior, which is
> test-covered) as unverified.

## What you need

- [Ollama](https://ollama.com/download) installed and running.
- The `muse-glimmer:30b` model pulled through Ollama (about 18 GB download).
- Plan for at least 24 GB of memory for that model: the 18 GB weights
  leave too little room on smaller machines. On a smaller machine, use
  a smaller model instead (see "Smaller models" below) or cloud mode.
- Yaad itself, started from this repository (see step 4).

Cloud mode stays the default. Nothing below changes it unless you set
`YAAD_MODEL_PROVIDER=local`.

## Step 1 — Install Ollama

Download and install Ollama from the official page:
<https://ollama.com/download>. Start it so it serves its API at
`http://localhost:11434` (the default).

## Step 2 — Get the model (recommended path)

```sh
ollama pull muse-glimmer:30b
```

This downloads Meta's 30-billion-parameter open model (Apache-2.0,
text and image input) in a pre-quantized 18 GB form and registers it
with your local Ollama runtime. On Apple Silicon you can instead pull
`muse-glimmer:30b-mlx` (19 GB, MLX engine) and set it as
`YAAD_LOCAL_MODEL` below. Then confirm it answers:

```sh
ollama run muse-glimmer:30b
```

Quit the interactive session when done; Yaad will call the model
itself over the local API.

## Smaller models (any Meta open-weight model you already run)

Yaad's adapter speaks only the Ollama chat API, so it is not tied to
Glimmer. If you already run another Meta open-weight model under
Ollama, point `YAAD_LOCAL_MODEL` at its tag in Step 3 — no other
change is needed. Example for a small machine:

```sh
ollama pull llama3.2:3b
```

`llama3.2:3b` is a 2 GB Meta Llama 3.2 tag that runs comfortably in
8 GB of memory. The capability trade-off is the model's, not Yaad's:

| Model property (per its Ollama page) | What works locally |
|---|---|
| Text input, structured chat output | Memory extraction, answer review |
| Image input listed (Glimmer does; Llama 3.2 does not) | Image understanding, scanned-page OCR, photo re-description |

With a text-only model, image steps fail with a clear error and the
affected files stay `partial`; nothing falls back to cloud. Smaller
models also fail Yaad's structured-output validation more often —
invalid candidates are rejected rather than stored, so memory may end
up thinner with a small model — never invented.

## About Hugging Face (optional background)

[Hugging Face](https://huggingface.co) is a popular hub where you can
discover and inspect open-weight models. Meta's official Glimmer
repository is <https://huggingface.co/meta-models/Muse-Glimmer-30B>:
public, Apache-2.0 licensed, full-precision Transformers
`safetensors` weights.

Finding weights there is not the same as running them:

- **Downloading** gives you weight files on disk. Yaad cannot use
  weight files directly.
- **Running** needs an inference runtime (Ollama here) that loads the
  weights and serves an API. Yaad's local adapter speaks only the
  Ollama API (`POST /api/chat`).

The full-precision Hugging Face checkpoint is tens of gigabytes and,
per the model card, needs 64 GB-class hardware. Ollama's documented
`Modelfile` `FROM <model directory>` mechanism can import safetensors
weights only for architectures your Ollama version supports, and
whether it accepts this custom architecture is untested here — that
path would also need real-model validation before use with Yaad.

Practical upshot: any model name your Ollama serves works with Yaad's
adapter (compatibility lives at the API layer, not the weight
source), but the only currently documented path is the prebuilt
Ollama library tags above. Anything else requires additional
verification and possibly integration work.

## Step 3 — Configure Yaad

Create or edit a file named `.env` in the repository root (the folder
containing `pyproject.toml`) and add:

```sh
YAAD_MODEL_PROVIDER=local
YAAD_LOCAL_ENDPOINT=http://localhost:11434
YAAD_LOCAL_MODEL=muse-glimmer:30b
```

Variables and defaults:

| Variable | Default | Meaning |
|---|---|---|
| `YAAD_MODEL_PROVIDER` | `cloud` | `local` selects the Ollama adapter, `cloud` selects Meta; any other value is rejected with an `Unknown model provider` error — Yaad never silently picks a side |
| `YAAD_LOCAL_ENDPOINT` | `http://localhost:11434` | Ollama API base URL (no trailing path needed) |
| `YAAD_LOCAL_MODEL` | `muse-glimmer:30b` | Model name as known to your Ollama runtime |

Yaad reads configuration once at startup, so **restart Yaad after
changing `.env`**. There is currently no in-app provider switch;
`.env` is the only configuration surface.

## Step 4 — Start Yaad and verify

```sh
yaad serve
```

Open <http://127.0.0.1:8000>, index a folder, and ask a question.
Then verify local mode is genuinely active:

1. **Active provider in-app:** the top bar shows `Local ·
   muse-glimmer:30b`, and Privacy & data names the provider, model,
   and endpoint. Open Privacy & data → Recent model activity: rows
   produced locally show `muse-glimmer:30b` in the Model column.
2. **Provider and cost (API check):**
   ```sh
   curl -s 'http://127.0.0.1:8000/api/egress?limit=5'
   ```
   Local rows carry `"provider": "local"` and
   `"estimated_cost_usd": 0.0`. Token counts there are
   runtime-reported stats, not billed usage.
3. **Negative check:** stop Ollama and ask again. Answers show the
   `Local model unavailable` badge with cited evidence, and no cloud
   rows appear — local mode never falls back to cloud.

## What works in local mode

| Operation | Local behavior |
|---|---|
| Memory extraction | Supported via structured chat output; same validation, conflicts, supersession |
| Answer review | Supported; citations checked against retrieved evidence as in cloud mode |
| Image understanding, scanned-page OCR | Supported via the model's image input |
| Photo re-description | Supported locally with no consent needed; previous descriptions restore on failure |
| PDF document understanding | **Not supported:** files stay `partial` with a clear message; local text still indexed |
| Audio transcription | **Not supported:** the model has no audio input; files stay `partial` with a clear message |
| Consent gates | Cloud consent is not required locally (nothing leaves the device); extraction stays explicit-only |

## Processing modes compared

| Capability | Local deterministic (always) | Local model (`YAAD_MODEL_PROVIDER=local`) | Cloud model (default) |
|---|---|---|---|
| PDF text, TXT, DOCX extraction | Yes, no consent needed | Same | Same |
| Image understanding, scanned-page OCR | No | Yes, no consent needed | Yes, needs consent + `YAAD_CLOUD_ENABLED=true` |
| Photo re-description | No | Yes, no consent needed | Yes, needs consent + `YAAD_CLOUD_ENABLED=true` |
| PDF document understanding (derived facts) | No | No (`partial`, clear message) | Yes, needs consent + `YAAD_CLOUD_ENABLED=true` |
| Audio transcription | No | No (`partial`, clear message) | Yes, needs consent + `YAAD_CLOUD_ENABLED=true` |
| Memory extraction | No (explicit runs only) | Yes, no consent needed, $0 spend | Yes, needs consent + key, $0.25 spend cap |
| Answer review | No (units-only answers still served) | Yes, no consent needed | Yes, needs consent on every cited folder + key |
| Relationship validation, conflicts, supersession | Yes (deterministic, both modes) | Same | Same |

Deterministic processing never calls any model. The provider switch
(`YAAD_MODEL_PROVIDER`, read once at startup) selects exactly one
model client for everything else; local mode never constructs the
cloud client, even on failures, and there is no silent fallback.

## For developers: provider architecture

- `yaad/llm/__init__.py:get_client(settings)` is the only client
  factory. `local` returns `LocalClient`, `cloud` returns `MetaClient`,
  anything else raises `ValueError` instead of guessing.
- Both clients implement `reason_json`, `vision_extract`,
  `transcribe`, `understand_pdf`, `describe_photo`, and record a
  `last_call` receipt (`provider`, `model`, `status_code`, `success`,
  token stats, `estimated_cost_usd`, `error_code`). Local cost is
  always exactly `0.0`.
- Photo description and re-description flow through `vision_extract`
  in both modes. `LocalClient.describe_photo` exists only for interface
  parity with `MetaClient`, raises `local_unsupported`, and has no
  production caller — do not wire new code to it.
- Callers must branch on `yaad.llm.is_local(settings)` for gates
  (consent, spend caps, key checks) and for user-facing error text;
  never hardcode a provider name in messages or unit `origin` labels.
- Every model call writes an `egress_log` row via the caller's receipt
  path, including failures — local rows carry `provider: local`.
- To add a provider: implement the client shape, register the name in
  `get_client`, add fake-server contract tests plus no-fallback tests
  mirroring `tests/test_local_inference.py`, and document its
  capability matrix before claiming support.

## Privacy properties and limits

- The local adapter speaks only to the configured endpoint (loopback by
  default). Tests prove the cloud client is never constructed in local
  mode, including on failures.
- If you point the endpoint at a non-loopback address, that host receives
  your prompts: local-privacy holds for runtimes you operate yourself.
- A remotely hosted Glimmer endpoint is useful for development smoke
  tests only. It is not local inference and proves nothing about privacy.
- Ollama's own network behavior (if any) is outside Yaad's audit; the
  no-cloud-fallback guarantee covers Yaad's code paths.

## Troubleshooting

- `Local model '…' is not installed. Run: ollama pull …` — pull the
  tag shown, then retry.
- `Local model did not respond at …` — Ollama is not running; start it.
- Changed `.env` but nothing changed — restart the Yaad server; settings
  load once at startup.
- `Unknown model provider: '…'` — `YAAD_MODEL_PROVIDER` must be exactly
  `cloud` or `local`. A typo is rejected rather than silently treated
  as either mode.

## Final verification against the real model (not yet done)

1. On a 24 GB+ machine: `ollama pull muse-glimmer:30b`, then ask it
   one question via `ollama run muse-glimmer:30b`.
2. On a scratch DB with cloud disabled and no API key:
   index the `yaad_cross_file_test` corpus, run one memory extraction,
   ask one corroboration question.
3. Assert: statuses are `answered`/`conflict` (not `local_unavailable`),
   every claim cites retrieved evidence, and the egress API shows only
   `provider: local` rows with zero cost.
4. Re-run with the runtime stopped: expect `local_unavailable` with
   cited evidence and still zero cloud rows.

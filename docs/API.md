# Yaad HTTP API reference

Served by `yaad serve` (default http://127.0.0.1:8000). The web UI at `/`
is a static single page; everything else is JSON. Binding a non-loopback
host requires `YAAD_API_TOKEN`, and then `/api/*` needs
`Authorization: Bearer <token>`.

## Folders and consent

| Method + path | Purpose |
|---|---|
| `POST /api/folders` | Add a folder: `{path, cloud_consent}` → `{folder_id, path, cloud_consent}` |
| `POST /api/folders/pick` | Open the macOS folder chooser and add the selection (cancel-safe; never silently revokes stored consent) |
| `POST /api/folders/{folder_id}/consent` | Set `{cloud_consent}`; marks `partial`/`failed` files for reindex retry |

## Indexing

| Method + path | Purpose |
|---|---|
| `POST /api/index` | Index `{folder_id}` (or all folders); 409 while a run is active; schedules background memory learning |
| `GET /api/index/status?folder_id=` | Files with kind, status, error, and consent state; photo schema versions |

## Ask and evidence

| Method + path | Purpose |
|---|---|
| `POST /api/ask` | Ask `{question, folder_id?}` → `{status, answer, evidence, citations, timings}`; also schedules conversation learning |
| `GET /api/evidence/{evidence_id}/preview` | Source preview (text excerpt, image, or timestamped audio) for one evidence unit |
| `POST /api/feedback` | Record helpful/not-helpful feedback for an evidence unit |

## Photos

| Method + path | Purpose |
|---|---|
| `GET /api/photos` | Searchable photo index |
| `POST /api/photos/{file_id}/redescribe` | Re-run image description (consent-gated; refuses unconsented folders without a provider call) |
| `GET /api/photos/{file_id}/preview` | Image bytes for preview |

## Memory

| Method + path | Purpose |
|---|---|
| `POST /api/memory/extract` | Run one extraction pass over `{folder_id}` now; 409 while running; reports accepted/rejected/conflicts/stale/spend |
| `GET /api/memory?folder_id=` | Stored records with evidence and per-record serving verdicts (unsettled records listed but excluded from factual answers) |
| `GET /api/memory/transitions` | Transition ledger, newest first |
| `DELETE /api/memory/{record_id}` | Remove a record (history preserved in the ledger) |
| `POST /api/memory/{record_id}/flag` | Report a problem with a record |

## Privacy and audit

| Method + path | Purpose |
|---|---|
| `GET /api/privacy` | Model provider, cloud enablement, key configuration, per-folder consent |
| `GET /api/egress?limit=&offset=` | Receipt log of model calls: time, purpose, provider, model, status, latency, tokens, cost (paginated) |

Answer `status` values: `answered`, `conflict`, `ambiguous`, `partial`,
`not_found`, `unverified`, `indexing`, `cloud_unavailable`,
`local_unavailable`, `error`. See `yaad/api/main.py` and `yaad/schemas.py`.

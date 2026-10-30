"""Decision-memory extraction pipeline (CP3A).

Candidate selection (consent-filtered), prompt assembly, one capped model
call, deterministic validation, upsert, and sweeps. Rejected candidates are
returned, never silently dropped. Served via POST /api/memory/extract;
no answer integration, no UI.
"""

from yaad.llm.meta_config import estimate_text_cost_usd
from yaad.memory.records import (
    apply_supersession_links,
    find_duplicate_candidates,
    generate_confirmed_quote_candidates,
    mark_conflicts,
    mark_stale,
    upsert_records,
    validate_candidate,
)

EXTRACTION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "records": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "record_type": {"type": "string",
                                    "enum": ["decision", "deadline", "commitment", "open_question"]},
                    "title": {"type": "string"},
                    "detail": {"type": "string"},
                    "date_value": {"type": "string"},
                    "date_raw": {"type": "string"},
                    "assignee": {"type": "string"},
                    "status": {"type": "string", "enum": ["decided", "open"]},
                    "statement_kind": {"type": "string",
                                       "enum": ["explicit", "proposal", "inferred", "unresolved"]},
                    "key_phrase": {"type": "string"},
                    "evidence": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "evidence_id": {"type": "string"},
                                "role": {"type": "string",
                                         "enum": ["supports", "contradicts", "supersedes"]},
                                "excerpt": {"type": "string"},
                            },
                            "required": ["evidence_id", "role", "excerpt"],
                        },
                    },
                },
                "required": ["record_type", "title", "detail", "date_value", "date_raw",
                             "assignee", "status", "statement_kind", "key_phrase", "evidence"],
            },
        }
    },
    "required": ["records"],
}

RULES = """Extract memory records from these indexed evidence units. Capture FOUR kinds
of items, WITH or WITHOUT dates -- dateless items matter as much as dated ones:
- decision: something settled: event dates, amounts, fees, headcount requirements,
  rules, venue choices. status 'decided'.
- deadline: something due by a date. A deadline REQUIRES a date; without one it
  is not a deadline. status 'open'.
- commitment: someone will do something. Needs an action; assignee only when a
  person is explicitly named. status 'open'.
- open_question: explicitly undecided, pending, TBD, or still open in the
  sources. status 'open'.
Grounding rules (output is validated mechanically; violations reject the record):
- Every record needs >=1 evidence row with role 'supports'.
- excerpt MUST be an exact substring copied from the cited unit's text. Never paraphrase.
- evidence_id MUST be one of the supplied IDs. Never invent one.
- key_phrase: 2-5 words, EVERY word appearing verbatim in the record's excerpts.
- date_value: YYYY-MM-DD only when the excerpt states a full date, else empty string.
  Never complete a partial date.
- date_raw: the exact source phrasing of the date, or empty string.
- assignee: exact person name from the excerpt, or empty string. Never guess.
- statement_kind: 'explicit' (stated as fact), 'proposal' (suggested/might),
  'inferred' (only when combining units; cite all of them; never carry an assignee),
  'unresolved' (source says undecided/pending/TBD/still open).
Completeness: list EVERY distinct item across every file. A fee, a headcount
requirement, a rule, or an assignment with no date is still a record -- do not
skip dateless items. Merge the same claim seen in several files into ONE record
with several evidence rows. When unsure whether two observations are one record
or two, prefer TWO separate records.
- Replacement links: when a newer explicit statement clearly replaces an older
  statement about the same matter (a moved date, a changed venue, amount, or
  assignee), give the NEWER record one extra evidence row with role
  'supersedes' citing the OLDER statement's evidence_id, with an exact excerpt
  copied from that older unit. The newer record still needs its own 'supports'
  rows. When a value changed several times, link the final record to EVERY
  earlier statement it replaces, not only the immediately previous one.
  Never link a proposal or unresolved item as the replacement, never
  link a record to its own evidence, and emit no link when the replacement is
  not explicit in the sources."""


def select_units(con, folder_id: int, require_consent: bool = True) -> list[dict]:
    """Evidence units eligible for extraction: live folder consent only.

    files.cloud_consent records consent at index time and goes stale when
    folder consent is revoked, so it must never authorize egress. Local
    runs pass require_consent=False: nothing leaves the device, so cloud
    consent (permission to send data to Meta) does not apply."""
    clause = "WHERE f.folder_id=? AND fo.cloud_consent=1" if require_consent else "WHERE f.folder_id=?"
    return [dict(row) for row in con.execute(
        f"""SELECT u.evidence_id,u.kind,u.text,u.locator,f.path AS file
           FROM units u JOIN files f ON f.id=u.file_id
           JOIN folders fo ON fo.id=f.folder_id
           {clause} ORDER BY u.id""", (folder_id,))]


def build_extraction_prompt(units: list[dict]) -> str:
    import json
    payload = [{"evidence_id": unit["evidence_id"],
                "file": str(unit["file"]).split("/")[-1],
                "kind": unit["kind"], "text": unit["text"]} for unit in units]
    return RULES + "\n\nEvidence units:\n" + json.dumps(payload, ensure_ascii=False)


def _normalize_blank(candidate: dict) -> dict:
    for field in ("date_value", "date_raw", "assignee", "detail"):
        if not str(candidate.get(field) or "").strip():
            candidate[field] = None
    return candidate


def _record_extraction_attempt(db, settings, prompt: str, info: dict,
                               spend: float | None, success: int, error_code: str | None) -> None:
    """Receipt for an extraction call that never reached the success path.

    Transport failures, spend-cap breaches, and persistence failures may
    still have been billed, so they are recorded instead of vanishing."""
    with db.transaction() as con:
        con.execute("""INSERT INTO egress_log(ts,file_id,purpose,provider,model,bytes,content_kind,
                       status_code,success,duration_ms,input_tokens,output_tokens,cached_tokens,
                       estimated_cost_usd,error_code) VALUES(CURRENT_TIMESTAMP,NULL,'memory extraction',
                       ?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (info.get("provider", "Meta Model API"),
                     info.get("model", settings.model), len(prompt.encode()), "evidence units",
                     info.get("status_code"), success, info.get("duration_ms"),
                     info.get("input_tokens"), info.get("output_tokens"),
                     info.get("cached_tokens"), spend, error_code))


def run_extraction(db, folder_id: int, run_id: str, settings, client,
                   max_spend_usd: float = 0.25) -> dict:
    """Run one extraction pass. Raises on consent, budget, or transport failure.
    Returns counts plus the rejected candidates with their validation errors."""
    from yaad.llm import is_local
    local = is_local(settings)
    if not local and (not settings.cloud_enabled or not settings.model_api_key):
        raise RuntimeError("extraction refused: cloud gate not satisfied")
    with db.connect() as con:
        units = select_units(con, folder_id, require_consent=not local)
    if not units:
        raise RuntimeError("extraction refused: no evidence units" if local else
                           "extraction refused: no consented evidence units")
    unit_texts = {unit["evidence_id"]: unit["text"] for unit in units}
    prompt = build_extraction_prompt(units)
    # Pre-flight: refuse before spending when the input alone exceeds the cap
    # (~4 chars/token at input price). Output is unknowable up front, so the
    # post-call check below stays as the backstop. Local runs cost nothing,
    # so cloud pricing is never applied to them.
    pre_spend = 0.0 if local else estimate_text_cost_usd(
        settings.model, {"input_tokens": len(prompt.encode()) // 4})
    if pre_spend > max_spend_usd:
        raise RuntimeError(
            f"extraction refused: estimated input cost ${pre_spend:.4f} exceeds cap ${max_spend_usd}")
    try:
        result = client.reason_json([{"role": "user", "content": prompt}],
                                   EXTRACTION_SCHEMA, "memory-extraction-v1")
    except Exception as exc:
        info = getattr(client, "last_call", None) or {}
        _record_extraction_attempt(db, settings, prompt, info, None, 0,
                                   info.get("error_code") or type(exc).__name__)
        raise
    info = client.last_call or {}
    usage = {"input_tokens": info.get("input_tokens", 0),
             "output_tokens": info.get("output_tokens", 0),
             "input_tokens_details": {"cached_tokens": info.get("cached_tokens", 0)}}
    spend = 0.0 if local else estimate_text_cost_usd(settings.model, usage)
    if spend > max_spend_usd:
        _record_extraction_attempt(db, settings, prompt, info, spend, 0, "spend_cap_exceeded")
        raise RuntimeError(f"extraction spend ${spend:.4f} exceeded cap ${max_spend_usd}")
    accepted, rejected = [], []
    for candidate in result.get("records", []):
        problems = validate_candidate(_normalize_blank(candidate), unit_texts)
        (accepted if not problems else rejected).append((candidate, problems))
    try:
        with db.transaction() as con:
            pruned: list = []
            ids = upsert_records(con, folder_id, run_id, [c for c, _ in accepted], unit_texts,
                                 pruned=pruned)
            rejected_links: list = []
            rejected_conflicts: list = []
            sweep_source = f"extraction:{run_id}"
            conflicts = mark_conflicts(con, folder_id, rejected=rejected_conflicts,
                                       source=sweep_source)
            generate_confirmed_quote_candidates(con, folder_id, conflicts)
            superseded = apply_supersession_links(con, folder_id, rejected=rejected_links,
                                                  source=sweep_source)
            stale = mark_stale(con, folder_id, source=sweep_source)
            dupes = find_duplicate_candidates(con, folder_id)
            con.execute("""INSERT INTO egress_log(ts,file_id,purpose,provider,model,bytes,content_kind,
                           status_code,success,duration_ms,input_tokens,output_tokens,cached_tokens,
                           estimated_cost_usd) VALUES(CURRENT_TIMESTAMP,NULL,'memory extraction',
                           ?,?,?,?,?,?,?,?,?,?,?)""",
                        (info.get("provider", "Meta Model API"),
                         info.get("model", settings.model), len(prompt.encode()), "evidence units",
                         info.get("status_code"), 1 if info.get("success") else 0,
                         info.get("duration_ms"), usage["input_tokens"],
                         usage["output_tokens"], usage["input_tokens_details"]["cached_tokens"], spend))
    except Exception as exc:
        # The model call completed and may have been billed: record the spend
        # before reporting the persistence failure. Never report success here.
        _record_extraction_attempt(db, settings, prompt, info, spend, 0,
                                   type(exc).__name__)
        raise
    return {"returned": len(accepted) + len(rejected), "accepted": len(accepted),
            "rejected": [{"title": cand.get("title"), "errors": problems} for cand, problems in rejected],
            "upserted": len(ids), "conflicts": conflicts, "superseded": superseded,
            "rejected_supersession": rejected_links,
            "rejected_conflicts": rejected_conflicts,
            "stale": stale, "dupe_hints": dupes, "pruned_evidence": pruned,
            "spend_usd": spend, "usage": usage}

"""Decision-memory extraction pipeline (CP3A).

Candidate selection (consent-filtered), prompt assembly, one capped model
call, deterministic validation, upsert, and sweeps. Rejected candidates are
returned, never silently dropped. Reached three ways: the manual
POST /api/memory/extract endpoint (admin/recovery), background scheduling
after indexing, and conversation learning scoped to one statement; every
path shares the same validation, spend caps, and consent gates.
"""

from yaad.llm.meta_config import estimate_text_cost_usd, spend_refusal
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
Rival values are separate records: when sources assert *different* values for
the same matter (amounts, dates, venues, people), emit ONE record per side,
each citing only its own side's units. Never merge rival sides into one
record and never write dispute summaries ('sources disagree', 'disputed',
'X vs Y', 'per source A ... per source B').
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


def build_extraction_prompt(units: list[dict], redact: bool = True) -> str:
    import json
    from yaad.privacy.masking import redact_text
    payload = [{"evidence_id": unit["evidence_id"],
                "file": str(unit["file"]).split("/")[-1],
                "kind": unit["kind"],
                "text": redact_text(unit["text"])[0] if redact else unit["text"]}
               for unit in units]
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


LINK_VERIFICATION_SCHEMA = {
    "type": "object",
    "properties": {"links": {"type": "array", "items": {"type": "object", "properties": {
        "index": {"type": "integer"},
        "verdict": {"type": "string", "enum": ["confirm", "reject"]},
        "reason": {"type": "string"}},
        "required": ["index", "verdict", "reason"], "additionalProperties": False}}},
    "required": ["links"],
    "additionalProperties": False,
}


def _verify_proposed_links(con, settings, client, local: bool,
                           ids: dict, max_spend_usd: float, spend: float,
                           verify_state: dict, verify_usage: dict,
                           rejected_links: list) -> float:
    """Second-pass review of proposed supersession links.

    The extraction model proposes; this call asks the model to re-check
    each proposal against both excerpts; deterministic validation in
    ``apply_supersession_links`` still makes the final decision. A
    "reject" verdict removes the proposed link (fail-closed); any
    verification failure or unparseable response leaves the links for
    deterministic validation exactly as before. Zero links means zero
    extra model calls.
    """
    if not ids:
        verify_state["skipped"] = "no records upserted"
        return spend
    marks = ",".join("?" for _ in ids.values())
    links = con.execute(
        f"""SELECT e.rowid, e.record_id, e.evidence_id, e.excerpt,
                   m.title, m.detail, u.text AS target_text
            FROM memory_record_evidence e
            JOIN memory_records m ON m.id = e.record_id
            LEFT JOIN units u ON u.evidence_id = e.evidence_id
            WHERE e.record_id IN ({marks}) AND e.role='supersedes'
            ORDER BY e.record_id, e.rowid""",
        tuple(ids.values())).fetchall()
    if not links:
        verify_state["skipped"] = "no proposed links"
        return spend
    supports = {}
    for row in con.execute(
            f"""SELECT record_id, excerpt FROM memory_record_evidence
                WHERE record_id IN ({marks}) AND role='supports'""",
            tuple(ids.values())):
        supports.setdefault(row["record_id"], []).append(row["excerpt"])
    from yaad.memory.relationships import (
        found_sequence_markers,
        has_transition,
        temporal_order,
    )
    from yaad.privacy.masking import redact_text

    def _safe(value: object) -> str:
        text = str(value or "")
        return text if local else redact_text(text)[0]

    def _order_line(claimant_text: str, target_text: str) -> str:
        order = temporal_order({}, {}, claimant_text, target_text)
        verdict = {1: "claimant-newer", -1: "claimant-older"}.get(order, "unordered")
        cmark = found_sequence_markers(claimant_text)
        tmark = found_sequence_markers(target_text)
        return (f"Temporal evidence (deterministic): claimant markers {cmark or 'none'} "
                f"(transition={has_transition(claimant_text)}); quoted-side markers "
                f"{tmark or 'none'} (transition={has_transition(target_text)}). "
                f"Computed order: {verdict}.")

    items = []
    for index, link in enumerate(links):
        claimant_text = " / ".join(part for part in
                                   (link["title"], link["detail"],
                                    " // ".join(supports.get(link["record_id"], []))) if part)
        target_text = " / ".join(part for part in
                                 (link["excerpt"], link["target_text"]) if part)
        items.append(
            f"Link {index}: record '{_safe(link['title'])}' states: {_safe(link['detail'])}. "
            f"Its evidence says: {' | '.join(_safe(e) for e in supports.get(link['record_id'], [])) or '(none)'}. "
            f"It claims to replace the quoted statement: {_safe(link['excerpt'])}. "
            f"Quoted source passage: {_safe((link['target_text'] or '')[:800])}. "
            f"{_order_line(claimant_text, target_text)}")
    prompt = ("Review each proposed replacement link. Confirm ONLY when the new "
              "statement is about the same matter as the quoted statement AND the "
              "computed order is claimant-newer. A Final notice supersedes an "
              "Update, an Update supersedes the Original, by position: replacement "
              "verbs are sufficient but not required when the order is "
              "claimant-newer. Reject when the computed order is claimant-older "
              "or unordered, and reject whenever the statements are about "
              "different subjects: order never rescues unrelated matters. "
              "When in doubt, reject.\n\n" + "\n\n".join(items))
    if not local:
        estimate = estimate_text_cost_usd(
            settings.model, {"input_tokens": len(prompt.encode()) // 4})
        if spend + estimate > max_spend_usd:
            verify_state["skipped"] = "spend cap"
            return spend
    try:
        verdicts = client.reason_json([{"role": "user", "content": prompt}],
                                      LINK_VERIFICATION_SCHEMA,
                                      "link-verification-v1")
    except Exception as exc:
        info = getattr(client, "last_call", {}) or {}
        code = info.get("error_code") or info.get("status_code")
        verify_state["skipped"] = f"verification call failed: {type(exc).__name__}" + (
            f" ({code})" if code else "")
        return spend
    info = client.last_call or {}
    verify_usage["input_tokens"] = info.get("input_tokens", 0)
    verify_usage["output_tokens"] = info.get("output_tokens", 0)
    verify_usage["input_tokens_details"]["cached_tokens"] = info.get("cached_tokens", 0)
    if not local:
        spend += estimate_text_cost_usd(settings.model, verify_usage)
    seen: set[int] = set()
    for entry in (verdicts.get("links", []) if isinstance(verdicts, dict) else []):
        try:
            index = int(entry.get("index"))
        except (TypeError, ValueError):
            continue
        if not 0 <= index < len(links) or index in seen:
            continue
        seen.add(index)
        verify_state["checked"] += 1
        if str(entry.get("verdict", "")).lower() != "reject":
            continue
        link = links[index]
        con.execute("DELETE FROM memory_record_evidence WHERE rowid=?", (link["rowid"],))
        verify_state["rejected"] += 1
        rejected_links.append({"claimant": link["record_id"],
                               "target_evidence": link["evidence_id"],
                               "reason": "model-verification: " + str(entry.get("reason", ""))[:200]})
    if not seen:
        verify_state["skipped"] = "unparseable verification response"
    # Same transaction as the extraction: one atomic pass, no second
    # connection (which would deadlock on SQLite's write lock).
    con.execute(
        """INSERT INTO egress_log(ts,file_id,purpose,provider,model,bytes,content_kind,
              status_code,success,duration_ms,input_tokens,output_tokens,cached_tokens,
              estimated_cost_usd) VALUES(CURRENT_TIMESTAMP,NULL,'link verification',
              ?,?,?,?,?,?,?,?,?,?,?)""",
        (info.get("provider", "Meta Model API"),
         info.get("model", settings.model), len(prompt.encode()), "proposed links",
         info.get("status_code"), 1 if info.get("success") else 0,
         info.get("duration_ms"), verify_usage["input_tokens"],
         verify_usage["output_tokens"],
         verify_usage["input_tokens_details"]["cached_tokens"],
         0.0 if local else estimate_text_cost_usd(settings.model, verify_usage)))
    return spend


SECOND_LOOK_SCHEMA = {
    "type": "object",
    "properties": {"links": {"type": "array", "items": {"type": "object", "properties": {
        "index": {"type": "integer"},
        "verdict": {"type": "string", "enum": ["supersedes", "none"]},
        "reason": {"type": "string"}},
        "required": ["index", "verdict", "reason"], "additionalProperties": False}}},
    "required": ["links"],
    "additionalProperties": False,
}

_SECOND_LOOK_MAX_PAIRS = 12


def _pair_rivals(claimant: dict, target: dict) -> bool:
    """Deterministic candidacy for a missed link: the pair competes on
    one stored slot or one attributed textual dimension. Keyword
    similarity alone never nominates a pair."""
    if claimant.get("date_value") and target.get("date_value") \
            and claimant["date_value"] != target["date_value"]:
        return True
    if claimant.get("amount_value") and target.get("amount_value") \
            and claimant["amount_value"] != target["amount_value"]:
        return True
    from yaad.memory.relationships import statement_dimension_values
    for dim in ("venue", "person", "name"):
        claimant_values = statement_dimension_values(
            claimant.get("title"), claimant.get("detail"), {dim})[dim]
        target_values = statement_dimension_values(
            target.get("title"), target.get("detail"), {dim})[dim]
        if claimant_values and target_values and claimant_values != target_values:
            return True
    return False


def _second_look_pairs(con, folder_id: int, claimant_ids: dict,
                       excluded: set) -> list:
    """Unlinked rival pairs a first pass may have missed: records this
    run touched (claimants) against folder records they rival but
    cite no supersession over, nominated only when the claimant is
    deterministically newer than the target -- inverted and
    unordered pairs cannot survive deterministic validation, so
    nominating them would only spend model calls to no effect.
    Record id order is the creation sequence, never fact order,
    and plays no part. Pairs already carrying a proposed link are
    validation's business, not a new model's; pairs the verifier
    rejected this run stay rejected."""
    rows = [dict(row) for row in con.execute(
        """SELECT * FROM memory_records
           WHERE folder_id=? AND status IN ('decided','open','conflicted')""",
        (folder_id,))]
    by_id = {row["id"]: row for row in rows}
    supports: dict[int, list] = {}
    for row in con.execute(
            """SELECT record_id,evidence_id,excerpt FROM memory_record_evidence
               WHERE role='supports' ORDER BY rowid"""):
        supports.setdefault(row["record_id"], []).append(
            (row["evidence_id"], row["excerpt"]))
    linked = {(row["record_id"], row["evidence_id"]) for row in con.execute(
        "SELECT record_id,evidence_id FROM memory_record_evidence WHERE role='supersedes'")}
    from yaad.memory.relationships import temporal_order

    def _texts(record):
        return " / ".join(part for part in
                           (record.get("title"), record.get("detail"),
                            " // ".join(excerpt for _, excerpt in
                                         supports.get(record["id"], []))) if part)

    pairs = []
    claimants = sorted((by_id[rid] for rid in claimant_ids.values() if rid in by_id),
                       key=lambda r: -r["id"])
    for claimant in claimants:
        if claimant["statement_kind"] != "explicit" or not supports.get(claimant["id"]):
            continue
        for target in sorted(rows, key=lambda r: r["id"]):
            if target["id"] == claimant["id"] \
                    or target["statement_kind"] == "inferred" \
                    or not supports.get(target["id"]):
                continue
            if temporal_order(claimant, target,
                              _texts(claimant), _texts(target)) != 1:
                continue
            target_first = supports[target["id"]][0]
            if (claimant["id"], target_first[0]) in excluded:
                continue
            if any((claimant["id"], eid) in linked for eid, _ in supports[target["id"]]):
                continue
            if not _pair_rivals(claimant, target):
                continue
            pairs.append((claimant, target, supports[claimant["id"]], target_first))
            if len(pairs) >= _SECOND_LOOK_MAX_PAIRS:
                return pairs
    return pairs


def _propose_missing_links(con, settings, client, local: bool, folder_id: int,
                           ids: dict, max_spend_usd: float, spend: float,
                           state: dict, usage: dict, rejected_links: list) -> float:
    """Recall-direction second look: the model may PROPOSE replacement
    links the extraction pass never emitted, over a bounded,
    deterministically nominated pair set.

    A proposal is written as a proposed supersession evidence row --
    exactly the shape extraction itself emits -- and the standard
    sweep (`apply_supersession_links`) then validates it. The model
    never writes a relationship: deterministic validation confirms or
    rejects every proposal, and rejections are reported alongside the
    extraction's own. Pairs the verifier rejected in this same run are
    never re-proposed. Zero candidate pairs means zero model calls.
    """
    if not ids:
        state["skipped"] = "no records upserted"
        return spend
    excluded = {(item["claimant"], item["target_evidence"])
                for item in rejected_links if item.get("claimant") is not None}
    pairs = _second_look_pairs(con, folder_id, ids, excluded)
    if not pairs:
        state["skipped"] = "no unlinked rival pairs"
        return spend
    from yaad.memory.relationships import found_sequence_markers
    from yaad.privacy.masking import redact_text

    def _safe(value: object) -> str:
        text = str(value or "")
        return text if local else redact_text(text)[0]

    def _marks(record) -> str:
        blob = " / ".join(part for part in
                           (record.get("title"), record.get("detail")) if part)
        found = found_sequence_markers(blob)
        return str(found) if found else "none"

    items = []
    for index, (claimant, target, claimant_supports, _first) in enumerate(pairs):
        items.append(
            f"Pair {index}: newer record '{_safe(claimant['title'])}' states: "
            f"{_safe(claimant['detail'])}. Its evidence says: "
            f"{' | '.join(_safe(excerpt) for _eid, excerpt in claimant_supports)}. "
            f"Older record '{_safe(target['title'])}' states: {_safe(target['detail'])}. "
            f"Sequence markers (deterministic): newer {_marks(claimant)}, "
            f"older {_marks(target)}.")
    prompt = ("Decide for each pair whether the newer record replaces the older "
              "one about the same matter. Pairs are nominated only when the "
              "newer record is deterministically newer (sequence position, "
              "change-of-state language, or confirmation of an estimate); "
              "answer \"supersedes\" when the newer statement addresses the same "
              "matter as the older one, even without replacement verbs -- "
              "a Final notice supersedes an Update by position. Answer "
              "\"none\" when the matters differ: order never rescues unrelated "
              "matters. When in doubt, answer \"none\".\n\n" + "\n\n".join(items))
    if not local:
        estimate = estimate_text_cost_usd(
            settings.model, {"input_tokens": len(prompt.encode()) // 4})
        if spend + estimate > max_spend_usd:
            state["skipped"] = "spend cap"
            return spend
    try:
        verdicts = client.reason_json([{"role": "user", "content": prompt}],
                                      SECOND_LOOK_SCHEMA, "link-second-look-v1")
    except Exception as exc:
        info = getattr(client, "last_call", {}) or {}
        code = info.get("error_code") or info.get("status_code")
        state["skipped"] = f"second-look call failed: {type(exc).__name__}" + (
            f" ({code})" if code else "")
        return spend
    info = client.last_call or {}
    usage["input_tokens"] = info.get("input_tokens", 0)
    usage["output_tokens"] = info.get("output_tokens", 0)
    usage["input_tokens_details"]["cached_tokens"] = info.get("cached_tokens", 0)
    if not local:
        spend += estimate_text_cost_usd(settings.model, usage)
    seen: set[int] = set()
    for entry in (verdicts.get("links", []) if isinstance(verdicts, dict) else []):
        try:
            index = int(entry.get("index"))
        except (TypeError, ValueError):
            continue
        if not 0 <= index < len(pairs) or index in seen:
            continue
        seen.add(index)
        state["checked"] += 1
        if str(entry.get("verdict", "")).lower() != "supersedes":
            continue
        claimant, _target, _supports, (target_eid, target_excerpt) = pairs[index]
        # The claimant quotes the older statement it replaces -- the
        # same evidence shape the extraction pass itself emits. The
        # sweep validates excerpt, relatedness, and subject next.
        con.execute(
            """INSERT OR IGNORE INTO memory_record_evidence(record_id,evidence_id,role,excerpt)
               VALUES(?,?,?,?)""",
            (claimant["id"], target_eid, "supersedes", target_excerpt))
        state["proposed"] += 1
    if not seen:
        state["skipped"] = "unparseable second-look response"
    con.execute(
        """INSERT INTO egress_log(ts,file_id,purpose,provider,model,bytes,content_kind,
              status_code,success,duration_ms,input_tokens,output_tokens,cached_tokens,
              estimated_cost_usd) VALUES(CURRENT_TIMESTAMP,NULL,'link second look',
              ?,?,?,?,?,?,?,?,?,?,?)""",
        (info.get("provider", "Meta Model API"),
         info.get("model", settings.model), len(prompt.encode()), "candidate pairs",
         info.get("status_code"), 1 if info.get("success") else 0,
         info.get("duration_ms"), usage["input_tokens"],
         usage["output_tokens"], usage["input_tokens_details"]["cached_tokens"],
         0.0 if local else estimate_text_cost_usd(settings.model, usage)))
    return spend


def run_extraction(db, folder_id: int, run_id: str, settings, client,
                   max_spend_usd: float = 0.25,
                   only_evidence_ids: set[str] | None = None) -> dict:
    """Run one extraction pass. Raises on consent, budget, or transport failure.
    Returns counts plus the rejected candidates with their validation errors.
    `only_evidence_ids` scopes the pass to a subset of eligible units
    (conversation learning extracts from a single new statement)."""
    from yaad.llm import is_local
    local = is_local(settings)
    if not local and (not settings.cloud_enabled or not settings.model_api_key):
        raise RuntimeError("extraction refused: cloud gate not satisfied")
    with db.connect() as con:
        units = select_units(con, folder_id, require_consent=not local)
    if only_evidence_ids is not None:
        units = [unit for unit in units if unit["evidence_id"] in only_evidence_ids]
    if not units:
        raise RuntimeError("extraction refused: no evidence units" if local else
                           "extraction refused: no consented evidence units")
    # Validation and grounding always use the original unit texts. Only the
    # cloud-bound prompt payload is minimized: local runs keep originals
    # because nothing leaves the device.
    unit_texts = {unit["evidence_id"]: unit["text"] for unit in units}
    prompt = build_extraction_prompt(units, redact=not local)
    # Pre-flight: refuse before spending when the input alone exceeds the cap
    # (~4 chars/token at input price). Output is unknowable up front, so the
    # post-call check below stays as the backstop. Local runs cost nothing,
    # so cloud pricing is never applied to them.
    pre_spend = 0.0 if local else estimate_text_cost_usd(
        settings.model, {"input_tokens": len(prompt.encode()) // 4})
    if pre_spend > max_spend_usd:
        raise RuntimeError(
            f"extraction refused: estimated input cost ${pre_spend:.4f} exceeds cap ${max_spend_usd}")
    if not local:
        refusal = spend_refusal(db, settings, pre_spend)
        if refusal:
            raise RuntimeError(f"extraction refused: {refusal}")
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
    verify_state: dict = {"checked": 0, "rejected": 0, "skipped": None}
    verify_usage = {"input_tokens": 0, "output_tokens": 0,
                    "input_tokens_details": {"cached_tokens": 0}}
    try:
        with db.transaction() as con:
            pruned: list = []
            ids = upsert_records(con, folder_id, run_id, [c for c, _ in accepted], unit_texts,
                                 pruned=pruned)
            rejected_links: list = []
            rejected_conflicts: list = []
            spend = _verify_proposed_links(con, settings, client, local, ids,
                                               max_spend_usd, spend, verify_state,
                                               verify_usage, rejected_links)
            second_look_state: dict = {"checked": 0, "proposed": 0, "skipped": None}
            second_look_usage = {"input_tokens": 0, "output_tokens": 0,
                                 "input_tokens_details": {"cached_tokens": 0}}
            spend = _propose_missing_links(con, settings, client, local, folder_id, ids,
                                           max_spend_usd, spend, second_look_state,
                                           second_look_usage, rejected_links)
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
    usage["input_tokens"] += verify_usage["input_tokens"] + second_look_usage["input_tokens"]
    usage["output_tokens"] += verify_usage["output_tokens"] + second_look_usage["output_tokens"]
    usage["input_tokens_details"]["cached_tokens"] += (
        verify_usage["input_tokens_details"]["cached_tokens"]
        + second_look_usage["input_tokens_details"]["cached_tokens"])
    return {"returned": len(accepted) + len(rejected), "accepted": len(accepted),
            "rejected": [{"title": cand.get("title"), "errors": problems} for cand, problems in rejected],
            "upserted": len(ids), "conflicts": conflicts, "superseded": superseded,
            "link_verification": verify_state,
            "link_second_look": second_look_state,
            "rejected_supersession": rejected_links,
            "rejected_conflicts": rejected_conflicts,
            "stale": stale, "dupe_hints": dupes, "pruned_evidence": pruned,
            "spend_usd": spend, "usage": usage}

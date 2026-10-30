import json
import re
import time
from pathlib import Path
from yaad.config import Settings, get_settings
from yaad.privacy.masking import is_sensitive
from yaad.query.retrieve import STOPWORDS, search
from yaad.query.photos import _question_hits, _rank_photos, all_photos_intent, photo_intent, photo_value_intent, search_photos, list_photos
from yaad.query.memory_answer import changes_listing_response, contract_claims, fetch_support_rows, historical_response, memory_pseudo_values, plan_memory, record_ids_by_evidence, status_listing_response, synthesize, unvalidated_amount_rivalry

NO_MEMORY = {"used": False, "record_ids": [], "claims": [], "context_claims": [],
             "conflicts": [], "conflict_validations": []}

DATE_PATTERNS = [
    re.compile(r"\b\d{4}-\d{1,2}-\d{1,2}\b"),
    re.compile(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b"),
    re.compile(r"\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{1,2}(?:st|nd|rd|th)?(?:,?\s*\d{4})?\b", re.I),
]

AMOUNT_PATTERNS = [
    re.compile(r"(?:INR|USD|EUR|\$|₹|Rs\.?|rupees?)\s*\d[\d,]*(?:\.\d{1,2})?", re.I),
    re.compile(r"\d[\d,]*(?:\.\d{1,2})?\s*(?:INR|USD|EUR|\$|₹|Rs\.?|rupees?)", re.I),
]

CROSS_FILE_HINTS = ("corroborat", "other file", "other source", "elsewhere",
                    "across files", "multiple sources", "also mention", "mentioned elsewhere")

DECISION_HINTS = ("decid", "undecided", "finalis", "finaliz", "chosen",
                  "settled", "agreed", "narrowed", "picked", "selected")

UNDECIDED_MARKERS = ("undecided", "still need to be decided", "to be decided", "tbd",
                     "not yet decided", "not been decided", "hasn't been decided",
                     "haven't been decided", "has not been decided", "have not been decided",
                     "pending decision", "decision is pending", "remain undecided",
                     "remains undecided", "yet to be decided", "not finalized",
                     "not finalised", "yet to be finalized", "yet to be finalised",
                     "need to decide", "needs to be decided", "have to decide",
                     "no decision", "still open")

def extract_field_value(question: str, text: str):
    q = question.lower()
    field = None
    patterns = []
    if any(term in q for term in ("deadline", "date", "when", "due")):
        field, patterns = "date", DATE_PATTERNS
    elif any(term in q for term in ("fee", "amount", "deposit", "cost", "price", "payment")):
        field, patterns = "amount", AMOUNT_PATTERNS
    elif any(term in q for term in ("roll number", "registration number", "application number", "id number", "identifier")):
        field = "identifier"
        patterns = [re.compile(r"(?:roll|registration|application|candidate|student)\s*(?:no\.?|number|id)?\s*[:#-]?\s*([A-Z0-9][A-Z0-9/-]{3,})", re.I)]
    if not field:
        return None, []
    values = []
    for pattern in patterns:
        values.extend(pattern.findall(text))
    values = [v.strip() for v in values if isinstance(v, str)]
    return field, list(dict.fromkeys(values))

def _is_cross_file_question(question: str) -> bool:
    q = question.casefold()
    return any(hint in q for hint in CROSS_FILE_HINTS)


def _transcript_expansion_query(texts: list[str]) -> str:
    """Build a follow-up query from a recording's transcript: dates and amounts
    first (precise cross-file anchors), then distinctive longer words."""
    blob = " ".join(texts)
    anchors = []
    for pattern in DATE_PATTERNS + AMOUNT_PATTERNS:
        anchors.extend(pattern.findall(blob))
    words = list(dict.fromkeys(word for word in re.findall(r"[\w-]{6,}", blob.casefold())
                               if word not in STOPWORDS))
    return " ".join(anchors + words[:20])


def _with_corroborating_rows(db, question, folder_id, audio_rows):
    """Merge the recording's units with matching units from other files so a
    corroboration question has multi-source context instead of audio alone."""
    seen = {row["id"] for row in audio_rows}
    merged = list(audio_rows)
    with db.connect() as con:
        audio_files = {row[0] for row in con.execute(
            "SELECT id FROM files WHERE kind='audio' AND (? IS NULL OR folder_id=?)",
            (folder_id, folder_id))}
    queries = [question, _transcript_expansion_query([row["text"] for row in audio_rows])]
    per_file = {}
    for query in queries:
        if not query.strip():
            continue
        for row in search(db, query, folder_id, 20):
            if row["file_id"] in audio_files or row["id"] in seen:
                continue
            # One verbose file must not crowd other sources out of review context.
            if per_file.get(row["file_id"], 0) >= 3:
                continue
            seen.add(row["id"])
            per_file[row["file_id"]] = per_file.get(row["file_id"], 0) + 1
            merged.append(row)
            if len(merged) >= 15:
                return merged
    return merged


def _evidence_display_key(item):
    """Identity of a source card as the user sees it: file plus locator label."""
    locator = item.get("locator")
    if not isinstance(locator, dict):
        return ("other", item.get("id"))
    kind = locator.get("type")
    if kind == "pdf_page":
        return ("pdf", item.get("file"), locator.get("page"))
    if kind == "audio_segment":
        return ("audio", item.get("file"), locator.get("start"), locator.get("end"))
    if kind == "image":
        return ("image", item.get("file"))
    if kind == "text_span":
        return ("text", item.get("file"), locator.get("section"))
    return ("other", item.get("id"))


def _dedupe_evidence(evidence, values):
    """Collapse source cards that would render identically (for example several
    derived facts from the same PDF page), keeping the highest-ranked card and
    remapping value citations onto the surviving evidence IDs."""
    unique = list({item["id"]: item for item in evidence}.values())
    kept, remap, deduped = {}, {}, []
    for item in unique:
        key = _evidence_display_key(item)
        if key in kept:
            remap[item["id"]] = kept[key]
        else:
            kept[key] = item["id"]
            deduped.append(item)
    for value in values:
        value["evidence_ids"] = [remap.get(eid, eid) for eid in value.get("evidence_ids", [])]
    merged_values, seen_values = [], set()
    for value in values:
        signature = (value.get("field"), value.get("value"), tuple(value.get("evidence_ids", [])))
        if signature not in seen_values:
            seen_values.add(signature)
            merged_values.append(value)
    values[:] = merged_values
    return deduped


def _superseded_units(db, folder_id: int | None) -> frozenset:
    """Evidence IDs whose only supporting records are superseded history.

    Empty when no memory records exist in scope, so the units pipeline
    behaves exactly as before memory was ever consulted."""
    with db.connect() as con:
        exists = con.execute("SELECT 1 FROM memory_records WHERE (? IS NULL OR folder_id=?) LIMIT 1",
                             (folder_id, folder_id)).fetchone()
        if not exists:
            return frozenset()
        rows = con.execute("""SELECT e.evidence_id,m.status FROM memory_record_evidence e
                              JOIN memory_records m ON m.id=e.record_id
                              WHERE (? IS NULL OR m.folder_id=?) AND e.role='supports'""",
                           (folder_id, folder_id)).fetchall()
    by_unit: dict[str, set] = {}
    for row in rows:
        by_unit.setdefault(row["evidence_id"], set()).add(row["status"])
    return frozenset(eid for eid, statuses in by_unit.items()
                     if "superseded" in statuses and not (statuses - {"superseded", "stale"}))


def _memory_wanted(memory) -> list[str]:
    wanted: list[str] = []
    for claim in memory["claims"]:
        wanted.extend(claim["evidence_ids"])
    for pair in memory["conflicts"]:
        for claim in pair:
            wanted.extend(claim["evidence_ids"])
    return list(dict.fromkeys(wanted))


def _explicitly_undecided(question, answer, rows, cited) -> bool:
    """True when a decision-state question is answered by sources that explicitly
    state the matter is undecided — a supported answer, not missing evidence."""
    if not cited or not answer.strip():
        return False
    if not any(hint in question.casefold() for hint in DECISION_HINTS):
        return False
    if not any(marker in answer.casefold() for marker in UNDECIDED_MARKERS):
        return False
    return any(row["evidence_id"] in cited
               and any(marker in row["text"].casefold() for marker in UNDECIDED_MARKERS)
               for row in rows)


def _semantic_review(db, question, rows, settings, tainted=frozenset()):
    from yaad.llm import is_local
    local = is_local(settings)
    if not rows:
        return None
    if not local and (not settings.cloud_enabled
                      or not all(bool(row["folder_cloud_consent"]) for row in rows)):
        return None
    schema = {
        "type": "object",
        "properties": {
            "status": {"type": "string", "enum": ["answered", "conflict", "partial", "unverified"]},
            "answer": {"type": "string"},
            "claims": {"type": "array", "items": {"type": "object", "properties": {
                "claim": {"type": "string"},
                "evidence_ids": {"type": "array", "items": {"type": "string"}},
            }, "required": ["claim", "evidence_ids"], "additionalProperties": False}},
        },
        "required": ["status", "answer", "claims"],
        "additionalProperties": False,
    }
    source = []
    for row in rows:
        try:
            locator = json.loads(row["locator"])
        except (TypeError, json.JSONDecodeError):
            continue
        source.append({"evidence_id": row["evidence_id"], "source_name": Path(row["path"]).name,
                       "source_type": row["file_kind"], "locator": locator, "text": row["text"][:1800],
                       "historical": row["evidence_id"] in tainted})
    if not source:
        return None
    egress_id = None
    client = None
    try:
        from yaad.llm import get_client
        client = get_client(settings)
        try:
            result = client.reason_json([{"role": "user", "content": json.dumps({
                                            "question": question,
                                            "sources": source,
                                            "rules": [
                                                "Answer the question by combining evidence when appropriate.",
                                                "Cite every factual claim with the supplied evidence IDs.",
                                                "Do not treat README or test-guide files as authoritative when an original PDF, DOCX, TXT, or image directly supports a fact.",
                                                "Dates for different milestones are not contradictions; name what each date describes.",
                                                "If sources truly disagree about the same fact, explain the disagreement and cite both.",
                                                "If the sources explicitly state that a decision is undecided, pending, or to be determined, report that as the answer with status answered: an explicit 'not decided' is a supported answer, not insufficient evidence.",
                                                "If some parts of a multi-part question are supported by evidence while other parts have no supporting evidence either way, use status partial, answer the supported parts, and say plainly which parts lack evidence.",
                                                "Sources marked historical record outdated information; never present them as current facts.",
                                                "If the evidence is insufficient, abstain honestly.",
                                            ]}, ensure_ascii=False)}],
                                        schema, "evidence_answer")
        finally:
            info = client.last_call
            with db.transaction() as con:
                cursor = con.execute("""INSERT INTO egress_log(file_id,unit_id,purpose,provider,model,bytes,content_kind,
                            status_code,success,duration_ms,input_tokens,output_tokens,cached_tokens,estimated_cost_usd,error_code)
                            VALUES(NULL,NULL,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                            ("semantic answer review", info.get("provider", "Meta Model API"),
                             info.get("model", settings.model), sum(len(row["text"][:1200].encode("utf-8")) for row in rows),
                             f"retrieved text excerpts from {len({row['file_id'] for row in rows})} "
                             f"{'local' if local else 'consented'} source(s)",
                             info.get("status_code"), None if info.get("success") is None else int(info["success"]),
                             info.get("duration_ms"), info.get("input_tokens"), info.get("output_tokens"),
                             info.get("cached_tokens"), info.get("estimated_cost_usd"), info.get("error_code")))
            egress_id = cursor.lastrowid
        allowed = {row["evidence_id"] for row in rows}
        claims = result.get("claims")
        if not isinstance(claims, list):
            raise ValueError("Missing claims in structured answer")
        cited = []
        valid_claims = []
        for claim in claims:
            ids = claim.get("evidence_ids", []) if isinstance(claim, dict) else []
            if not isinstance(ids, list) or not ids or any(item not in allowed for item in ids):
                raise ValueError("Answer claim did not cite retrieved evidence")
            if not str(claim.get("claim", "")).strip():
                raise ValueError("Answer claim was empty")
            valid_claims.append(claim)
            cited.extend(ids)
        cited = list(dict.fromkeys(cited))
        status = result.get("status")
        if status in {"answered", "partial"} and (not cited or not valid_claims or not result.get("answer", "").strip()):
            status = "unverified"
        if status in {"answered", "partial"} and cited and all(item in tainted for item in cited):
            status = "unverified"
        if status == "conflict":
            file_ids = {row["file_id"] for row in rows if row["evidence_id"] in cited}
            if len(file_ids) < 2:
                status = "unverified"
        return {"status": status if status in {"answered", "conflict", "partial", "unverified"} else "unverified",
                "answer": result.get("answer", "")[:1200], "evidence_ids": cited, "egress_id": egress_id}
    except Exception:
        info = getattr(client, "last_call", {}) if client else {}
        status_code = info.get("status_code")
        error_code = info.get("error_code")
        if local and error_code == "local_unreachable":
            message = (f"Local model did not respond at {settings.local_endpoint}. "
                       "Start Ollama and retry.")
        elif local and error_code == "local_model_missing":
            message = (f"Local model '{settings.local_model}' is not installed. "
                       f"Run: ollama pull {settings.local_model}")
        elif local and error_code in {"malformed_structured_response", "invalid_json"}:
            message = "Local model returned an unreadable answer. Retry the question."
        elif local:
            message = "Local model review failed before a verifiable answer was returned. Retry the question."
        elif status_code and int(status_code) >= 400:
            message = f"Meta answer review failed (HTTP {status_code}). Retry the question."
        elif error_code in {"TimeoutError", "URLError", "Timeout"}:
            message = "Meta answer review timed out. Retry the question."
        elif error_code in {"malformed_structured_response", "invalid_json"}:
            message = "Meta returned an unreadable answer. Retry the question."
        elif not settings.model_api_key:
            message = "Meta answer review is unavailable because MODEL_API_KEY is not configured."
        else:
            message = "Meta answer review failed before a verifiable answer was returned. Retry the question."
        return {"status": "error", "answer": message, "evidence_ids": [], "egress_id": egress_id}


def _parse_date_value(text: str) -> str | None:
    cleaned = text.strip().rstrip(",").replace(",", "")
    for fmt in ("%Y-%m-%d", "%B %d %Y", "%b %d %Y", "%d %B %Y", "%d %b %Y",
                "%m/%d/%Y", "%d/%m/%Y", "%m-%d-%Y", "%d-%m-%Y"):
        try:
            import datetime
            return datetime.datetime.strptime(cleaned, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _rival_is_superseded(db, folder_id, rival: str, field: str) -> bool:
    """True when a superseded record asserts exactly this rival slot
    value (matched on its raw phrasing, ISO date, or normalized
    amount). Current records asserting the rival block the rescue:
    the caller only asks about values no current claim carries."""
    from yaad.memory.records import normalize_amount
    with db.connect() as con:
        rows = con.execute(
            "SELECT date_value,date_raw,amount_value FROM memory_records "
            "WHERE status='superseded' AND (? IS NULL OR folder_id=?)",
            (folder_id, folder_id)).fetchall()
    for row in rows:
        if field == "date":
            if row["date_raw"] and rival.casefold() == row["date_raw"].strip().casefold():
                return True
            parsed = _parse_date_value(rival)
            if parsed and row["date_value"] == parsed:
                return True
        elif field == "amount":
            if row["amount_value"] and normalize_amount(rival) == row["amount_value"]:
                return True
    return False


def _supersession_rescue(db, question, folder_id, memory, best) -> bool:
    """Narrow L3 rescue: a `conflict` status may be resolved from
    memory only when exactly one Tier A claim carries the asked slot,
    no validated memory conflict exists, and every competing slot
    value in the retrieved units is asserted by a superseded record.
    Anything else keeps the conflict exactly as before."""
    if memory["conflicts"] or len(memory["claims"]) != 1:
        return False
    claim = memory["claims"][0]
    claim_values = set()
    if claim.get("date_raw"):
        claim_values.add(claim["date_raw"].strip().casefold())
    found = None
    for pattern in AMOUNT_PATTERNS:
        found = pattern.search(claim.get("statement") or "")
        if found:
            break
    if found:
        claim_values.add(found.group(0).strip().casefold())
    slot: dict[str, tuple[str, str]] = {}
    for row in best:
        row_field, vals = extract_field_value(question, row["text"])
        if not row_field:
            continue
        for value in vals:
            slot[value.strip().casefold()] = (value.strip(), row_field)
    if not slot:
        return False
    claimed = claim_values & set(slot)
    if len(claimed) != 1:
        return False
    rivals = set(slot) - claimed
    if not rivals:
        return False
    return all(_rival_is_superseded(db, folder_id, slot[key][0], slot[key][1])
               for key in rivals)


def _photo_disclosure(db, photos: list[dict]) -> list[dict]:
    """Per-file egress disclosure for listed photos, mirroring the
    main pipeline join. Empty when nothing was cloud-processed; one
    entry per logged call, never duplicated or invented."""
    file_ids = sorted({photo["file_id"] for photo in photos})
    if not file_ids:
        return []
    marks = ",".join("?" for _ in file_ids)
    with db.connect() as con:
        calls = con.execute(f"SELECT e.*,f.path FROM egress_log e LEFT JOIN files f ON f.id=e.file_id WHERE e.file_id IN ({marks}) ORDER BY e.ts", file_ids).fetchall()
    return [{"what": f"{call['content_kind']}, {call['path'] or 'retrieved excerpts'}", "why": call["purpose"],
             "to": call["provider"], "model": call["model"], "when": call["ts"]} for call in calls]


def _stage(timings: dict | None, key: str, start: float) -> None:
    """Record one stage duration (ms). Counts only, never content."""
    if timings is not None:
        timings[key] = round((time.perf_counter() - start) * 1000)


def answer_question(db, question: str, folder_id: int | None = None, settings: Settings | None = None,
                    timings: dict | None = None, channels: tuple[str, ...] | None = None) -> dict:
    settings = settings or get_settings()
    with db.connect() as con:
        active = con.execute(
            "SELECT 1 FROM index_runs WHERE state='running' AND (folder_id=? OR ? IS NULL) LIMIT 1",
            (folder_id, folder_id),
        ).fetchone()
    if active:
        return {"status": "indexing", "answer": "Indexing is still running. Ask again when it finishes.",
                "values": [], "evidence": [], "candidates": [], "disclosure": [],
                "memory": dict(NO_MEMORY)}
    if photo_intent(question) and not photo_value_intent(question):
        photos = list_photos(db, folder_id) if all_photos_intent(question) else search_photos(db, question, folder_id)
        # Question-aware ranking also applies to browse listings: a
        # content-bearing browse ("show photos of the red bicycle")
        # puts the best-matching photo first; pure browsing (no hits)
        # keeps path order. Memory never enters this branch.
        if photos and max(_question_hits(p, question) for p in photos) > 0:
            photos = _rank_photos(photos, question)
        if not photos:
            return {"status": "not_found", "answer": "No matching indexed photos were found in the selected scope.",
                    "values": [], "evidence": [], "candidates": [], "disclosure": [], "photos": [],
                    "memory": dict(NO_MEMORY)}
        is_location = any(word in question.casefold() for word in ("where", "location", "from ", "near ", " in "))
        if all_photos_intent(question):
            answer = f"Showing {len(photos)} indexed photo(s) from the selected scope."
        elif is_location:
            confidence = {photo.get("location_confidence") for photo in photos}
            if "folder_context" in confidence:
                answer = "The folder path provides location context for these photos; EXIF GPS was not used to verify the place."
            elif "coordinates_recorded" in confidence:
                answer = "Some visual matches include EXIF GPS coordinates, but Yaad has not verified that those coordinates correspond to the requested place."
            else:
                answer = "These are visual matches only. Location is unconfirmed; mountain scenery cannot verify a place name."
        else:
            answer = f"Found {len(photos)} matching indexed photo(s) in the selected scope."
        evidence = [{"id": p["evidence_id"], "file": p["file"], "locator": {"type": "image"}, "quote": p["quote"],
                     "preview_url": p["preview_url"], "sensitive": is_sensitive(p["quote"] or "", p["file"]),
                     "historical": False, "record_ids": []} for p in photos if p["evidence_id"]]
        photo_disclosure = _photo_disclosure(db, photos)
        return {"status": "answered", "answer": answer, "answer_sensitive": is_sensitive(answer, photos[0]["file"]),
                "values": [], "evidence": evidence,
                "candidates": [{"file": p["file"], "partial": False} for p in photos], "disclosure": photo_disclosure,
                "photos": photos, "memory": dict(NO_MEMORY)}
    photo_gallery = search_photos(db, question, folder_id) if photo_intent(question) else []

    def _with_gallery(result: dict) -> dict:
        if photo_gallery:
            result["photos"] = photo_gallery
        return result

    audio_intent = any(term in question.casefold() for term in ("audio", "voice note", "voice memo", "recording", "transcript", "voice recording"))
    retrieval_start = time.perf_counter()
    if audio_intent:
        with db.connect() as con:
            sql = "SELECT path,error,status FROM files WHERE kind='audio'"
            args = []
            if folder_id is not None:
                sql += " AND folder_id=?"
                args.append(folder_id)
            audio_failures = con.execute(sql, args).fetchall()
        if not audio_failures:
            return {"status": "not_found", "answer": "No audio files are indexed in the selected scope. Choose another folder or add the folder containing the recording.",
                    "values": [], "evidence": [], "candidates": [], "disclosure": [],
                    "memory": dict(NO_MEMORY)}
        rows = search(db, question, folder_id, 15, file_kinds=["audio"])
        if not rows:
            with db.connect() as con:
                rows = con.execute("""SELECT u.*,f.path,f.kind AS file_kind,f.partial,fo.cloud_consent AS folder_cloud_consent
                                     FROM units u JOIN files f ON f.id=u.file_id JOIN folders fo ON fo.id=f.folder_id
                                     WHERE f.kind='audio' AND (? IS NULL OR f.folder_id=?) ORDER BY f.path,u.id LIMIT 15""",
                                   (folder_id, folder_id)).fetchall()
        if rows and _is_cross_file_question(question):
            rows = _with_corroborating_rows(db, question, folder_id, rows)
        if not rows:
            state = next((row for row in audio_failures if row["status"] in ("partial", "failed")), audio_failures[0])
            detail = str(state["error"] or "No transcript text was indexed.")
            if "no speech turns" in detail.casefold():
                answer = f"No speech transcript was found in {Path(state['path']).name}; Meta returned no speech turns."
            elif "consent" in detail.casefold():
                answer = f"{Path(state['path']).name} has not been transcribed because folder cloud consent is off. Allow cloud processing for that folder, then reindex it."
            else:
                answer = f"No transcript is available for {Path(state['path']).name}. Indexing reported: {detail} Reindex the folder to retry."
            return {"status": "unverified", "answer": answer, "values": [], "evidence": [],
                    "candidates": [{"file": row["path"], "partial": row["status"] != "indexed"} for row in audio_failures], "disclosure": [],
                    "memory": dict(NO_MEMORY)}
    else:
        from yaad.llm import is_local
        from yaad.query.hybrid import hybrid_search
        rows = hybrid_search(db, question, folder_id, channels=channels,
                             local=is_local(settings))
    _stage(timings, "retrieval_ms", retrieval_start)
    if timings is not None:
        timings["retrieved_rows"] = len(rows)
    memory_start = time.perf_counter()
    from yaad.llm import is_local
    memory = plan_memory(db, question, folder_id, rows, local=is_local(settings))
    _stage(timings, "memory_ms", memory_start)
    if timings is not None:
        timings["memory_records"] = len(memory["record_ids"])
    memory_meta = {"used": bool(memory["claims"] or memory["context_claims"]
                                or memory["conflicts"] or memory.get("transitions")),
                   "record_ids": memory["record_ids"], **contract_claims(memory)}
    if memory.get("changes_mode"):
        return _with_gallery(changes_listing_response(db, folder_id, memory, memory_meta))
    if memory.get("status_mode"):
        asked = question.casefold()
        if memory["claims"] or "memor" in asked or "record" in asked:
            return _with_gallery(status_listing_response(db, folder_id, memory, memory_meta))
        # No status records and no explicit memory scope: the units pipeline
        # runs exactly as before status queries existed.
        memory = {"claims": [], "context_claims": [], "conflicts": [], "support_rows": [],
                  "record_ids": [], "conflict_validations": []}
        memory_meta = {"used": False, "record_ids": [], "claims": [], "context_claims": [],
                       "conflicts": [], "conflict_validations": []}
    if not memory["conflicts"]:
        historical = historical_response(db, question, folder_id,
                                         require_consent=not is_local(settings))
        if historical is not None:
            historical["memory"] = memory_meta
            return _with_gallery(historical)
    if not rows:
        if memory_meta["used"]:
            full = fetch_support_rows(db, folder_id, _memory_wanted(memory))
            synth = synthesize(question, memory, full)
            if synth is not None:
                synth["memory"] = memory_meta
                return _with_gallery(synth)
        if photo_gallery and not memory_meta["used"]:
            # Photo meta-word fallback: the gallery carries the folder's
            # indexed images with descriptions; answer from those alone.
            # Memory stays out (unused above), so unrelated records can
            # never enter this response.
            descriptions = "; ".join(
                (photo.get("quote") or (photo.get("description") or {}).get("caption") or photo["filename"])
                for photo in photo_gallery[:3])
            answer = (f"Indexed photo(s) in the selected scope show: {descriptions}"
                      if descriptions else "Indexed photo(s) found in the selected scope.")
            if len(answer) > 1200:
                answer = answer[:1200].rsplit(" ", 1)[0] + "…"
            evidence = [{"id": p["evidence_id"], "file": p["file"], "locator": {"type": "image"}, "quote": p["quote"],
                         "preview_url": p["preview_url"], "sensitive": is_sensitive(p["quote"] or "", p["file"]),
                         "historical": False, "record_ids": []} for p in photo_gallery if p["evidence_id"]]
            return {"status": "answered", "answer": answer,
                    "answer_sensitive": is_sensitive(answer, photo_gallery[0]["file"]),
                    "values": [], "evidence": evidence,
                    "candidates": [{"file": p["file"], "partial": False} for p in photo_gallery],
                    "disclosure": _photo_disclosure(db, photo_gallery),
                    "photos": photo_gallery, "memory": memory_meta}
        return _with_gallery({"status": "not_found", "answer": "No matching evidence was found in the selected indexed scope.", "values": [], "evidence": [], "candidates": [], "disclosure": [],
                "memory": memory_meta})
    have = {row["id"] for row in rows}
    injected = [row for row in memory["support_rows"] if row["id"] not in have]
    best = (injected + list(rows[:8]))[:12]
    tainted = _superseded_units(db, folder_id)
    file_groups = {}
    for row in best:
        file_groups.setdefault(row["file_id"], []).append(row)
    field, extracted = [], []
    for row in best:
        if row["evidence_id"] in tainted:
            continue
        row_field, vals = extract_field_value(question, row["text"])
        for value in vals:
            extracted.append((row_field, value, row))
    extracted.extend(memory_pseudo_values(question, memory, {row["evidence_id"]: row for row in best}))
    distinct = {v.lower() for _, v, _ in extracted}
    conflict = len(distinct) > 1 and len({row["file_id"] for _, _, row in extracted}) > 1
    status = "conflict" if conflict else ("ambiguous" if (len(file_groups) > 1 and len(best) > 1) or len(distinct) > 1 else "answered")
    if status == "conflict":
        answer = "I found conflicting values in different sources. Review each cited source."
    elif status == "ambiguous":
        answer = "I found relevant evidence in multiple files. Choose a source to narrow this down."
    else:
        top = best[0]
        if extracted:
            answer = f"{extracted[0][0]}: {extracted[0][1]}"
        else:
            answer = "I found related text, but I could not verify a direct answer. Review the cited source."
        if len(answer) > 1200:
            answer = answer[:1200].rsplit(" ", 1)[0] + "…"
    evidence = []
    attribution = record_ids_by_evidence(memory)
    for row in best[:8]:
        try:
            locator = json.loads(row["locator"])
        except (TypeError, json.JSONDecodeError):
            continue
        quote = row["text"][:600]
        evidence.append({"id": row["evidence_id"], "file": row["path"], "locator": locator, "quote": quote,
                         "preview_url": f"/api/evidence/{row['evidence_id']}/preview",
                         "sensitive": is_sensitive(row["text"], row["path"]),
                         "historical": row["evidence_id"] in tainted,
                         "record_ids": attribution.get(row["evidence_id"], [])})
    values = [{"field": row_field, "value": value, "verification": "verified_text", "evidence_ids": [row["evidence_id"]]}
              for row_field, value, row in extracted]
    file_ids = sorted({r["file_id"] for r in best})
    needs_review = (not extracted) or len(distinct) > 1 or (len(file_groups) > 1 and len(best) > 1)
    semantic = None
    review_egress_id = None
    if needs_review:
        review_rows = [r for r in best if not Path(r["path"]).name.lower().startswith("readme")]
        semantic_start = time.perf_counter()
        semantic = _semantic_review(db, question, (review_rows or best)[:8], settings, tainted)
        _stage(timings, "semantic_ms", semantic_start)
        review_egress_id = semantic.get("egress_id") if semantic else None
        if semantic and semantic.get("status") == "error":
            # A failed review must not wipe the local answer: treat it
            # exactly like no review, keeping the receipt for disclosure.
            semantic = None
        if semantic:
            status, answer = semantic["status"], semantic["answer"]
            ids = set(semantic["evidence_ids"])
            evidence = [item for item in evidence if item["id"] in ids] if ids else []
    if semantic and status == "unverified" and _explicitly_undecided(question, answer, best, set(semantic["evidence_ids"])):
        status = "answered"
    # The unavailability message is skipped only when memory synthesis will
    # actually run (Tier A claims or validated conflicts). Contextual-only
    # matches consult memory without answering, so the honest units message
    # stands exactly as in the memory-less twin.
    if needs_review and semantic is None and (status in {"ambiguous", "conflict"} or not extracted) and not (memory["claims"] or memory["conflicts"]):
        from yaad.llm import is_local
        if is_local(settings):
            status = "local_unavailable"
            answer = (f"I found relevant evidence, but the local model ({settings.local_model}) is "
                      "unavailable. Start Ollama and pull the model, then retry. "
                      "No data was sent to any cloud service.")
            evidence = evidence[:8]
        else:
            status = "cloud_unavailable"
            if not settings.cloud_enabled:
                answer = "I found relevant evidence, but cloud review is disabled in app settings. Review the cited passages or enable cloud processing."
            elif not settings.model_api_key:
                answer = "I found relevant evidence, but MODEL_API_KEY is not configured, so Meta could not review it. Add the key in the app environment and retry."
            elif any(not bool(row["folder_cloud_consent"]) for row in best):
                answer = "I found relevant evidence, but one or more selected folders have cloud consent off. Enable consent for those folders and retry, or review the cited passages."
            elif len(distinct) > 1:
                answer = "I found different values in the selected sources, but Meta review is unavailable. Review the cited passages."
            else:
                answer = "I found relevant evidence, but Meta review is unavailable, so I could not verify a direct answer. Review the cited passages."
            evidence = evidence[:8]
    if status == "answered" and not extracted and not semantic:
        status = "unverified"
    if memory_meta["used"]:
        full = fetch_support_rows(db, folder_id, _memory_wanted(memory))
        if status == "conflict" and _supersession_rescue(db, question, folder_id, memory, best):
            # Validated supersession resolves this conflict: the only
            # current claim for the slot wins over superseded rivals.
            synth = synthesize(question, memory, full)
            if synth is not None and synth["status"] == "answered":
                status, answer, values, evidence = synth["status"], synth["answer"], synth["values"], synth["evidence"]
                candidates_override = synth["candidates"]
            else:
                candidates_override = None
        elif status in {"answered", "partial"} and not memory["conflicts"] \
                and memory["claims"] and all(c["unresolved"] for c in memory["claims"]):
            # Review-path parity with synthesis: unresolved-only
            # memory must not stay settled as `answered`.
            synth = synthesize(question, memory, full)
            if synth is not None and synth["status"] == "unverified":
                status, answer, values, evidence = synth["status"], synth["answer"], synth["values"], synth["evidence"]
                candidates_override = synth["candidates"]
            else:
                candidates_override = None
        elif status in {"answered", "partial"} and not memory["conflicts"] \
                and unvalidated_amount_rivalry(memory["claims"]):
            # Competing single amounts with no validated relationship
            # must not stay settled: qualify, never pick a side.
            synth = synthesize(question, memory, full)
            if synth is not None and synth["status"] == "unverified":
                status, answer, values, evidence = synth["status"], synth["answer"], synth["values"], synth["evidence"]
                candidates_override = synth["candidates"]
            else:
                candidates_override = None
        elif memory["conflicts"] and status in {"answered", "partial"}:
            # A matched conflict pair is never served as a settled fact, not
            # even inside a partial response that settled other parts.
            synth = synthesize(question, {"claims": [], "conflicts": memory["conflicts"]}, full)
            if synth is not None:
                status, answer, values, evidence = synth["status"], synth["answer"], synth["values"], synth["evidence"]
                candidates_override = synth["candidates"]
            else:
                candidates_override = None
        elif (memory["claims"] or memory["conflicts"]) and status in {"ambiguous", "unverified", "cloud_unavailable"}:
            synth = synthesize(question, memory, full)
            if synth is not None:
                status, answer, values, evidence = synth["status"], synth["answer"], synth["values"], synth["evidence"]
                candidates_override = synth["candidates"]
            else:
                candidates_override = None
        else:
            candidates_override = None
    else:
        candidates_override = None
    answer_sensitive = is_sensitive(answer, best[0]["path"])
    disclosure = []
    if file_ids:
        marks = ",".join("?" for _ in file_ids)
        semantic_clause = " OR e.id=?" if review_egress_id else ""
        args = list(file_ids)
        if semantic_clause:
            args.append(review_egress_id)
        with db.connect() as con:
            calls = con.execute(f"SELECT e.*,f.path FROM egress_log e LEFT JOIN files f ON f.id=e.file_id WHERE e.file_id IN ({marks}){semantic_clause} ORDER BY e.ts", args).fetchall()
        disclosure = [{"what": f"{call['content_kind']}, {call['path'] or 'retrieved excerpts'}", "why": call["purpose"],
                       "to": call["provider"], "model": call["model"], "when": call["ts"]} for call in calls]
    evidence = _dedupe_evidence(evidence, values)
    used_ids = {item["id"] for item in evidence}
    if semantic and semantic["status"] == "answered":
        values = [value for value in values if any(eid in used_ids for eid in value["evidence_ids"])]
    if candidates_override is not None:
        candidates = [item for item in candidates_override if item["file"] in {card["file"] for card in evidence}]
    else:
        candidates = [{"file": r["path"], "partial": bool(r["partial"])} for r in best if r["evidence_id"] in used_ids]
    return _with_gallery({"status": status, "answer": answer, "answer_sensitive": answer_sensitive, "values": values[:10], "evidence": evidence,
            "candidates": candidates, "disclosure": disclosure, "memory": memory_meta})

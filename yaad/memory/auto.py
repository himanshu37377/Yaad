"""Automatic memory maintenance: learn without being asked.

Two triggers, both best-effort and failure-isolated:

- After indexing: when a folder's eligible evidence changed since the
  last successful automatic pass (watermark in the cache table), run the
  normal extraction pipeline in a background thread. Cloud runs still
  require the global switch, an API key, and live folder consent;
  local runs send nothing anywhere. Nothing here ever raises into the
  indexing or answering path.
- During conversation: a user *statement* (never a question) that
  carries a new amount/date for a matter Yaad already remembers is
  validated and stored as a conversation-sourced record, reusing the
  exact upsert/conflict/supersession machinery as document extraction.
  Explicit replacement language attempts a validated supersession;
  anything else becomes a preserved conflict. No model call is needed
  for this path. Statement-like messages with a value but no known
  rival fall back to a scoped extraction over just the new statement.
"""

import hashlib
import json
import re
import threading
import time
from datetime import datetime, timezone
from uuid import uuid4

from yaad.evidence.ids import evidence_id
from yaad.memory.extract import run_extraction
from yaad.memory.lifecycle import (
    CORRECTION,
    PREFERENCE,
    SUPERSESSION,
    TEMPORARY,
    UNCERTAIN,
    classify_update,
)
from yaad.memory.records import (
    AMOUNT_PATTERN,
    apply_supersession_links,
    content_words,
    generate_confirmed_quote_candidates,
    mark_conflicts,
    mark_stale,
    normalize_amount,
    upsert_records,
    validate_candidate,
)
from yaad.query.answer import DATE_PATTERNS, _parse_date_value

CONVERSATION_SCHEME = "yaad-conversation://"
_WATERMARK_PREFIX = "auto-memory:"

_STATEMENT_MARKERS = ("decid", "agree", "confirm", "approv", "final",
                      "deadline", "due", "scheduled", "booked", " will ",
                      " shall ", "budget", "fee", "cost", "price")
_QUESTION_STARTERS = frozenset({
    "what", "when", "where", "who", "whom", "whose", "why", "how", "is",
    "are", "was", "were", "do", "does", "did", "can", "could", "should",
    "would", "show", "list", "tell", "give", "find", "search",
})

_lock = threading.Lock()
_folder_guards: set[int] = set()
_learn_inflight = False
_threads: list[threading.Thread] = []


def begin_folder_extraction(folder_id: int) -> bool:
    """Claim the per-folder extraction slot shared by background
    auto-learning, background conversation learning, and the manual
    extract endpoint: exactly one extraction runs per folder at a
    time, whoever started it. (The endpoint's old lock and the
    scheduler's in-flight set could not see each other, so a manual
    run and a background run could overlap -- double model spend and
    competing sweeps over the same records.)"""
    with _lock:
        if folder_id in _folder_guards:
            return False
        _folder_guards.add(folder_id)
        return True


def end_folder_extraction(folder_id: int) -> None:
    with _lock:
        _folder_guards.discard(folder_id)


def _prune_threads() -> None:
    with _lock:
        _threads[:] = [thread for thread in _threads if thread.is_alive()]


def is_conversation_path(path: str) -> bool:
    return str(path or "").startswith(CONVERSATION_SCHEME)


def _auto_enabled(settings) -> bool:
    return bool(getattr(settings, "auto_memory", True))


def memory_ready(settings) -> bool:
    """True when an extraction client may legitimately run at all."""
    from yaad.llm import is_local
    if is_local(settings):
        return True
    return bool(settings.cloud_enabled and settings.model_api_key)


def _eligible_watermark(db, folder_id: int, require_consent: bool) -> tuple[int, int]:
    clause = ("WHERE f.folder_id=? AND fo.cloud_consent=1" if require_consent
              else "WHERE f.folder_id=?")
    with db.connect() as con:
        rows = con.execute(
            f"""SELECT u.id FROM units u JOIN files f ON f.id=u.file_id
                JOIN folders fo ON fo.id=f.folder_id {clause}""",
            (folder_id,)).fetchall()
    return len(rows), max((row["id"] for row in rows), default=0)


def _stored_watermark(db, folder_id: int) -> tuple[int, int] | None:
    with db.connect() as con:
        row = con.execute("SELECT response FROM cache WHERE key=?",
                          (f"{_WATERMARK_PREFIX}{folder_id}",)).fetchone()
    if row is None:
        return None
    try:
        data = json.loads(row["response"])
        return int(data["count"]), int(data["max_id"])
    except (TypeError, ValueError, KeyError):
        return None


def _store_watermark(db, folder_id: int, watermark: tuple[int, int]) -> None:
    with db.transaction() as con:
        con.execute("INSERT OR REPLACE INTO cache(key,response) VALUES(?,?)",
                    (f"{_WATERMARK_PREFIX}{folder_id}",
                     json.dumps({"count": watermark[0], "max_id": watermark[1]})))


def auto_extract_folder(db, settings, folder_id: int, client=None) -> dict | None:
    """One automatic extraction pass over a folder's changed evidence.

    Returns a small summary, an error note, or None when there is
    legitimately nothing to do (feature off, no model gate, no eligible
    units, or evidence unchanged since the last successful pass).
    Never raises: automatic learning must not break indexing."""
    if not _auto_enabled(settings) or not memory_ready(settings):
        return None
    from yaad.llm import get_client, is_local
    local = is_local(settings)
    watermark = _eligible_watermark(db, folder_id, require_consent=not local)
    if watermark[0] == 0 or _stored_watermark(db, folder_id) == watermark:
        return None
    try:
        client = client or get_client(settings)
        summary = run_extraction(db, folder_id, f"auto:{uuid4().hex[:8]}",
                                 settings, client)
    except Exception as exc:  # failure isolation: indexing already succeeded
        return {"folder_id": folder_id, "error": type(exc).__name__}
    _store_watermark(db, folder_id, watermark)
    return {"folder_id": folder_id, "accepted": summary["accepted"],
            "returned": summary["returned"],
            "conflicts": len(summary["conflicts"]),
            "superseded": len(summary["superseded"]),
            "spend_usd": summary["spend_usd"]}


def _run_auto(db, settings, folder_id: int, client) -> None:
    try:
        auto_extract_folder(db, settings, folder_id, client=client)
    except Exception:
        pass
    finally:
        end_folder_extraction(folder_id)


def schedule_auto_memory(db, settings, folder_ids, client=None) -> list[threading.Thread]:
    """Start background extraction for folders not already in flight."""
    if not _auto_enabled(settings):
        return []
    _prune_threads()
    started = []
    for folder_id in folder_ids:
        if not begin_folder_extraction(folder_id):
            continue
        thread = threading.Thread(target=_run_auto, args=(db, settings, folder_id, client),
                                  daemon=True, name=f"yaad-auto-memory-{folder_id}")
        _threads.append(thread)
        thread.start()
        started.append(thread)
    return started


def wait_auto_memory(timeout: float | None = None) -> None:
    """Join outstanding automatic-memory threads (CLI/test convenience)."""
    for thread in list(_threads):
        thread.join(timeout=timeout)
    _prune_threads()


# ---------------------------------------------------------------------------
# Conversation learning
# ---------------------------------------------------------------------------

def _is_question(message: str) -> bool:
    text = message.strip()
    if text.endswith("?"):
        return True
    words = re.findall(r"[a-z']+", text.casefold())
    return bool(words) and words[0] in _QUESTION_STARTERS


def _message_amounts(message: str) -> set[str]:
    found = set()
    for match in AMOUNT_PATTERN.finditer(message):
        normalized = normalize_amount(match.group(0))
        if normalized:
            found.add(normalized)
    return found


def _message_dates(message: str) -> tuple[set[str], list[tuple[str, str]]]:
    iso_values, pairs = set(), []
    for pattern in DATE_PATTERNS:
        for raw in pattern.findall(message):
            cleaned = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", str(raw).strip())
            iso = _parse_date_value(cleaned)
            if iso is None and re.fullmatch(r"[A-Za-z]+ \d{1,2}", cleaned):
                iso = _parse_date_value(f"{cleaned} {datetime.now().year}")
            if iso:
                iso_values.add(iso)
                pairs.append((iso, str(raw)))
    return iso_values, pairs


def _record_slots(record: dict) -> tuple[set[str], set[str]]:
    amounts = {record["amount_value"]} if record.get("amount_value") else set()
    dates = {record["date_value"]} if record.get("date_value") else set()
    return amounts, dates


def _find_rivals(db, folder_id: int | None, message: str,
                 amounts: set[str], dates: set[str]) -> list[dict]:
    """Current records the message plausibly updates: matter overlap plus
    a differing amount or date. Corroborations (same values) are not
    rivals, so restating a fact never churns memory."""
    message_words = set(content_words(message))
    sql = """SELECT * FROM memory_records
             WHERE status IN ('decided','open','conflicted')
               AND statement_kind != 'inferred'"""
    args: list = []
    if folder_id is not None:
        sql += " AND folder_id=?"
        args.append(folder_id)
    rivals = []
    with db.connect() as con:
        rows = [dict(row) for row in con.execute(sql, args)]
    for record in rows:
        shared = set(content_words(f"{record['title']} {record['detail']}")) & message_words
        if len(shared) < 2:
            continue
        rec_amounts, rec_dates = _record_slots(record)
        differs = bool((amounts and rec_amounts and not amounts & rec_amounts)
                       or (dates and rec_dates and not dates & rec_dates))
        if differs:
            rivals.append({**record, "_shared": sorted(shared)})
    rivals.sort(key=lambda r: -len(r["_shared"]))
    return rivals


_CONVERSATION_FILE_SHA = hashlib.sha256(b"yaad-conversation").hexdigest()


def _conversation_ref() -> tuple[dict, str]:
    """Locator and evidence_id for one user statement. Computable
    without persisting anything, so a candidate can be validated
    before its evidence exists anywhere."""
    locator = {"type": "conversation",
               "at": datetime.now(timezone.utc).isoformat(timespec="microseconds")}
    return locator, evidence_id(_CONVERSATION_FILE_SHA, locator)


def _insert_conversation_unit(con, folder_id: int, message: str,
                              eid: str, locator: dict) -> None:
    """Persist a user statement as citable evidence inside the caller's
    transaction. Provenance is structural: the file uses the
    conversation scheme and the unit meta names the origin, so every
    downstream consumer can tell user assertions from documents."""
    path = f"{CONVERSATION_SCHEME}folder-{folder_id}"
    row = con.execute("SELECT id FROM files WHERE path=?", (path,)).fetchone()
    if row is None:
        consent = con.execute("SELECT cloud_consent FROM folders WHERE id=?",
                              (folder_id,)).fetchone()
        file_id = con.execute(
            """INSERT INTO files(folder_id,path,sha256,kind,size,mtime,status,cloud_consent)
               VALUES(?,?,?,?,?,?,?,?)""",
            (folder_id, path, _CONVERSATION_FILE_SHA, "text", len(message), time.time(),
             "indexed", int(consent["cloud_consent"]) if consent else 0)).lastrowid
    else:
        file_id = row["id"]
    con.execute(
        "INSERT INTO units(file_id,evidence_id,kind,locator,text,meta) VALUES(?,?,?,?,?,?)",
        (file_id, eid, "text_span", json.dumps(locator), message,
         json.dumps({"origin": "conversation"})))


def _ensure_conversation_unit(db, folder_id: int, message: str) -> str:
    """Store the user's own statement as citable evidence (provenance:
    the conversation itself), returning its evidence_id."""
    locator, eid = _conversation_ref()
    with db.transaction() as con:
        _insert_conversation_unit(con, folder_id, message, eid, locator)
    return eid


def _drop_conversation_unit(db, eid: str) -> None:
    """Remove a conversation unit that validated into nothing. The FTS
    delete trigger keeps the index consistent, so a rejected statement
    can never resurface as retrieved evidence."""
    with db.transaction() as con:
        con.execute("DELETE FROM units WHERE evidence_id=?", (eid,))


def _sweep(db, folder_id: int, source: str,
           supersede_first: bool = False) -> tuple[list, list]:
    """Run the standard lifecycle sweeps. A claimant carrying a validated
    supersedes link must be processed before conflict marking, or being
    marked conflicted first would disqualify it as a superseder."""
    superseded: list = []
    if supersede_first:
        with db.transaction() as con:
            superseded = apply_supersession_links(con, folder_id, source=source)
    with db.transaction() as con:
        conflicts = mark_conflicts(con, folder_id, source=source)
        generate_confirmed_quote_candidates(con, folder_id, conflicts)
        if not supersede_first:
            superseded = apply_supersession_links(con, folder_id, source=source)
        mark_stale(con, folder_id, source=source)
    return conflicts, superseded


def _learn_rival(db, folder_id: int, message: str, rival: dict,
                 amounts: set[str], date_pairs: list[tuple[str, str]]) -> dict:
    """Deterministic path: store the statement against a known rival,
    then let the normal conflict/supersession machinery decide.

    The candidate is validated BEFORE its evidence is persisted: the
    statement's evidence ref is computed in memory, validation runs
    against it, and only a valid candidate's unit is inserted --
    atomically with the record itself. A rejected statement therefore
    leaves nothing behind, in particular nothing the retriever could
    later surface as evidence."""
    locator, conv_eid = _conversation_ref()
    with db.connect() as con:
        support = con.execute(
            """SELECT e.evidence_id,e.excerpt,u.text AS unit_text
               FROM memory_record_evidence e LEFT JOIN units u ON u.evidence_id=e.evidence_id
               WHERE e.record_id=? AND e.role='supports' LIMIT 1""", (rival["id"],)).fetchone()
    unit_texts = {conv_eid: message}
    evidence = [{"evidence_id": conv_eid, "role": "supports", "excerpt": message}]
    # Rivals differ by construction (_find_rivals excludes corroborations),
    # so same_values is always False here; the taxonomy decides whether the
    # statement replaces, corrects, hedges, or merely contradicts.
    decision = classify_update(message, rival, same_values=False)
    replaces = decision.kind in (SUPERSESSION, CORRECTION)
    if support is not None and support["unit_text"]:
        # Cite the rival's source truthfully: the statement contradicts
        # it (and replaces it, when the user says so). This also puts the
        # rival's wording in the validation pool, so the inherited
        # key-phrase signature below can validate.
        unit_texts[support["evidence_id"]] = support["unit_text"]
        evidence.append({"evidence_id": support["evidence_id"], "role": "contradicts",
                         "excerpt": support["excerpt"]})
        if replaces:
            evidence.append({"evidence_id": support["evidence_id"], "role": "supersedes",
                             "excerpt": support["excerpt"]})
    # Inherit the rival's key-phrase signature: conflict validation only
    # recognises rivalry between records sharing a phrase signature.
    key_parts = str(rival.get("record_key") or "").split("|")
    signature_words = (key_parts[4].split("+")
                       if len(key_parts) == 6 and key_parts[4] != "nosig" else [])
    if len(signature_words) < 2:
        signature_words = rival["_shared"][:3]
    hedged = decision.kind in (UNCERTAIN, TEMPORARY, PREFERENCE)
    record_type = rival["record_type"]
    iso, raw = date_pairs[0] if date_pairs else (None, None)
    if record_type == "deadline" and not iso:
        record_type = "decision"
    if hedged:
        kind = "proposal"
        status = "open"
        if record_type == "decision":
            record_type = "open_question"
    else:
        kind = "explicit"
        status = "decided" if record_type == "decision" else "open"
    candidate = {
        "record_type": record_type, "title": rival["title"], "detail": message.strip(),
        "date_value": iso, "date_raw": raw, "assignee": rival.get("assignee") or "",
        "status": status, "statement_kind": kind,
        "key_phrase": " ".join(signature_words[:5]),
        "evidence": evidence,
    }
    problems = validate_candidate(candidate, unit_texts)
    if problems:
        return {"learned": False, "action": "rejected", "reasons": problems}
    run_id = f"conversation:{uuid4().hex[:8]}"
    with db.transaction() as con:
        _insert_conversation_unit(con, folder_id, message, conv_eid, locator)
        ids = upsert_records(con, folder_id, run_id, [candidate], unit_texts)
    new_id = next(iter(ids.values()))
    conflicts, superseded = _sweep(db, folder_id, source=f"conversation:{run_id}",
                                   supersede_first=replaces)
    if rival["id"] in superseded:
        action = "superseded"
    elif any(new_id in pair for pair in conflicts):
        action = "conflict"
    else:
        action = "created"
    return {"learned": True, "action": action, "record_id": new_id,
            "rival_id": rival["id"], "folder_id": folder_id,
            "decision": {"kind": decision.kind, "trust": decision.trust,
                         "reason": decision.reason,
                         "rival_record_id": decision.rival_record_id}}


def _learn_scoped_extraction(db, settings, folder_id: int, message: str,
                             client) -> dict:
    """Fallback for durable statements about new matters: extract from
    just the user's statement, reusing the full validated pipeline."""
    conv_eid = _ensure_conversation_unit(db, folder_id, message)
    run_id = f"conversation:{uuid4().hex[:8]}"
    try:
        summary = run_extraction(db, folder_id, run_id, settings, client,
                                 only_evidence_ids={conv_eid})
    except Exception as exc:
        # Extraction failed after the unit was persisted for selection:
        # nothing learned from it, so it must not stay retrievable.
        _drop_conversation_unit(db, conv_eid)
        return {"learned": False, "action": "error", "error": type(exc).__name__}
    if not summary["accepted"]:
        # Every candidate was rejected in validation: the statement
        # stays the user's chat message, never indexed evidence.
        _drop_conversation_unit(db, conv_eid)
        return {"learned": False, "action": "ignored"}
    return {"learned": True, "action": "extracted", "folder_id": folder_id,
            "accepted": summary["accepted"]}


def learn_from_message(db, settings, folder_id: int | None, message: str,
                       client=None) -> dict:
    """Best-effort automatic update from one user statement.

    Questions never teach. Statements carrying a new amount/date for a
    known matter are stored deterministically; other durable statements
    may go through scoped extraction when a model gate allows. Never
    raises and never sends data anywhere the gates forbid."""
    result = {"learned": False, "action": "ignored"}
    try:
        text = (message or "").strip()
        if len(text) < 8 or _is_question(text):
            return result
        amounts = _message_amounts(text)
        dates, date_pairs = _message_dates(text)
        if not amounts and not dates:
            return result
        rivals = _find_rivals(db, folder_id, text, amounts, dates)
        if rivals:
            rival = rivals[0]
            return _learn_rival(db, rival["folder_id"], text, rival,
                                amounts, date_pairs)
        lowered = text.casefold()
        if (any(marker in lowered for marker in _STATEMENT_MARKERS)
                and memory_ready(settings)):
            from yaad.llm import get_client
            client = client or get_client(settings)
            targets = [folder_id] if folder_id is not None else [
                row["id"] for row in _folder_ids(db)]
            for target in targets:
                outcome = _learn_scoped_extraction(db, settings, target, text, client)
                if outcome.get("learned"):
                    return outcome
            return {"learned": False, "action": "ignored"}
        return result
    except Exception as exc:  # learning must never break answering
        return {"learned": False, "action": "error", "error": type(exc).__name__}


def _folder_ids(db) -> list[dict]:
    with db.connect() as con:
        return [dict(row) for row in con.execute("SELECT id FROM folders ORDER BY id")]


def _run_learn(db, settings, folder_id, message, client) -> None:
    global _learn_inflight
    # Conversation learning can run a scoped extraction; it takes the
    # same per-folder slot as every other extraction path. Background
    # learning is best-effort, so contention skips quietly.
    claimed = folder_id is not None and begin_folder_extraction(folder_id)
    if folder_id is not None and not claimed:
        with _lock:
            _learn_inflight = False
        return
    try:
        learn_from_message(db, settings, folder_id, message, client=client)
    except Exception:
        pass
    finally:
        if claimed:
            end_folder_extraction(folder_id)
        with _lock:
            _learn_inflight = False


def schedule_conversation_learning(db, settings, folder_id: int | None,
                                   message: str, client=None) -> threading.Thread | None:
    """Background-learn from a user statement; at most one at a time."""
    global _learn_inflight
    if not _auto_enabled(settings):
        return None
    with _lock:
        if _learn_inflight:
            return None
        _learn_inflight = True
    thread = threading.Thread(target=_run_learn, args=(db, settings, folder_id, message, client),
                              daemon=True, name="yaad-conversation-learning")
    _threads.append(thread)
    thread.start()
    return thread

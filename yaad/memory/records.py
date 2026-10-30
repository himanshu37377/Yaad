"""Deterministic record layer for evidence-backed decision memory (CP1).

Identity (`record_key`) is computed and stable across runs; evidence
aggregation is a validated union that never destroys provenance. Records
merge only on identical keys; near-duplicates stay separate and are
reported as review hints instead.
"""

import datetime
import re
import unicodedata

from yaad.memory.ledger import (
    MATERIAL_FIELDS,
    log_transition,
    record_state,
    supports_snapshot,
)
from yaad.memory.relationships import (
    DIMENSION_LABELS,
    SUPPORTED,
    statement_dimensions,
    statement_words,
    subject_markers,
    validate_relationship,
)
from yaad.query.retrieve import STOPWORDS

MEMORY_SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_records(
  id INTEGER PRIMARY KEY,
  folder_id INTEGER NOT NULL REFERENCES folders(id) ON DELETE CASCADE,
  record_type TEXT NOT NULL CHECK(record_type IN ('decision','deadline','commitment','open_question')),
  title TEXT NOT NULL,
  detail TEXT NOT NULL DEFAULT '',
  date_value TEXT,
  date_raw TEXT,
  amount_value TEXT,
  amount_raw TEXT,
  assignee TEXT,
  status TEXT NOT NULL DEFAULT 'open'
    CHECK(status IN ('decided','open','superseded','conflicted','stale')),
  statement_kind TEXT NOT NULL DEFAULT 'explicit'
    CHECK(statement_kind IN ('explicit','proposal','inferred','unresolved')),
  record_key TEXT NOT NULL,
  extraction_run TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(folder_id, record_key)
);
CREATE TABLE IF NOT EXISTS memory_record_evidence(
  record_id INTEGER NOT NULL REFERENCES memory_records(id) ON DELETE CASCADE,
  evidence_id TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'supports'
    CHECK(role IN ('supports','contradicts','supersedes')),
  excerpt TEXT NOT NULL,
  PRIMARY KEY(record_id, evidence_id, role)
);
CREATE TABLE IF NOT EXISTS record_flags(
  id INTEGER PRIMARY KEY,
  record_id INTEGER NOT NULL REFERENCES memory_records(id) ON DELETE CASCADE,
  reason TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""

RECORD_TYPES = frozenset({"decision", "deadline", "commitment", "open_question"})
STATUSES = frozenset({"decided", "open", "superseded", "conflicted", "stale"})
INTAKE_STATUSES = frozenset({"decided", "open"})
KINDS = frozenset({"explicit", "proposal", "inferred", "unresolved"})
ROLES = frozenset({"supports", "contradicts", "supersedes"})
# Statuses a record returns to when the condition that overrode it clears.
RESTORED_STATUS = {"decision": "decided", "deadline": "open",
                   "commitment": "open", "open_question": "open"}

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

AMOUNT_PATTERN = re.compile(
    r"(?:INR|USD|EUR|\$|₹|Rs\.?|rupees?)\s*\d[\d,]*(?:\.\d{1,2})?"
    r"|\d[\d,]*(?:\.\d{1,2})?\s*(?:INR|USD|EUR|\$|₹|Rs\.?|rupees?)", re.I)

_CURRENCY_ALIASES = (("₹", "INR"), ("$", "USD"), ("€", "EUR"),
                     ("USD", "USD"), ("EUR", "EUR"), ("INR", "INR"),
                     ("Rs", "INR"), ("Rs.", "INR"), ("rupees", "INR"), ("rupee", "INR"))


def normalize_amount(raw: str) -> str | None:
    """Canonical money value ('INR150') or None when unparseable.

    Same currency aliases and comma/decimal spellings collapse; different
    currencies never do."""
    if not raw or not raw.strip():
        return None
    text = raw.strip()
    currency = None
    for token, code in _CURRENCY_ALIASES:
        if re.search(rf"(?i)(?<![A-Za-z]){re.escape(token)}(?![A-Za-z])", text):
            currency = code
            break
    number = re.search(r"\d[\d,]*(\.\d+)?", text)
    if currency is None or number is None:
        return None
    value = number.group(0).replace(",", "")
    if "." in value:
        value = value.rstrip("0").rstrip(".")
    return f"{currency}{value}"


def amount_class_of(excerpts: list[str]) -> tuple[str, str | None]:
    """Identity class from supporting excerpts: the single normalized amount,
    'multi' when several distinct amounts appear, 'noamount' when none.

    Computed deterministically from cited text, never model-supplied, so a
    rival value cannot be invented or hidden. Multi-amount pools stay
    deliberately vague: mentioning a total alongside a line item is not a
    rival claim."""
    found: dict[str, str] = {}
    for excerpt in excerpts:
        for match in AMOUNT_PATTERN.finditer(excerpt or ""):
            normalized = normalize_amount(match.group(0))
            if normalized is not None and normalized not in found:
                found[normalized] = match.group(0).strip()
    if len(found) == 1:
        only = next(iter(found))
        return only, found[only]
    if len(found) > 1:
        return "multi", None
    return "noamount", None


def normalize_text(value: str) -> str:
    """Mirror of the indexer's excerpt comparison (worker._normalized_source_text)."""
    return re.sub(r"\W+", "", unicodedata.normalize("NFKC", value or "").casefold(),
                  flags=re.UNICODE)


def content_words(text: str) -> list[str]:
    return sorted({word for word in re.findall(r"[\w-]+", (text or "").casefold())
                   if len(word) > 1 and word not in STOPWORDS})


def kind_group_of(statement_kind: str) -> str:
    """Merge groups: asserted claims merge together; proposals and unresolved
    matters never merge with asserted claims (different epistemic states)."""
    if statement_kind in ("explicit", "inferred"):
        return "asserted"
    if statement_kind in ("proposal", "unresolved"):
        return statement_kind
    raise ValueError(f"unknown statement_kind: {statement_kind!r}")


def compute_record_key(record_type: str, statement_kind: str, date_value: str | None,
                       assignee: str | None, key_phrase: str,
                       amount_key: str = "noamount") -> str:
    date_part = date_value if date_value else "nodate"
    who = (assignee or "").strip().casefold() or "noassignee"
    signature = "+".join(content_words(key_phrase)) or "nosig"
    amount_part = amount_key if amount_key else "noamount"
    return "|".join((record_type, kind_group_of(statement_kind), date_part, who, signature,
                     amount_part))


def conflict_key_of(record_type: str, assignee: str | None, key_phrase: str) -> str:
    """Identity without the date: same conflict key plus different explicit
    dates is a contradiction, never an automatic merge or supersession."""
    who = (assignee or "").strip().casefold() or "noassignee"
    signature = "+".join(content_words(key_phrase)) or "nosig"
    return "|".join((record_type, who, signature))


def _valid_iso_date(value: str | None) -> bool:
    if value is None:
        return True
    if not _ISO_DATE.match(value):
        return False
    try:
        datetime.date.fromisoformat(value)
    except ValueError:
        return False
    return True


def validate_candidate(candidate: dict, unit_text_by_evidence_id: dict[str, str]) -> list[str]:
    """Check one extraction candidate. Returns error strings; [] means valid."""
    errors = []
    rtype = candidate.get("record_type")
    status = candidate.get("status")
    kind = candidate.get("statement_kind")
    if rtype not in RECORD_TYPES:
        errors.append(f"bad record_type: {rtype!r}")
    if status not in INTAKE_STATUSES:
        errors.append(f"bad intake status: {status!r} (system statuses are set by sweeps, not extraction)")
    if kind not in KINDS:
        errors.append(f"bad statement_kind: {kind!r}")
    if not str(candidate.get("title") or "").strip():
        errors.append("empty title")
    if not str(candidate.get("detail") or "").strip():
        errors.append("empty detail: statement text is required")
    phrase = str(candidate.get("key_phrase") or "")
    if not content_words(phrase):
        errors.append("key_phrase has no content words")
    if rtype == "decision" and status == "open":
        errors.append("decision cannot be open")
    if rtype == "open_question" and status == "decided":
        errors.append("open_question cannot be decided (a decision supersedes it instead)")
    if kind == "proposal" and status == "decided":
        errors.append("proposal cannot be decided")
    if kind == "unresolved" and status == "decided":
        errors.append("unresolved cannot be decided")
    if kind == "inferred" and (candidate.get("assignee") or "").strip():
        errors.append("inferred records must not carry an assignee")
    if not _valid_iso_date(candidate.get("date_value")):
        errors.append(f"bad date_value: {candidate.get('date_value')!r}")
    if rtype == "deadline" and not candidate.get("date_value"):
        errors.append("deadline requires date_value")
    evidence = candidate.get("evidence") or []
    if not any(item.get("role", "supports") == "supports" for item in evidence):
        errors.append("record needs at least one supports row")
    pool = ""
    for item in evidence:
        eid = item.get("evidence_id")
        if item.get("role", "supports") not in ROLES:
            errors.append(f"bad role: {item.get('role')!r}")
        if eid not in unit_text_by_evidence_id:
            errors.append(f"unknown evidence_id: {eid!r}")
            continue
        excerpt = str(item.get("excerpt") or "")
        if not excerpt.strip() or normalize_text(excerpt) not in normalize_text(unit_text_by_evidence_id[eid]):
            errors.append(f"excerpt not contained for {eid!r}")
        pool += " " + excerpt
    pool_norm = normalize_text(pool)
    for word in content_words(phrase):
        if normalize_text(word) not in pool_norm:
            errors.append(f"key_phrase word not in excerpts: {word!r}")
    date_raw = str(candidate.get("date_raw") or "")
    if date_raw and normalize_text(date_raw) not in pool_norm:
        errors.append("date_raw not in excerpts")
    return errors


def upsert_records(con, folder_id: int, run_id: str, candidates: list[dict],
                   unit_texts: dict[str, str], pruned: list | None = None) -> dict[str, int]:
    """Validate then insert-or-refresh candidates keyed by record_key.

    Only identical record_keys merge; near-duplicates stay separate (see
    find_duplicate_candidates for review hints). Evidence is unioned across
    runs: new validated rows are added, surviving old rows are kept, and rows
    that no longer revalidate are dropped into `pruned` for the caller to
    report. Status is refreshed only when restoring a stale record; conflict
    sweeps own all other status changes so re-extraction cannot silently
    clear them.
    """
    grouped: dict[str, dict] = {}
    for candidate in candidates:
        problems = validate_candidate(candidate, unit_texts)
        if problems:
            raise ValueError(f"invalid candidate {candidate.get('title')!r}: {'; '.join(problems)}")
        amount_key, amount_raw = amount_class_of(
            [str(item.get("excerpt") or "") for item in candidate["evidence"]
             if item.get("role", "supports") == "supports"])
        key = compute_record_key(candidate["record_type"], candidate["statement_kind"],
                                 candidate.get("date_value"), candidate.get("assignee"),
                                 candidate["key_phrase"], amount_key)
        candidate = {**candidate, "amount_value": None if amount_key in ("noamount", "multi") else amount_key,
                     "amount_raw": amount_raw}
        own = []
        own_seen = set()
        for item in candidate["evidence"]:
            sig = (item["evidence_id"], item.get("role", "supports"))
            if sig not in own_seen:
                own_seen.add(sig)
                own.append(item)
        if key in grouped:
            seen = {(item["evidence_id"], item.get("role", "supports"))
                    for item in grouped[key]["evidence"]}
            for item in own:
                if (item["evidence_id"], item.get("role", "supports")) not in seen:
                    grouped[key]["evidence"].append(item)
        else:
            grouped[key] = {**candidate, "evidence": list(own)}
    ids = {}
    for key, candidate in grouped.items():
        row = con.execute("SELECT * FROM memory_records WHERE folder_id=? AND record_key=?",
                          (folder_id, key)).fetchone()
        if row is None:
            cursor = con.execute(
                """INSERT INTO memory_records(folder_id,record_type,title,detail,date_value,date_raw,
                   amount_value,amount_raw,assignee,status,statement_kind,record_key,extraction_run)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (folder_id, candidate["record_type"], candidate["title"], candidate.get("detail", ""),
                 candidate.get("date_value"), candidate.get("date_raw"),
                 candidate.get("amount_value"), candidate.get("amount_raw"),
                 candidate.get("assignee") or None,
                 candidate["status"], candidate["statement_kind"], key, run_id))
            record_id = cursor.lastrowid
            created = True
        else:
            record_id = row["id"]
            created = False
            status = candidate["status"] if row["status"] == "stale" else row["status"]
            kind = row["statement_kind"]
            if candidate["statement_kind"] == "explicit":
                kind = "explicit"
            con.execute(
                """UPDATE memory_records SET title=?,detail=?,date_value=?,date_raw=?,
                   amount_value=?,amount_raw=?,assignee=?,
                   status=?,statement_kind=?,extraction_run=? WHERE id=?""",
                (candidate["title"], candidate.get("detail", ""), candidate.get("date_value"),
                 candidate.get("date_raw"), candidate.get("amount_value"), candidate.get("amount_raw"),
                 candidate.get("assignee") or None,
                 status, kind, run_id, record_id))
        # Union, not replace: a later run that cites fewer references must not
        # discard previously validated corroboration. Old rows survive only if
        # they still revalidate against the live units table (any consent
        # state: consent gates extraction input, never rewrites history).
        # Dropped rows are collected for the caller to report, never silent.
        merged = {(item["evidence_id"], item.get("role", "supports")): item["excerpt"]
                  for item in candidate["evidence"]}
        for old in con.execute("SELECT evidence_id,role,excerpt FROM memory_record_evidence WHERE record_id=?",
                               (record_id,)):
            sig = (old["evidence_id"], old["role"])
            if sig in merged:
                continue
            unit = con.execute("SELECT text FROM units WHERE evidence_id=?",
                               (old["evidence_id"],)).fetchone()
            if unit is not None and normalize_text(old["excerpt"]) in normalize_text(unit["text"]):
                merged[sig] = old["excerpt"]
            elif pruned is not None:
                pruned.append({"record_id": record_id, "evidence_id": old["evidence_id"],
                               "role": old["role"],
                               "reason": "evidence_id unresolved" if unit is None
                                         else "excerpt no longer contained"})
        con.execute("DELETE FROM memory_record_evidence WHERE record_id=?", (record_id,))
        con.executemany(
            "INSERT INTO memory_record_evidence(record_id,evidence_id,role,excerpt) VALUES(?,?,?,?)",
            [(record_id, eid, role, excerpt) for (eid, role), excerpt in merged.items()])
        fresh = con.execute("SELECT * FROM memory_records WHERE id=?", (record_id,)).fetchone()
        if created:
            log_transition(con, folder_id=folder_id, record_id=record_id,
                           transition="record_created", prev_state=None,
                           new_state=record_state(fresh),
                           reason="accepted by validated extraction",
                           evidence=supports_snapshot(con, record_id),
                           source=f"extraction:{run_id}")
        else:
            old_state, new_state = record_state(row), record_state(fresh)
            changed = sorted(field for field in MATERIAL_FIELDS
                             if old_state[field] != new_state[field])
            if changed:
                log_transition(con, folder_id=folder_id, record_id=record_id,
                               transition="record_updated", prev_state=old_state,
                               new_state=new_state,
                               reason=f"re-extraction changed {', '.join(changed)}",
                               evidence=supports_snapshot(con, record_id),
                               source=f"extraction:{run_id}")
            if row["status"] == "stale" and fresh["status"] != "stale":
                log_transition(con, folder_id=folder_id, record_id=record_id,
                               transition="restored", prev_state=old_state,
                               new_state=new_state,
                               reason="evidence resolved again; restored to live status",
                               evidence=supports_snapshot(con, record_id),
                               source=f"extraction:{run_id}")
        ids[key] = record_id
    return ids


def backfill_amount_keys(con) -> int:
    """One-time migration: extend pre-amount 5-part keys with the amount
    class computed from stored supporting excerpts. Idempotent (re-runs
    find only 6-part keys); distinct keys stay distinct."""
    columns = {row["name"] for row in con.execute("PRAGMA table_info(memory_records)")}
    if "amount_value" not in columns:
        return 0
    moved = 0
    for row in con.execute("SELECT id,record_key FROM memory_records"):
        parts = str(row["record_key"]).split("|")
        if len(parts) != 5:
            continue
        excerpts = [item["excerpt"] for item in con.execute(
            "SELECT excerpt FROM memory_record_evidence WHERE record_id=? AND role='supports'",
            (row["id"],))]
        token, raw = amount_class_of(excerpts)
        con.execute("UPDATE memory_records SET record_key=?,amount_value=?,amount_raw=? WHERE id=?",
                    (row["record_key"] + "|" + token,
                     None if token in ("noamount", "multi") else token, raw, row["id"]))
        moved += 1
    return moved


def resolving_supports(con, record_id: int) -> int:
    """Supports rows whose unit still exists AND still contains the excerpt.

    Single definition shared by the stale sweep and the serving path, so a
    changed source (same id, new text) stops supporting current answers even
    before any re-extraction touches the record."""
    count = 0
    for row in con.execute(
            """SELECT e.excerpt,u.text FROM memory_record_evidence e
               LEFT JOIN units u ON u.evidence_id=e.evidence_id
               WHERE e.record_id=? AND e.role='supports'""", (record_id,)):
        if row["text"] is not None and normalize_text(row["excerpt"]) in normalize_text(row["text"]):
            count += 1
    return count


def _matter_key(row) -> tuple:
    parts = str(row["record_key"]).split("|")
    return (parts[0], parts[3], parts[4]) if len(parts) in (5, 6) else (None, None, None)


def _textual_rivalry_hint(left, right) -> str | None:
    """Statements-only candidacy for textual rivalry: both statements
    name the same attribute dimension and each carries proper nouns the
    other never mentions. A cheap prefilter; validation decides."""
    shared = statement_dimensions(left.get("title"), left.get("detail")) \
        & statement_dimensions(right.get("title"), right.get("detail"))
    if not shared:
        return None
    left_markers = subject_markers(left.get("title"), left.get("detail"), [])
    right_markers = subject_markers(right.get("title"), right.get("detail"), [])
    left_words = statement_words(left.get("title"), left.get("detail"))
    right_words = statement_words(right.get("title"), right.get("detail"))
    if (left_markers - right_words) and (right_markers - left_words):
        return sorted(shared)[0]
    return None


def _rivals(left, right) -> bool:
    """Same matter asserting different dates, different single amounts,
    or candidate competing textual values.

    Amount rivalry needs exactly one normalized amount on each side: a
    record mentioning several amounts (a line item beside a total) never
    auto-conflicts, and different budget categories differ in matter.
    Textual candidacy still passes full evidence validation before any
    marking, so unrelated records sharing a dimension never conflict."""
    if _matter_key(left) != _matter_key(right):
        return False
    if left["date_value"] and right["date_value"] and left["date_value"] != right["date_value"]:
        return True
    left_amount = left["amount_value"] if "amount_value" in left.keys() else None
    right_amount = right["amount_value"] if "amount_value" in right.keys() else None
    if left_amount and right_amount and left_amount != right_amount:
        return True
    return _textual_rivalry_hint(left, right) is not None


def conflict_partners(con, folder_id: int, record_id: int) -> list[dict]:
    """Explicit current records sharing the matter, asserting a different
    date or single amount, and passing evidence validation: the partners
    a conflicted record must be presented with. Unvalidated rivals are
    not partners, so a stale or false mark can never pull an unrelated
    record into an answer."""
    row = con.execute("SELECT * FROM memory_records WHERE id=? AND folder_id=?",
                      (record_id, folder_id)).fetchone()
    if row is None:
        return []
    row = dict(row)
    partners = []
    for cand in con.execute(
            """SELECT * FROM memory_records WHERE folder_id=? AND id!=?
               AND statement_kind='explicit' AND status IN ('decided','open','conflicted')""",
            (folder_id, record_id)):
        if not _rivals(row, dict(cand)):
            continue
        verdict = validate_relationship(con, "conflict", record_id, cand["id"])
        if verdict["verdict"] == SUPPORTED:
            partners.append(dict(cand))
    return partners


def _rival_detail(left: dict, right: dict) -> str:
    """Deterministic description of what two rivals disagree on (sorted so
    pair direction never changes the wording)."""
    parts = []
    if left.get("date_value") and right.get("date_value") \
            and left["date_value"] != right["date_value"]:
        first, second = sorted((left["date_value"], right["date_value"]))
        parts.append(f"rival dates {first} vs {second}")
    left_amount, right_amount = left.get("amount_value"), right.get("amount_value")
    if left_amount and right_amount and left_amount != right_amount:
        first, second = sorted((left_amount, right_amount))
        parts.append(f"rival amounts {first} vs {second}")
    dimension = _textual_rivalry_hint(left, right)
    if dimension is not None:
        left_markers = subject_markers(left.get("title"), left.get("detail"), [])
        right_markers = subject_markers(right.get("title"), right.get("detail"), [])
        left_words = statement_words(left.get("title"), left.get("detail"))
        right_words = statement_words(right.get("title"), right.get("detail"))
        first = " ".join(sorted(left_markers - right_words))
        second = " ".join(sorted(right_markers - left_words))
        parts.append(f"rival {DIMENSION_LABELS[dimension]} {first} vs {second}")
    return "; ".join(parts) or "rival values"


def mark_conflicts(con, folder_id: int, rejected: list | None = None,
                   source: str | None = None) -> list[tuple[int, int]]:
    """Flag explicit records sharing a conflict key, asserting different
    dates or single amounts, and passing evidence validation. Rival
    values stay separate candidates; this only labels them. Pairs the
    evidence does not support are collected into `rejected` for the
    caller to report and are never marked. Newly marked and newly
    cleared records append one ledger entry each. Idempotent: re-running
    over the same rows changes nothing, and a previously marked pair that
    no longer validates is restored to its lifecycle status."""
    rows = con.execute("""SELECT id,record_type,assignee,statement_kind,date_value,amount_value,
                          status,record_key,title,detail FROM memory_records WHERE folder_id=?""",
                       (folder_id,)).fetchall()

    pairs = []
    current = [dict(r) for r in rows
               if r["statement_kind"] == "explicit" and r["status"] in ("decided", "open", "conflicted")]
    for pos, left in enumerate(current):
        for right in current[pos + 1:]:
            if left["id"] == right["id"] or not _rivals(left, right):
                continue
            verdict = validate_relationship(con, "conflict", left["id"], right["id"])
            if verdict["verdict"] == SUPPORTED:
                pairs.append((left["id"], right["id"]))
            elif rejected is not None:
                rejected.append({"left_id": left["id"], "right_id": right["id"],
                                 "verdict": verdict["verdict"], "reasons": verdict["reasons"]})
    involved = sorted({rid for pair in pairs for rid in pair})
    before = {}
    if involved:
        marks = ",".join("?" for _ in involved)
        before = {row["id"]: dict(row) for row in con.execute(
            f"SELECT * FROM memory_records WHERE id IN ({marks})", involved)}
    for first, second in pairs:
        con.execute("UPDATE memory_records SET status='conflicted' WHERE id IN (?,?)", (first, second))
    for record_id in involved:
        if before[record_id]["status"] == "conflicted":
            continue
        partners = sorted(other for pair in pairs for other in pair if other != record_id)
        primary = before[partners[0]]
        after = {**before[record_id], "status": "conflicted"}
        names = ", ".join(str(pid) for pid in partners)
        log_transition(con, folder_id=folder_id, record_id=record_id,
                       transition="conflict_marked",
                       prev_state=record_state(before[record_id]),
                       new_state=record_state(after),
                       reason=f"validated conflict{'s' if len(partners) > 1 else ''} "
                              f"with record{'s' if len(partners) > 1 else ''} {names} "
                              f"({_rival_detail(before[record_id], primary)})",
                       related_record_id=partners[0],
                       evidence=supports_snapshot(con, record_id) + [
                           item for pid in partners for item in supports_snapshot(con, pid)],
                       source=source)
    # A conflict clears only when no validated partner remains: every
    # partner stopped being an explicit rival, or the evidence no longer
    # supports the pairing.
    for row in con.execute("SELECT id FROM memory_records WHERE folder_id=? AND status='conflicted'", (folder_id,)):
        rivals = [pair for pair in pairs if row["id"] in pair]
        if not rivals:
            full = dict(con.execute("SELECT * FROM memory_records WHERE id=?",
                                      (row["id"],)).fetchone())
            restored = RESTORED_STATUS[full["record_type"]]
            con.execute("UPDATE memory_records SET status=? WHERE id=?", (restored, row["id"]))
            log_transition(con, folder_id=folder_id, record_id=row["id"],
                           transition="conflict_cleared",
                           prev_state=record_state(full),
                           new_state=record_state({**full, "status": restored}),
                           reason="no validated conflict partner remains",
                           evidence=supports_snapshot(con, row["id"]),
                           source=source)
    return pairs


# Text-vs-text relevance floor for the supersession relatedness gate: mirrors
# memory_answer.MATCH_WORDS (kept local because memory_answer imports this
# module). Relevance needs shared wording; one common word is not enough.
SUPERSESSION_EXCERPT_WORDS = 2


def _supersession_related(new_record, target_record, new_excerpts: list[str],
                          target_excerpts: list[str]) -> bool:
    """Conservative same-matter check for a supersession edge.

    Either the records' key-phrase signatures overlap at the duplicate-hint
    floor (the model's own matter distillation, tolerant of changed dates
    and record types), or the stored matter agrees exactly and both sides'
    supporting excerpts share wording at the answer-relevance floor.
    Excerpts are supports rows only: the edge itself quotes the target, so
    counting it would accept every structurally valid edge. Uncertain pairs
    return False; the caller rejects the edge and keeps existing status.
    """
    if _jaccard(_hint_signature(new_record["record_key"]),
                _hint_signature(target_record["record_key"])) >= DUPE_JACCARD_WEAK:
        return True
    if not _same_matter(new_record, target_record):
        return False
    new_words = set()
    for excerpt in new_excerpts:
        new_words.update(content_words(excerpt))
    target_words = set()
    for excerpt in target_excerpts:
        target_words.update(content_words(excerpt))
    return len(new_words & target_words) >= SUPERSESSION_EXCERPT_WORDS


def apply_supersession_links(con, folder_id: int, rejected: list | None = None,
                             source: str | None = None) -> list[int]:
    """Step down records replaced through validated supersession edges.

    An edge takes effect only when the link evidence resolves, the claimant
    brings distinct resolving evidence of its own, each target is a current
    explicit record whose own supporting excerpt still resolves, both claims
    concern the same matter, and the supersession relationship validates
    (explicit current replacement, no predating, same subject or event).
    Invalid, self-referential, unsupported, unrelated, and unvalidated
    edges are collected into `rejected` for the caller to report — never
    applied silently, and the old records keep their status and provenance.
    Runs before the stale sweep, which takes precedence for records with
    no resolving evidence.
    """
    links = con.execute("""SELECT DISTINCT e.record_id AS new_id, e.evidence_id, e.excerpt
                           FROM memory_record_evidence e
                           JOIN memory_records m ON m.id=e.record_id
                           WHERE m.folder_id=? AND e.role='supersedes'""", (folder_id,)).fetchall()

    def flag(new_id, evidence_id, target_id, reason):
        if rejected is not None:
            rejected.append({"record_id": new_id, "evidence_id": evidence_id,
                             "target_id": target_id, "reason": reason})

    stepped = []
    for link in links:
        new_id, eid = link["new_id"], link["evidence_id"]
        unit = con.execute("SELECT text FROM units WHERE evidence_id=?", (eid,)).fetchone()
        if unit is None or normalize_text(link["excerpt"] or "") not in normalize_text(unit["text"]):
            flag(new_id, eid, None, "supersession link evidence unresolved")
            continue
        self_cite = con.execute("""SELECT 1 FROM memory_record_evidence
                                   WHERE record_id=? AND evidence_id=? AND role='supports'""",
                                (new_id, eid)).fetchone()
        if self_cite is not None:
            flag(new_id, eid, new_id, "self-referential supersession link")
            continue
        own = con.execute("""SELECT e.evidence_id,e.excerpt,u.text FROM memory_record_evidence e
                             LEFT JOIN units u ON u.evidence_id=e.evidence_id
                             WHERE e.record_id=? AND e.role='supports' AND e.evidence_id!=?""",
                          (new_id, eid)).fetchall()
        if not any(row["text"] is not None
                   and normalize_text(row["excerpt"] or "") in normalize_text(row["text"])
                   for row in own):
            flag(new_id, eid, None, "replacement lacks distinct resolving evidence")
            continue
        supporters = con.execute("""SELECT DISTINCT m.id,m.status,s.excerpt FROM memory_records m
                                    JOIN memory_record_evidence s ON s.record_id=m.id
                                    WHERE m.folder_id=? AND m.id!=? AND s.evidence_id=?
                                    AND s.role='supports'""",
                                 (folder_id, new_id, eid)).fetchall()
        if not supporters:
            flag(new_id, eid, None, "no record supports the named evidence")
            continue
        new_row = con.execute("SELECT * FROM memory_records WHERE id=?", (new_id,)).fetchone()
        for target in supporters:
            if target["status"] in ("superseded", "stale"):
                flag(new_id, eid, target["id"], "target not current")
                continue
            if normalize_text(target["excerpt"] or "") not in normalize_text(unit["text"]):
                flag(new_id, eid, target["id"], "target evidence unresolving")
                continue
            target_row = con.execute("SELECT * FROM memory_records WHERE id=?",
                                     (target["id"],)).fetchone()
            target_supports = [row["excerpt"] for row in con.execute(
                "SELECT excerpt FROM memory_record_evidence WHERE record_id=? AND role='supports'",
                (target["id"],))]
            if not _supersession_related(new_row, target_row,
                                         [row["excerpt"] for row in own], target_supports):
                flag(new_id, eid, target["id"], "records concern unrelated matters")
                continue
            verdict = validate_relationship(con, "supersession", new_id, target["id"])
            if verdict["verdict"] != SUPPORTED and new_row["status"] == "conflicted" \
                    and any("only a current record can supersede" in reason
                            for reason in verdict["reasons"]):
                # Production sweeps mark conflicts before attempting
                # supersession, so a legitimate claimant can already be
                # conflicted. Retry only when this target is the
                # claimant's own independently validated conflict
                # partner: stored status is never trusted alone, and
                # every other supersession constraint still applies.
                partnership = validate_relationship(con, "conflict", new_id, target["id"])
                if partnership["verdict"] == SUPPORTED:
                    verdict = validate_relationship(
                        con, "supersession", new_id, target["id"],
                        allow_conflicted_claimant=True)
            if verdict["verdict"] != SUPPORTED:
                flag(new_id, eid, target["id"],
                     f"{verdict['verdict']}: {'; '.join(verdict['reasons'])}")
                continue
            con.execute("UPDATE memory_records SET status='superseded' WHERE id=?", (target["id"],))
            stepped.append(target["id"])
            log_transition(con, folder_id=folder_id, record_id=target["id"],
                           transition="superseded",
                           prev_state=record_state(target_row),
                           new_state=record_state({**dict(target_row), "status": "superseded"}),
                           reason=f"superseded by record {new_id} via validated link {eid}",
                           related_record_id=new_id,
                           evidence=supports_snapshot(con, target["id"])
                           + supports_snapshot(con, new_id),
                           source=source)
            # The winner must not stay conflicted until the next sweep:
            # once its validated partner steps down, a claimant with no
            # remaining validated partner returns to its lifecycle status
            # in this same pass. A claimant still rivaling others stays
            # conflicted.
            claimant = con.execute("SELECT * FROM memory_records WHERE id=?",
                                   (new_id,)).fetchone()
            if claimant is not None and claimant["status"] == "conflicted" \
                    and not conflict_partners(con, folder_id, new_id):
                restored = RESTORED_STATUS[claimant["record_type"]]
                con.execute("UPDATE memory_records SET status=? WHERE id=?",
                            (restored, new_id))
                log_transition(con, folder_id=folder_id, record_id=new_id,
                               transition="conflict_cleared",
                               prev_state=record_state(claimant),
                               new_state=record_state({**dict(claimant), "status": restored}),
                               reason="conflict resolved by validated supersession",
                               related_record_id=target["id"],
                               evidence=supports_snapshot(con, new_id),
                               source=source)
    # Transitive closure, independently revalidated: when C superseded
    # B, records B itself had replacement links over are re-evaluated
    # against C through the full supersession validation. Stored
    # chain membership is never trusted: if C does not independently
    # validate against A, A keeps its status. Iterates to a fixpoint
    # so chains of any length settle in one production sweep.
    progressed = True
    while progressed:
        progressed = False
        chains = con.execute(
            """SELECT t.record_id AS middle_id, t.related_record_id AS winner_id
               FROM memory_transitions t
               JOIN memory_records m ON m.id=t.record_id
               WHERE t.folder_id=? AND t.transition='superseded'
                 AND t.related_record_id IS NOT NULL AND m.status='superseded'""",
            (folder_id,)).fetchall()
        for chain in chains:
            middle_id, winner_id = chain["middle_id"], chain["winner_id"]
            winner = con.execute("SELECT * FROM memory_records WHERE id=?",
                                 (winner_id,)).fetchone()
            if winner is None or winner["status"] in ("superseded", "stale"):
                continue
            outgoing = con.execute(
                "SELECT DISTINCT evidence_id, excerpt FROM memory_record_evidence "
                "WHERE record_id=? AND role='supersedes'", (middle_id,)).fetchall()
            for link in outgoing:
                targets = con.execute(
                    """SELECT DISTINCT m.id FROM memory_records m
                       JOIN memory_record_evidence s ON s.record_id=m.id
                       WHERE m.folder_id=? AND m.id NOT IN (?,?) AND s.evidence_id=?
                         AND s.role='supports' AND m.status NOT IN ('superseded','stale')""",
                    (folder_id, middle_id, winner_id, link["evidence_id"])).fetchall()
                for target in targets:
                    target_row = con.execute("SELECT * FROM memory_records WHERE id=?",
                                             (target["id"],)).fetchone()
                    target_supports = [row["excerpt"] for row in con.execute(
                        "SELECT excerpt FROM memory_record_evidence "
                        "WHERE record_id=? AND role='supports'", (target["id"],))]
                    winner_supports = [row["excerpt"] for row in con.execute(
                        "SELECT excerpt FROM memory_record_evidence "
                        "WHERE record_id=? AND role='supports'", (winner_id,))]
                    if not _supersession_related(winner, target_row,
                                                 winner_supports, target_supports):
                        continue
                    verdict = validate_relationship(con, "supersession",
                                                    winner_id, target["id"])
                    if verdict["verdict"] != SUPPORTED:
                        continue
                    con.execute("UPDATE memory_records SET status='superseded' WHERE id=?",
                                (target["id"],))
                    stepped.append(target["id"])
                    progressed = True
                    log_transition(con, folder_id=folder_id, record_id=target["id"],
                                   transition="superseded",
                                   prev_state=record_state(target_row),
                                   new_state=record_state({**dict(target_row),
                                                           "status": "superseded"}),
                                   reason=f"transitively superseded by record {winner_id} "
                                          f"via record {middle_id} (independently revalidated)",
                                   related_record_id=winner_id,
                                   evidence=supports_snapshot(con, target["id"])
                                   + supports_snapshot(con, winner_id),
                                   source=source)
    return stepped


_CONFIRMATION_MARKERS = ("confirmed", "final confirmation", "final decision", "finally confirmed")
_ESTIMATE_MARKERS = ("quoted", "quote", "estimate", "proposed", "proposal")


def _marker_side(excerpts: list[str], markers: tuple[str, ...]) -> bool:
    blob = " ".join(excerpts).casefold()
    return any(marker in blob for marker in markers)


def generate_confirmed_quote_candidates(con, folder_id: int,
                                        pairs: list[tuple[int, int]]) -> int:
    """Deterministic supersession *candidacy* for validated conflicts.

    When exactly one side of a validated conflict carries explicit
    confirmation language in its supporting excerpts and the other
    carries estimate language, insert a supersession link from the
    confirmed record quoting the estimate record's evidence. The link
    is only a candidate: apply_supersession_links still runs the full
    relationship validation, so proposals, inferred records, and
    unrelated pairs are rejected there. No asymmetry, no candidate.
    Returns the number of links inserted."""
    inserted = 0
    for left_id, right_id in pairs:
        sides = []
        for record_id in (left_id, right_id):
            record = con.execute("SELECT * FROM memory_records WHERE id=?",
                                 (record_id,)).fetchone()
            excerpts = [row["excerpt"] for row in con.execute(
                "SELECT excerpt FROM memory_record_evidence "
                "WHERE record_id=? AND role='supports'", (record_id,))]
            sides.append((record, excerpts))
        confirmed = [s for s in sides if _marker_side(s[1], _CONFIRMATION_MARKERS)]
        estimated = [s for s in sides if _marker_side(s[1], _ESTIMATE_MARKERS)]
        if len(confirmed) != 1 or len(estimated) != 1 or confirmed[0] is estimated[0]:
            continue
        winner, loser = confirmed[0][0], estimated[0][0]
        if winner["id"] == loser["id"]:
            continue
        loser_link = con.execute(
            """SELECT e.evidence_id,e.excerpt FROM memory_record_evidence e
               WHERE e.record_id=? AND e.role='supports' LIMIT 1""",
            (loser["id"],)).fetchone()
        if loser_link is None:
            continue
        cursor = con.execute(
            "INSERT OR IGNORE INTO memory_record_evidence(record_id,evidence_id,role,excerpt)"
            " VALUES(?,?, 'supersedes', ?)",
            (winner["id"], loser_link["evidence_id"], loser_link["excerpt"]))
        inserted += cursor.rowcount
    return inserted


def mark_stale(con, folder_id: int, source: str | None = None) -> list[int]:
    """Flag records whose supporting evidence no longer resolves. Newly
    stale records append one ledger entry each, snapshotting the
    unresolving supports before they are gone. Idempotent."""
    rows = con.execute("SELECT id FROM memory_records WHERE folder_id=? AND status!='stale'",
                       (folder_id,)).fetchall()
    stale = [row["id"] for row in rows if not resolving_supports(con, row["id"])]
    for record_id in stale:
        full = dict(con.execute("SELECT * FROM memory_records WHERE id=?",
                                (record_id,)).fetchone())
        con.execute("UPDATE memory_records SET status='stale' WHERE id=?", (record_id,))
        log_transition(con, folder_id=folder_id, record_id=record_id,
                       transition="stale_marked",
                       prev_state=record_state(full),
                       new_state=record_state({**full, "status": "stale"}),
                       reason="no resolving supports",
                       evidence=supports_snapshot(con, record_id),
                       source=source)
    return stale


# Hint thresholds, calibrated on reviewed live paraphrases (see
# tests/test_memory_duplicates.py). Hints never merge: matching stays
# similarity-based and conservative, merging stays exact-key-only.
DUPE_JACCARD_STRONG = 0.5
DUPE_JACCARD_WEAK = 0.2


def _hint_signature(record_key: str) -> frozenset[str]:
    """Key-phrase words minus digit artifacts ('5,000' tokenizes to '000').

    Identity (record_key) keeps every token; similarity ignores numeric
    fragments that make distinct money claims look alike."""
    parts = str(record_key).split("|")
    if len(parts) not in (5, 6):
        return frozenset()
    return frozenset(word for word in parts[4].split("+") if word and not word.isdigit())


def _jaccard(left: frozenset[str], right: frozenset[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _same_matter(left, right) -> bool:
    return (left["record_type"] == right["record_type"]
            and kind_group_of(left["statement_kind"]) == kind_group_of(right["statement_kind"])
            and (left["date_value"] or None) == (right["date_value"] or None)
            and (left["assignee"] or "").strip().casefold() == (right["assignee"] or "").strip().casefold())


def claims_match(left, right, shared_evidence: bool) -> bool:
    """Same conservative predicate as duplicate hints: shared matter plus
    strong wording overlap, or weaker overlap anchored on shared evidence.
    Used for presentation-level grouping only; never merges records."""
    if not _same_matter(left, right):
        return False
    score = _jaccard(_hint_signature(left["record_key"]), _hint_signature(right["record_key"]))
    return score >= DUPE_JACCARD_STRONG or (shared_evidence and score >= DUPE_JACCARD_WEAK)


def find_duplicate_candidates(con, folder_id: int) -> list[tuple[int, int]]:
    """Possible same-claim pairs: review hints only, never an automatic merge.

    Candidates share type, epistemic group, date, and assignee, plus strong
    wording overlap, or weaker overlap anchored on shared supporting
    evidence, and their identity relationship must validate (same subject
    or event, resolving evidence on both sides). Paraphrase re-extractions
    usually cite identical evidence, so the old disjoint-evidence
    requirement systematically missed them."""
    rows = con.execute("""SELECT id,record_type,statement_kind,date_value,assignee,record_key
                          FROM memory_records
                          WHERE folder_id=? AND status NOT IN ('stale','superseded')""",
                       (folder_id,)).fetchall()
    supports = {}
    for row in rows:
        supports[row["id"]] = {r["evidence_id"] for r in con.execute(
            "SELECT evidence_id FROM memory_record_evidence WHERE record_id=? AND role='supports'",
            (row["id"],))}
    hints = []
    listed = list(rows)
    for pos, left in enumerate(listed):
        for right in listed[pos + 1:]:
            if not claims_match(left, right, bool(supports[left["id"]] & supports[right["id"]])):
                continue
            verdict = validate_relationship(con, "duplicate", left["id"], right["id"])
            if verdict["verdict"] == SUPPORTED:
                hints.append((left["id"], right["id"]))
    return hints


def answer_eligibility(status: str, statement_kind: str, resolving_supports: int) -> tuple[str, str]:
    """Serving verdict for one record. Inferred records are JSONL-only in
    this milestone: never eligible for factual answers."""
    if status in ("stale", "superseded") or not resolving_supports:
        return ("excluded", "unsupported by current evidence")
    if status == "conflicted":
        return ("excluded", "contradicted: surface only as a conflict pair")
    if statement_kind == "inferred":
        return ("excluded", "inference: experiment output only, not factual answers")
    if statement_kind == "unresolved":
        return ("undecided_only", "usable only for what-is-undecided questions")
    if statement_kind == "proposal":
        return ("labeled", "must render as proposed, not decided")
    return ("eligible", "grounded explicit record")

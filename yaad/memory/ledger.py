"""Temporal Memory Ledger: append-only history of validated transitions.

Every validated memory transition appends exactly one row preserving the
previous and new state, timestamp, transition type, human-readable reason,
related record, and a snapshot of the supporting evidence at transition
time. Snapshots (not live joins) mean history survives source deletion:
"what was the previous decision, and what supported it" stays answerable
after the units are gone.

Append-only is enforced by construction: this module exposes only INSERTs,
and no statement anywhere updates or deletes ledger rows. The sole
removal path is ON DELETE CASCADE on user-initiated folder/record
deletion (privacy outranks ledger purity). Sweeps log only actual flips,
so reruns over unchanged state append nothing.

No model calls; deterministic given the calling sweep.
"""

import json

LEDGER_SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_transitions(
  id INTEGER PRIMARY KEY,
  folder_id INTEGER NOT NULL REFERENCES folders(id) ON DELETE CASCADE,
  record_id INTEGER NOT NULL REFERENCES memory_records(id) ON DELETE CASCADE,
  transition TEXT NOT NULL,
  prev_state TEXT,
  new_state TEXT NOT NULL,
  reason TEXT NOT NULL DEFAULT '',
  related_record_id INTEGER,
  evidence TEXT NOT NULL DEFAULT '[]',
  source TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_transitions_folder ON memory_transitions(folder_id, id);
CREATE INDEX IF NOT EXISTS idx_transitions_record ON memory_transitions(record_id, id);
"""

TRANSITION_KINDS = (
    "record_created",
    "record_updated",
    "conflict_marked",
    "conflict_cleared",
    "superseded",
    "stale_marked",
    "restored",
)

# Material record content: changes here mean the record says something new.
# Status moves are their own transitions; record_key/extraction_run churn is
# bookkeeping, never content.
MATERIAL_FIELDS = (
    "title",
    "detail",
    "date_value",
    "date_raw",
    "amount_value",
    "amount_raw",
    "assignee",
    "statement_kind",
)


def record_state(row) -> dict:
    """Point-in-time material snapshot of a memory record row."""
    record = dict(row)
    return {field: record.get(field) for field in (*MATERIAL_FIELDS, "status")}


def supports_snapshot(con, record_id: int) -> list[dict]:
    """Current evidence rows for one record: every role, verbatim
    excerpts, stable row order. Snapshots freeze provenance at
    transition time instead of joining live units later."""
    return [dict(item) for item in con.execute(
        """SELECT ? AS record_id, evidence_id, role, excerpt
           FROM memory_record_evidence WHERE record_id=? ORDER BY rowid""",
        (record_id, record_id))]


def log_transition(con, *, folder_id: int, record_id: int, transition: str,
                   prev_state: dict | None, new_state: dict,
                   reason: str = "", related_record_id: int | None = None,
                   evidence: list[dict] | None = None,
                   source: str | None = None) -> int:
    """Append one validated transition. Callers log only actual flips, so
    reruns over unchanged state append nothing."""
    if transition not in TRANSITION_KINDS:
        raise ValueError(f"unknown transition kind: {transition!r}")
    cursor = con.execute(
        """INSERT INTO memory_transitions(folder_id,record_id,transition,prev_state,new_state,
           reason,related_record_id,evidence,source)
           VALUES(?,?,?,?,?,?,?,?,?)""",
        (folder_id, record_id, transition,
         None if prev_state is None else json.dumps(prev_state, sort_keys=True),
         json.dumps(new_state, sort_keys=True), reason, related_record_id,
         json.dumps(evidence or []), source))
    return cursor.lastrowid


def _parse(row) -> dict:
    entry = dict(row)
    entry["prev_state"] = json.loads(entry["prev_state"]) if entry["prev_state"] else None
    entry["new_state"] = json.loads(entry["new_state"])
    entry["evidence"] = json.loads(entry["evidence"])
    return entry


def list_transitions(con, folder_id: int | None = None, record_id: int | None = None,
                     kinds=None, since: str | None = None, until: str | None = None,
                     limit: int = 50, newest_first: bool = True) -> list[dict]:
    """Query the ledger: folder/record/kind/time filters, recency order by
    default. `since`/`until` compare lexicographically against ISO
    timestamps. Unknown kinds raise ValueError (the API maps it to 400)."""
    if isinstance(kinds, str):
        kinds = [kinds]
    for kind in kinds or []:
        if kind not in TRANSITION_KINDS:
            raise ValueError(f"unknown transition kind: {kind!r}")
    sql = "SELECT * FROM memory_transitions WHERE 1=1"
    args: list = []
    if folder_id is not None:
        sql += " AND folder_id=?"
        args.append(folder_id)
    if record_id is not None:
        sql += " AND record_id=?"
        args.append(record_id)
    if kinds:
        sql += f" AND transition IN ({','.join('?' for _ in kinds)})"
        args.extend(kinds)
    if since is not None:
        sql += " AND created_at>=?"
        args.append(since)
    if until is not None:
        sql += " AND created_at<=?"
        args.append(until)
    sql += " ORDER BY id DESC" if newest_first else " ORDER BY id ASC"
    sql += " LIMIT ?"
    args.append(limit)
    return [_parse(row) for row in con.execute(sql, args)]


def record_history(con, record_id: int) -> list[dict]:
    """One record's full story, oldest first."""
    return [_parse(row) for row in con.execute(
        "SELECT * FROM memory_transitions WHERE record_id=? ORDER BY id ASC", (record_id,))]


def previous_decision(con, folder_id: int, record_id: int) -> dict | None:
    """The decision a record most recently replaced: target identity,
    point-in-time values, supporting evidence, and timestamp. None when
    the record replaced nothing (chains walk by repeated lookup)."""
    row = con.execute(
        """SELECT * FROM memory_transitions
           WHERE folder_id=? AND transition='superseded' AND related_record_id=?
           ORDER BY id DESC LIMIT 1""", (folder_id, record_id)).fetchone()
    if row is None:
        return None
    entry = _parse(row)
    state = entry["prev_state"] or {}
    return {"record_id": entry["record_id"], "transition_id": entry["id"],
            "superseded_at": entry["created_at"], "title": state.get("title"),
            "detail": state.get("detail"), "date_value": state.get("date_value"),
            "amount_value": state.get("amount_value"), "evidence": entry["evidence"]}

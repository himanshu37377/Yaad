"""CP1: deterministic validation, identity, lifecycle, and eligibility tests.

Hand-written fixtures on temporary databases only. No model calls, no
production database contact, no API/UI/answer changes.
"""
import pytest

from yaad.db import Database
from yaad.memory.records import (
    MEMORY_SCHEMA,
    answer_eligibility,
    apply_supersession_links,
    compute_record_key,
    conflict_key_of,
    find_duplicate_candidates,
    mark_conflicts,
    mark_stale,
    upsert_records,
    validate_candidate,
)

UNITS = {
    "ev_draft_a": "Deliver the launch draft by March 3rd without fail.",
    "ev_draft_b": "Reminder: launch draft due March 3rd.",
    "ev_draft_moved": "The launch draft moved to March 10th instead.",
    "ev_room": "Maya to book the seminar room.",
    "ev_slides": "Tomas to finalize the slides.",
    "ev_venue_open": "Which venue was decided? Still open.",
}


@pytest.fixture
def store(tmp_path):
    database = Database(tmp_path / "mem.sqlite3")
    with database.transaction() as con:
        con.executescript(MEMORY_SCHEMA)
        con.execute("INSERT INTO folders(path, cloud_consent) VALUES(?, 1)", (str(tmp_path / "src"),))
        folder_id = con.execute("SELECT id FROM folders").fetchone()["id"]
        con.execute(
            "INSERT INTO files(folder_id,path,sha256,kind,size,mtime,status,cloud_consent)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (folder_id, str(tmp_path / "src" / "notes.txt"), "0" * 64, "text", 10, 0.0, "indexed", 1))
        file_id = con.execute("SELECT id FROM files").fetchone()["id"]
        for eid, text in UNITS.items():
            con.execute(
                "INSERT INTO units(file_id,evidence_id,kind,locator,text) VALUES(?,?,?,?,?)",
                (file_id, eid, "text_span", '{"type":"text_span"}', text))
    return database, folder_id


def candidate(**over):
    base = {
        "record_type": "deadline", "title": "Launch draft due", "detail": "Draft due March 3rd.",
        "date_value": "2026-03-03", "date_raw": "March 3rd", "assignee": None,
        "status": "open", "statement_kind": "explicit", "key_phrase": "launch draft",
        "evidence": [{"evidence_id": "ev_draft_a", "role": "supports",
                      "excerpt": "Deliver the launch draft by March 3rd"}],
    }
    base.update(over)
    return base


def upserted(database, folder_id, run, candidates):
    with database.transaction() as con:
        return upsert_records(con, folder_id, run, candidates, dict(UNITS))


# --- identity ---

def test_key_is_deterministic_and_separates_epistemic_states():
    args = ("deadline", "2026-03-03", None, "launch draft")
    assert compute_record_key("deadline", "explicit", *args[1:]) == compute_record_key("deadline", "explicit", *args[1:])
    assert (compute_record_key("deadline", "explicit", *args[1:])
            == compute_record_key("deadline", "inferred", *args[1:]))
    assert (compute_record_key("deadline", "explicit", *args[1:])
            != compute_record_key("deadline", "proposal", *args[1:]))
    assert (compute_record_key("deadline", "explicit", *args[1:])
            != compute_record_key("deadline", "explicit", "2026-03-10", None, "launch draft"))
    assert (compute_record_key("decision", "explicit", None, None, "fee registration", "INR150")
            != compute_record_key("decision", "explicit", None, None, "fee registration", "INR200"))
    assert (compute_record_key("decision", "explicit", None, None, "fee registration", "INR150")
            == compute_record_key("decision", "explicit", None, None, "fee registration", "INR150"))
    assert conflict_key_of("deadline", None, "launch draft") == conflict_key_of("deadline", None, "draft launch")


def test_valid_candidate_passes():
    assert validate_candidate(candidate(), dict(UNITS)) == []


def test_rejects_unknown_evidence_and_uncontained_excerpt():
    bad_id = candidate(evidence=[{"evidence_id": "ev_nope", "role": "supports", "excerpt": "x"}])
    assert any("unknown evidence_id" in err for err in validate_candidate(bad_id, dict(UNITS)))
    bad_excerpt = candidate(evidence=[{"evidence_id": "ev_draft_a", "role": "supports",
                                        "excerpt": "launch draft by April 9th"}])
    assert any("not contained" in err for err in validate_candidate(bad_excerpt, dict(UNITS)))


def test_rejects_invalid_combinations():
    assert validate_candidate(candidate(record_type="decision", status="open"), dict(UNITS))
    assert validate_candidate(candidate(record_type="open_question", status="decided"), dict(UNITS))
    assert validate_candidate(candidate(statement_kind="proposal", status="decided"), dict(UNITS))
    assert validate_candidate(candidate(date_value=None), dict(UNITS))
    assert validate_candidate(candidate(statement_kind="inferred", assignee="Maya"), dict(UNITS))
    assert validate_candidate(candidate(status="conflicted"), dict(UNITS))


def test_rejects_invented_key_phrase():
    assert any("key_phrase" in err
               for err in validate_candidate(candidate(key_phrase="launch rocket"), dict(UNITS)))


# --- merge vs separation ---

def test_same_claim_merges_and_reupsert_is_stable(store):
    database, folder_id = store
    first = candidate()
    second = candidate(evidence=[{"evidence_id": "ev_draft_b", "role": "supports",
                                   "excerpt": "launch draft due March 3rd"}])
    upserted(database, folder_id, "run-1", [first])
    upserted(database, folder_id, "run-2", [first, second])
    with database.connect() as con:
        assert con.execute("SELECT COUNT(*) n FROM memory_records").fetchone()["n"] == 1
        rows = con.execute("SELECT evidence_id FROM memory_record_evidence").fetchall()
        assert {row["evidence_id"] for row in rows} == {"ev_draft_a", "ev_draft_b"}
    upserted(database, folder_id, "run-3", [first, second])
    with database.connect() as con:
        assert con.execute("SELECT COUNT(*) n FROM memory_records").fetchone()["n"] == 1
        assert con.execute("SELECT COUNT(*) n FROM memory_record_evidence").fetchone()["n"] == 2


def test_duplicate_evidence_rows_within_candidate_dedupe(store):
    database, folder_id = store
    dupe = candidate(evidence=[
        {"evidence_id": "ev_draft_a", "role": "supports",
         "excerpt": "Deliver the launch draft by March 3rd"},
        {"evidence_id": "ev_draft_a", "role": "supports",
         "excerpt": "Deliver the launch draft by March 3rd"}])
    upserted(database, folder_id, "run-1", [dupe])
    with database.connect() as con:
        assert con.execute("SELECT COUNT(*) n FROM memory_record_evidence").fetchone()["n"] == 1


def test_distinct_commitments_stay_separate(store):
    database, folder_id = store
    maya = candidate(record_type="commitment", title="Maya books room", detail="Maya to book room.",
                     date_value=None, date_raw=None, assignee="Maya", key_phrase="book seminar room",
                     evidence=[{"evidence_id": "ev_room", "role": "supports",
                                "excerpt": "Maya to book the seminar room"}])
    tomas = candidate(record_type="commitment", title="Tomas finalizes slides",
                      detail="Tomas to finalize slides.", date_value=None, date_raw=None,
                      assignee="Tomas", key_phrase="finalize slides",
                      evidence=[{"evidence_id": "ev_slides", "role": "supports",
                                 "excerpt": "Tomas to finalize the slides"}])
    assert validate_candidate(maya, dict(UNITS)) == []
    assert validate_candidate(tomas, dict(UNITS)) == []
    upserted(database, folder_id, "run-1", [maya, tomas])
    with database.connect() as con:
        assert con.execute("SELECT COUNT(*) n FROM memory_records").fetchone()["n"] == 2


def test_same_date_different_action_stays_separate_with_hint(store):
    database, folder_id = store
    draft = candidate()
    other = candidate(title="Reminder note", detail="Reminder about March 3rd.",
                      date_raw="March 3rd", key_phrase="reminder march",
                      evidence=[{"evidence_id": "ev_draft_b", "role": "supports",
                                 "excerpt": "Reminder: launch draft due March 3rd"}])
    overlap = candidate(title="Draft reminder", detail="Draft reminder March 3rd.",
                        date_raw="March 3rd", key_phrase="draft reminder",
                        evidence=[{"evidence_id": "ev_draft_b", "role": "supports",
                                   "excerpt": "Reminder: launch draft due March 3rd"}])
    upserted(database, folder_id, "run-1", [draft, other, overlap])
    with database.connect() as con:
        assert con.execute("SELECT COUNT(*) n FROM memory_records").fetchone()["n"] == 3
        titles = {row["id"]: row["title"] for row in
                  con.execute("SELECT id,title FROM memory_records")}
        hints = find_duplicate_candidates(con, folder_id)
        # Only the shared-evidence pair with weak wording overlap is hinted;
        # disjoint-vocabulary and disjoint-evidence pairs stay silent.
        assert len(hints) == 1
        assert {titles[a] for a, _ in hints} | {titles[b] for _, b in hints} == {
            "Reminder note", "Draft reminder"}


def test_explicit_and_proposal_do_not_merge(store):
    database, folder_id = store
    firm = candidate()
    soft = candidate(statement_kind="proposal", title="Maybe March 3rd",
                     evidence=[{"evidence_id": "ev_draft_b", "role": "supports",
                                "excerpt": "launch draft due March 3rd"}])
    upserted(database, folder_id, "run-1", [firm, soft])
    with database.connect() as con:
        assert con.execute("SELECT COUNT(*) n FROM memory_records").fetchone()["n"] == 2


def test_inferred_upgrades_to_explicit_on_merge(store):
    database, folder_id = store
    guess = candidate(statement_kind="inferred")
    firm = candidate()
    upserted(database, folder_id, "run-1", [guess])
    upserted(database, folder_id, "run-2", [firm])
    with database.connect() as con:
        kinds = {row["statement_kind"] for row in con.execute("SELECT statement_kind FROM memory_records")}
        assert kinds == {"explicit"}
        assert con.execute("SELECT COUNT(*) n FROM memory_records").fetchone()["n"] == 1


# --- lifecycle ---

def test_changed_date_stays_separate_then_conflicts_idempotently(store):
    database, folder_id = store
    march3 = candidate()
    march10 = candidate(title="Launch draft due (moved)", detail="Draft moved to March 10th.",
                        date_value="2026-03-10", date_raw="March 10th",
                        evidence=[{"evidence_id": "ev_draft_moved", "role": "supports",
                                   "excerpt": "launch draft moved to March 10th"}])
    upserted(database, folder_id, "run-1", [march3, march10])
    with database.transaction() as con:
        pairs = mark_conflicts(con, folder_id)
        assert len(pairs) == 1
        assert mark_conflicts(con, folder_id) == pairs
    with database.connect() as con:
        statuses = {row["status"] for row in con.execute("SELECT status FROM memory_records")}
        assert statuses == {"conflicted"}


def test_stale_on_source_delete_and_restore_on_return(store):
    database, folder_id = store
    upserted(database, folder_id, "run-1", [candidate()])
    with database.transaction() as con:
        con.execute("DELETE FROM units")
        assert mark_stale(con, folder_id) != []
        assert mark_stale(con, folder_id) == []
    with database.connect() as con:
        assert con.execute("SELECT status FROM memory_records").fetchone()["status"] == "stale"
    with database.transaction() as con:
        file_id = con.execute("SELECT id FROM files").fetchone()["id"]
        con.execute("INSERT INTO units(file_id,evidence_id,kind,locator,text) VALUES(?,?,?,?,?)",
                    (file_id, "ev_draft_a", "text_span", '{"type":"text_span"}', UNITS["ev_draft_a"]))
    upserted(database, folder_id, "run-2", [candidate()])
    with database.connect() as con:
        assert con.execute("SELECT status FROM memory_records").fetchone()["status"] == "open"


def test_conflict_clears_when_pair_goes_stale(store):
    database, folder_id = store
    march3 = candidate()
    march10 = candidate(title="Launch draft due (moved)", detail="Draft moved to March 10th.",
                        date_value="2026-03-10", date_raw="March 10th",
                        evidence=[{"evidence_id": "ev_draft_moved", "role": "supports",
                                   "excerpt": "launch draft moved to March 10th"}])
    upserted(database, folder_id, "run-1", [march3, march10])
    with database.transaction() as con:
        mark_conflicts(con, folder_id)
        con.execute("DELETE FROM units WHERE evidence_id='ev_draft_moved'")
        mark_stale(con, folder_id)
        mark_conflicts(con, folder_id)
    with database.connect() as con:
        statuses = sorted(row["status"] for row in con.execute("SELECT status FROM memory_records"))
        assert statuses == ["open", "stale"]


def test_supersession_link_marks_superseded(store):
    database, folder_id = store
    old = candidate()
    new = candidate(title="Launch draft due (moved)", detail="Draft moved to March 10th.",
                    date_value="2026-03-10", date_raw="March 10th",
                    evidence=[{"evidence_id": "ev_draft_moved", "role": "supports",
                               "excerpt": "launch draft moved to March 10th"},
                              {"evidence_id": "ev_draft_a", "role": "supersedes",
                               "excerpt": "Deliver the launch draft by March 3rd"}])
    upserted(database, folder_id, "run-1", [old, new])
    with database.transaction() as con:
        assert apply_supersession_links(con, folder_id) != []
    with database.connect() as con:
        by_title = {row["title"]: row["status"]
                    for row in con.execute("SELECT title,status FROM memory_records")}
        assert by_title["Launch draft due"] == "superseded"
        assert by_title["Launch draft due (moved)"] == "open"


# --- eligibility ---

def test_answer_eligibility_matrix():
    assert answer_eligibility("decided", "explicit", 1)[0] == "eligible"
    assert answer_eligibility("open", "explicit", 2)[0] == "eligible"
    assert answer_eligibility("open", "proposal", 1)[0] == "labeled"
    assert answer_eligibility("open", "unresolved", 1)[0] == "undecided_only"
    assert answer_eligibility("open", "inferred", 3)[0] == "excluded"
    assert answer_eligibility("conflicted", "explicit", 2)[0] == "excluded"
    assert answer_eligibility("stale", "explicit", 0)[0] == "excluded"
    assert answer_eligibility("superseded", "explicit", 1)[0] == "excluded"
    assert answer_eligibility("open", "explicit", 0)[0] == "excluded"


# --- schema ---

def test_schema_applies_and_cascades(store):
    database, folder_id = store
    upserted(database, folder_id, "run-1", [candidate()])
    with database.transaction() as con:
        record_id = con.execute("SELECT id FROM memory_records").fetchone()["id"]
        con.execute("INSERT INTO record_flags(record_id,reason) VALUES(?,'typo?')", (record_id,))
        con.execute("DELETE FROM memory_records WHERE id=?", (record_id,))
        assert con.execute("SELECT COUNT(*) n FROM memory_record_evidence").fetchone()["n"] == 0
        assert con.execute("SELECT COUNT(*) n FROM record_flags").fetchone()["n"] == 0
    upserted(database, folder_id, "run-2", [candidate()])
    with database.transaction() as con:
        con.execute("DELETE FROM folders WHERE id=?", (folder_id,))
        assert con.execute("SELECT COUNT(*) n FROM memory_records").fetchone()["n"] == 0

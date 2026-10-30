"""Regressions for the five Evaluation V2 fixes: slot-aware Tier A,
transitive supersession, confirmed-vs-quoted candidacy, unresolved
synthesis status, and question-aware photo ranking. Deterministic,
disposable databases, no model calls.
"""
from test_memory_answers import Store, candidate
from test_memory_extract import StubClient
from test_photo_answers import add_image
from yaad.memory.extract import run_extraction
from yaad.memory.records import (
    apply_supersession_links,
    generate_confirmed_quote_candidates,
    mark_conflicts,
)


# --- Fix 1: slot-aware Tier A -------------------------------------------

def test_venue_record_cannot_answer_budget_question(tmp_path):
    store = Store(tmp_path / "slot.sqlite3")
    store.add_file("a.txt", [("ev_a", "Autumn Fest venue is Riverside Hall on October 5")])
    store.add_records("run-1", [candidate(
        "Autumn Fest venue Riverside Hall", "autumn fest venue",
        [("ev_a", "Autumn Fest venue is Riverside Hall")],
        record_type="decision", status="decided", date_value=None, date_raw=None,
        detail="Autumn Fest venue is Riverside Hall on October 5, 2027.")])
    result = store.ask("What is the Autumn Fest budget?")
    assert result["status"] in {"unverified", "not_found", "cloud_unavailable"}
    assert result["memory"]["claims"] == []
    assert result["memory"]["context_claims"], "wrong-slot record must stay contextual"
    assert result["memory"]["context_claims"][0]["relevance"]["tier"] == "contextual"


def test_budget_record_still_answers_budget_question(tmp_path):
    store = Store(tmp_path / "slot2.sqlite3")
    store.add_file("a.txt", [("ev_a", "Spring Fest budget is Rs 20000")])
    store.add_records("run-1", [candidate(
        "Spring Fest budget Rs 20000", "spring fest budget",
        [("ev_a", "Spring Fest budget is Rs 20000")],
        record_type="decision", status="decided", date_value=None, date_raw=None,
        detail="Spring Fest budget is Rs 20000")])
    result = store.ask("What is the Spring Fest budget?")
    assert result["status"] == "answered"
    assert result["memory"]["claims"][0]["relevance"]["tier"] == "answerable"


# --- Fix 2: transitive supersession --------------------------------------

def _deadline_chain(path, final_kind="explicit"):
    store = Store(path)
    store.add_file("a.txt", [("ev_a", "Registration deadline is March 1, 2027 for Spring Fest")])
    store.add_file("b.txt", [("ev_b", "Registration deadline is April 2, 2027 for Spring Fest")])
    store.add_file("c.txt", [("ev_c", "Registration deadline is April 9, 2027 for Spring Fest")])
    store.add_records("run-1", [
        candidate("Deadline March", "registration deadline",
                  [("ev_a", "Registration deadline is March 1, 2027")],
                  date_value="2027-03-01", date_raw="March 1, 2027",
                  detail="Registration deadline is March 1, 2027 for Spring Fest"),
        candidate("Deadline April 2", "registration deadline",
                  [("ev_b", "Registration deadline is April 2, 2027")],
                  date_value="2027-04-02", date_raw="April 2, 2027",
                  detail="Registration deadline is April 2, 2027 for Spring Fest"),
        candidate("Deadline April 9", "registration deadline",
                  [("ev_c", "Registration deadline is April 9, 2027")],
                  date_value="2027-04-09", date_raw="April 9, 2027",
                  statement_kind=final_kind, status="open",
                  detail="Registration deadline is April 9, 2027 for Spring Fest"),
    ])
    with store.db.transaction() as con:
        con.execute("INSERT INTO memory_record_evidence(record_id,evidence_id,role,excerpt)"
                    " VALUES(2,'ev_a','supersedes','Registration deadline is March 1, 2027')")
        con.execute("INSERT INTO memory_record_evidence(record_id,evidence_id,role,excerpt)"
                    " VALUES(3,'ev_b','supersedes','Registration deadline is April 2, 2027')")
        mark_conflicts(con, store.folder_id)
        stepped = apply_supersession_links(con, store.folder_id)
    return store, stepped


def _statuses(store):
    with store.db.connect() as con:
        return {r["id"]: r["status"] for r in
                con.execute("SELECT id,status FROM memory_records ORDER BY id")}


def test_three_step_chain_fully_supersedes_in_production_order(tmp_path):
    store, _ = _deadline_chain(tmp_path / "chain.sqlite3")
    assert _statuses(store) == {1: "superseded", 2: "superseded", 3: "open"}
    result = store.ask("What is the registration deadline?")
    assert result["status"] == "answered"
    assert {v["value"] for v in result["values"]} == {"April 9, 2027"}


def test_failed_transitive_validation_keeps_records_current(tmp_path):
    store, _ = _deadline_chain(tmp_path / "chain2.sqlite3", final_kind="proposal")
    statuses = _statuses(store)
    assert statuses[1] != "superseded" or statuses[2] != "superseded"
    assert statuses[3] != "superseded"


# --- Fix 3: confirmed-vs-quoted candidacy --------------------------------

def test_confirmed_amount_supersedes_quoted_amounts(tmp_path):
    store = Store(tmp_path / "cq.sqlite3")
    store.add_file("a.txt", [("ev_a", "Venue hire cost for Spring Fest is 12000 INR, quoted by City Halls.")])
    store.add_file("b.txt", [("ev_b", "Venue hire cost for Spring Fest is 15000 INR, quoted by City Halls.")])
    store.add_file("c.txt", [("ev_c", "Venue hire cost for Spring Fest is 18000 INR, confirmed with City Halls.")])
    from yaad.config import Settings
    settings = Settings(db_path=tmp_path / "cq.sqlite3", model_api_key="unused",
                        cloud_enabled=True)
    cands = []
    for title, key, ev, text in [
            ("Spring Fest venue hire 12000 INR", "venue hire cost", "ev_a",
             "Venue hire cost for Spring Fest is 12000 INR, quoted by City Halls."),
            ("Spring Fest venue hire 15000 INR", "venue hire cost", "ev_b",
             "Venue hire cost for Spring Fest is 15000 INR, quoted by City Halls."),
            ("Spring Fest venue hire 18000 INR", "venue hire cost", "ev_c",
             "Venue hire cost for Spring Fest is 18000 INR, confirmed with City Halls.")]:
        cands.append({
            "record_type": "decision", "title": title, "detail": text,
            "date_value": None, "date_raw": None, "assignee": None,
            "status": "decided", "statement_kind": "explicit", "key_phrase": key,
            "evidence": [{"evidence_id": ev, "role": "supports", "excerpt": text}]})
    summary = run_extraction(store.db, store.folder_id, "run-cq", settings,
                             StubClient(cands))
    # Store.db is a Database wrapper around the same file the records
    # were written to; read statuses back through it.
    with store.db.connect() as con:
        statuses = {r["amount_value"]: r["status"] for r in con.execute(
            "SELECT amount_value,status FROM memory_records")}
    assert statuses["INR18000"] == "decided"
    assert statuses["INR12000"] == "superseded"
    assert statuses["INR15000"] == "superseded"


def test_genuine_confirmed_conflict_generates_no_candidate(tmp_path):
    store = Store(tmp_path / "cq2.sqlite3")
    store.add_file("a.txt", [("ev_a", "Catering contract confirmed at 8000 INR with Fresh Foods")])
    store.add_file("b.txt", [("ev_b", "Catering contract confirmed at 9500 INR with Fresh Foods")])
    store.add_records("run-1", [
        candidate("Catering 8000", "catering contract",
                  [("ev_a", "Catering contract confirmed at 8000 INR")],
                  record_type="decision", status="decided", date_value=None, date_raw=None,
                  detail="Catering contract confirmed at 8000 INR with Fresh Foods"),
        candidate("Catering 9500", "catering contract",
                  [("ev_b", "Catering contract confirmed at 9500 INR")],
                  record_type="decision", status="decided", date_value=None, date_raw=None,
                  detail="Catering contract confirmed at 9500 INR with Fresh Foods")])
    with store.db.transaction() as con:
        pairs = mark_conflicts(con, store.folder_id)
        assert pairs, "genuine conflict must be validated first"
        assert generate_confirmed_quote_candidates(con, store.folder_id, pairs) == 0
        apply_supersession_links(con, store.folder_id)
    assert set(_statuses(store).values()) == {"conflicted"}


def test_proposal_with_confirmation_wording_cannot_supersede(tmp_path):
    store = Store(tmp_path / "cq3.sqlite3")
    store.add_file("a.txt", [("ev_a", "Spring Fest budget is Rs 20000, quoted by the council")])
    store.add_file("b.txt", [("ev_b", "Proposed Spring Fest budget confirmed at Rs 25000")])
    store.add_records("run-1", [
        candidate("Spring Fest budget Rs 20000", "spring fest budget",
                  [("ev_a", "Spring Fest budget is Rs 20000")],
                  record_type="decision", status="decided", date_value=None, date_raw=None,
                  detail="Spring Fest budget is Rs 20000, quoted by the council"),
        candidate("Proposed budget Rs 25000", "spring fest budget",
                  [("ev_b", "Proposed Spring Fest budget confirmed at Rs 25000")],
                  record_type="commitment", status="open", date_value=None, date_raw=None,
                  statement_kind="proposal",
                  detail="Proposed Spring Fest budget confirmed at Rs 25000")])
    with store.db.transaction() as con:
        pairs = mark_conflicts(con, store.folder_id)
        generate_confirmed_quote_candidates(con, store.folder_id, pairs)
        stepped = apply_supersession_links(con, store.folder_id)
    assert stepped == []
    assert _statuses(store)[1] == "decided"


# --- Fix 4: unresolved synthesis ------------------------------------------

def test_unresolved_only_synthesis_is_unverified(tmp_path):
    store = Store(tmp_path / "unres.sqlite3")
    notice = "Council is deciding between Rs 20000 and Rs 25000 for the snack budget"
    store.add_file("a.txt", [("ev_a", notice)])
    store.add_records("run-1", [candidate(
        "Snack budget undecided", "snack budget", [("ev_a", notice)],
        record_type="open_question", status="open", date_value=None, date_raw=None,
        statement_kind="unresolved", detail=notice)])
    result = store.ask("What is the decided snack budget?")
    assert result["status"] == "unverified"
    listing = store.ask("What remains undecided?")
    assert listing["status"] == "answered"
    assert "snack budget" in listing["answer"].casefold()


# --- Fix 5: photo ranking ---------------------------------------------------

def test_photo_ranking_puts_question_match_first(tmp_path):
    store = Store(tmp_path / "rank.sqlite3")
    add_image(store, "aaa_stage.jpg", "Bicycle parking near the festival stage")
    add_image(store, "zzz_bike.jpg", "A red bicycle leaning on a fence")
    result = store.ask("show photos of the red bicycle")
    assert result["status"] == "answered"
    assert result["photos"][0]["file"] == "/src/zzz_bike.jpg"

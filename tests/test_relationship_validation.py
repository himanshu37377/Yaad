"""Evidence-verified relationships: regression suite.

A relationship between memory records (conflict, supersession,
duplicate/identity) is accepted only when its evidence establishes that the
records concern the same underlying subject or event and that the proposed
relationship is supported. Matching dates, amounts, or keywords alone prove
nothing; an excerpt that merely repeats the other record's claim establishes
no independent relationship.

Only validated conflict relationships may influence answer synthesis: an
uncertain or invalid relationship preserves its evidence, marks nothing,
overrides no baseline answer or abstention, and exposes its validation
status. Historical and superseded evidence stays available for history
queries but never becomes current evidence silently.

Deterministic fixtures on disposable databases; no model calls, no
production database contact.
"""
from test_memory_answers import Store, candidate

from yaad.config import Settings
from yaad.memory.records import (
    apply_supersession_links,
    conflict_partners,
    find_duplicate_candidates,
    mark_conflicts,
    mark_stale,
)
from yaad.memory.relationships import validate_relationship


def fee_store(path):
    """Two events, same key phrase, different amounts (G3 shape)."""
    store = Store(path)
    store.add_file("autumn.txt", [("ev_afee", "Autumn Fest registration fee is Rs 200 per person")])
    store.add_file("spring.txt", [("ev_sfee", "Spring Fest registration fee is Rs 100 per person")])
    store.add_records("run-1", [
        candidate("Autumn Fest registration fee", "registration fee",
                  [("ev_afee", "Autumn Fest registration fee is Rs 200")],
                  record_type="decision", status="decided", date_value=None, date_raw=None),
        candidate("Spring Fest registration fee", "registration fee",
                  [("ev_sfee", "Spring Fest registration fee is Rs 100")],
                  record_type="decision", status="decided", date_value=None, date_raw=None),
    ])
    return store


def same_event_fee_store(path):
    """Two sources, one event, rival amounts: a genuine conflict."""
    store = Store(path)
    store.add_file("a.txt", [("ev_a", "Autumn Fest registration fee is Rs 150 per person")])
    store.add_file("b.txt", [("ev_b", "Autumn Fest registration fee is Rs 200 per person")])
    store.add_records("run-1", [
        candidate("Autumn Fest registration fee", "registration fee",
                  [("ev_a", "Autumn Fest registration fee is Rs 150")],
                  record_type="decision", status="decided", date_value=None, date_raw=None),
        candidate("Autumn Fest registration fee", "registration fee",
                  [("ev_b", "Autumn Fest registration fee is Rs 200")],
                  record_type="decision", status="decided", date_value=None, date_raw=None),
    ])
    return store


def room_store(path, run_id="run-1"):
    """A confirmed venue decision with a superseding replacement available."""
    store = Store(path)
    store.add_file("a.txt", [("ev_old", "The workshop is in Room A")])
    store.add_file("b.txt", [("ev_new", "Correction: the workshop moved to Room B")])
    store.add_records(run_id, [
        candidate("Workshop Room A", "workshop room", [("ev_old", "workshop is in Room A")],
                  record_type="decision", status="decided", date_value=None, date_raw=None),
    ])
    return store


def replacement_candidate():
    return candidate(
        "Workshop Room B", "workshop room moved",
        [("ev_new", "workshop moved to Room B")], detail="Workshop Room B",
        record_type="decision", status="decided", date_value=None, date_raw=None,
        evidence=[{"evidence_id": "ev_new", "role": "supports",
                   "excerpt": "workshop moved to Room B"},
                  {"evidence_id": "ev_old", "role": "supersedes",
                   "excerpt": "workshop is in Room A"}])


# --- Scenario 1: different events, different amounts -> no conflict ---

def test_different_events_with_different_amounts_do_not_conflict(tmp_path):
    store = fee_store(tmp_path / "s1.sqlite3")
    with store.db.transaction() as con:
        rejected: list = []
        assert mark_conflicts(con, store.folder_id, rejected=rejected) == []
        assert len(rejected) == 1
        assert rejected[0]["verdict"] == "contradicted"
        assert "subject" in " ".join(rejected[0]["reasons"])
    with store.db.connect() as con:
        assert {row["status"] for row in con.execute("SELECT status FROM memory_records")} == {"decided"}
        first = con.execute("SELECT id FROM memory_records ORDER BY id LIMIT 1").fetchone()["id"]
        assert conflict_partners(con, store.folder_id, first) == []


def test_validator_contradicts_different_event_pair(tmp_path):
    store = fee_store(tmp_path / "s1v.sqlite3")
    with store.db.connect() as con:
        left, right = [row["id"] for row in con.execute("SELECT id FROM memory_records ORDER BY id")]
        verdict = validate_relationship(con, "conflict", left, right)
    assert verdict["verdict"] == "contradicted"
    assert verdict["kind"] == "conflict"
    assert verdict["reasons"]
    assert "autumn" in verdict["details"]["left_markers"]
    assert "spring" in verdict["details"]["right_markers"]


def test_one_sided_event_mention_is_ambiguous_not_supported(tmp_path):
    """Real-G3 shape: one side names an event the other never establishes."""
    store = Store(tmp_path / "s1b.sqlite3")
    store.add_file("a.txt", [("ev_a", "Updated notice: the registration fee is Rs 200")])
    store.add_file("b.txt", [("ev_b", "Spring Fest registration fee is Rs 100")])
    store.add_records("run-1", [
        candidate("Updated registration fee", "registration fee",
                  [("ev_a", "registration fee is Rs 200")],
                  record_type="decision", status="decided", date_value=None, date_raw=None),
        candidate("Spring fee", "registration fee",
                  [("ev_b", "Spring Fest registration fee is Rs 100")],
                  record_type="decision", status="decided", date_value=None, date_raw=None),
    ])
    with store.db.transaction() as con:
        assert mark_conflicts(con, store.folder_id) == []
        left, right = [row["id"] for row in con.execute("SELECT id FROM memory_records ORDER BY id")]
        assert validate_relationship(con, "conflict", left, right)["verdict"] == "ambiguous"
    with store.db.connect() as con:
        assert {row["status"] for row in con.execute("SELECT status FROM memory_records")} == {"decided"}


# --- Scenario 2: two sources, same event, different amounts -> conflict ---

def test_same_event_rival_amounts_validate_as_conflict(tmp_path):
    store = same_event_fee_store(tmp_path / "s2.sqlite3")
    with store.db.transaction() as con:
        pairs = mark_conflicts(con, store.folder_id)
        assert len(pairs) == 1
        left, right = [row["id"] for row in con.execute("SELECT id FROM memory_records ORDER BY id")]
        assert set(pairs[0]) == {left, right}
        verdict = validate_relationship(con, "conflict", left, right)
        assert verdict["verdict"] == "supported"
    with store.db.connect() as con:
        assert {row["status"] for row in con.execute("SELECT status FROM memory_records")} == {"conflicted"}
        assert [p["id"] for p in conflict_partners(con, store.folder_id, left)] == [right]


# --- Scenario 3: later confirmed decision supersedes earlier one ---

def test_later_confirmed_decision_supersedes_earlier(tmp_path):
    store = room_store(tmp_path / "s3.sqlite3", run_id="run-1")
    store.add_records("run-2", [replacement_candidate()])
    with store.db.transaction() as con:
        rows = {row["title"]: row for row in con.execute("SELECT id,title,status FROM memory_records")}
        verdict = validate_relationship(con, "supersession",
                                        rows["Workshop Room B"]["id"], rows["Workshop Room A"]["id"])
        assert verdict["verdict"] == "supported"
        rejected: list = []
        stepped = apply_supersession_links(con, store.folder_id, rejected=rejected)
        assert len(stepped) == 1 and rejected == []
        assert con.execute("SELECT status FROM memory_records WHERE id=?",
                           (rows["Workshop Room A"]["id"],)).fetchone()["status"] == "superseded"
    result = store.ask("which room is the workshop in?")
    assert result["status"] == "answered" and "Room B" in result["answer"]
    assert [card["id"] for card in result["evidence"]] == ["ev_new"]


def test_replacement_predating_target_is_rejected(tmp_path):
    store = room_store(tmp_path / "s3b.sqlite3")
    store.add_records("run-1", [replacement_candidate()])
    with store.db.transaction() as con:
        rows = {row["title"]: row["id"] for row in con.execute("SELECT id,title FROM memory_records")}
        new_id, old_id = rows["Workshop Room B"], rows["Workshop Room A"]
        con.execute("UPDATE memory_records SET created_at='2026-01-01 00:00:00' WHERE id=?", (new_id,))
        con.execute("UPDATE memory_records SET created_at='2026-06-01 00:00:00' WHERE id=?", (old_id,))
        verdict = validate_relationship(con, "supersession", new_id, old_id)
        assert verdict["verdict"] == "contradicted"
        assert "predates" in " ".join(verdict["reasons"])
        rejected: list = []
        assert apply_supersession_links(con, store.folder_id, rejected=rejected) == []
        assert any("predates" in entry["reason"] for entry in rejected)
        assert con.execute("SELECT status FROM memory_records WHERE id=?",
                           (old_id,)).fetchone()["status"] == "decided"


# --- Scenario 4: proposal must not supersede a confirmed decision ---

def test_proposal_does_not_supersede_confirmed_decision(tmp_path):
    store = room_store(tmp_path / "s4.sqlite3")
    store.add_file("c.txt", [("ev_maybe", "Workshop might move to Room C")])
    store.add_records("run-1", [
        candidate("Workshop Room C maybe", "workshop room",
                  [("ev_maybe", "Workshop might move to Room C")],
                  detail="Workshop Room C maybe",
                  record_type="commitment", status="open", statement_kind="proposal",
                  date_value=None, date_raw=None,
                  evidence=[{"evidence_id": "ev_maybe", "role": "supports",
                             "excerpt": "Workshop might move to Room C"},
                            {"evidence_id": "ev_old", "role": "supersedes",
                             "excerpt": "workshop is in Room A"}]),
    ])
    with store.db.transaction() as con:
        rows = {row["title"]: row["id"] for row in con.execute("SELECT id,title FROM memory_records")}
        verdict = validate_relationship(con, "supersession",
                                        rows["Workshop Room C maybe"], rows["Workshop Room A"])
        assert verdict["verdict"] == "contradicted"
        assert "proposal" in " ".join(verdict["reasons"])
        rejected: list = []
        assert apply_supersession_links(con, store.folder_id, rejected=rejected) == []
        assert any("proposal" in entry["reason"] for entry in rejected)
        assert con.execute("SELECT status FROM memory_records WHERE id=?",
                           (rows["Workshop Room A"],)).fetchone()["status"] == "decided"
    result = store.ask("which room is the workshop in?")
    assert result["status"] == "answered" and "Room A" in result["answer"]


# --- Scenario 5: matching date/amount alone never links unrelated records ---

def test_matching_amount_with_different_dates_and_subjects_does_not_conflict(tmp_path):
    store = Store(tmp_path / "s5.sqlite3")
    store.add_file("a.txt", [("ev_a", "Autumn Fest budget is Rs 25000, opening November 18")])
    store.add_file("b.txt", [("ev_b", "Spring Fest budget is Rs 25000, opening March 20")])
    store.add_records("run-1", [
        candidate("Autumn Fest budget", "fest budget",
                  [("ev_a", "Autumn Fest budget is Rs 25000, opening November 18")],
                  record_type="decision", status="decided",
                  date_value="2026-11-18", date_raw="November 18"),
        candidate("Spring Fest budget", "fest budget",
                  [("ev_b", "Spring Fest budget is Rs 25000, opening March 20")],
                  record_type="decision", status="decided",
                  date_value="2026-03-20", date_raw="March 20"),
    ])
    with store.db.transaction() as con:
        assert mark_conflicts(con, store.folder_id) == []
    with store.db.connect() as con:
        assert {row["status"] for row in con.execute("SELECT status FROM memory_records")} == {"decided"}


def test_same_date_and_amount_does_not_group_or_hint_distinct_events(tmp_path):
    store = Store(tmp_path / "s5b.sqlite3")
    store.add_file("a.txt", [("ev_a", "Autumn Fest budget Rs 25000 confirmed for November 18")])
    store.add_file("b.txt", [("ev_b", "Spring Fest budget Rs 25000 confirmed for November 18")])
    store.add_records("run-1", [
        candidate("Autumn Fest budget", "fest budget autumn",
                  [("ev_a", "Autumn Fest budget Rs 25000 confirmed for November 18")],
                  detail="Autumn Fest budget Rs 25000",
                  record_type="decision", status="decided",
                  date_value="2026-11-18", date_raw="November 18"),
        candidate("Spring Fest budget", "fest budget spring",
                  [("ev_b", "Spring Fest budget Rs 25000 confirmed for November 18")],
                  detail="Spring Fest budget Rs 25000",
                  record_type="decision", status="decided",
                  date_value="2026-11-18", date_raw="November 18"),
    ])
    with store.db.connect() as con:
        assert find_duplicate_candidates(con, store.folder_id) == []
        left, right = [row["id"] for row in con.execute("SELECT id FROM memory_records ORDER BY id")]
        assert validate_relationship(con, "duplicate", left, right)["verdict"] == "contradicted"
    result = store.ask("what is the fest budget?")
    assert len(result["memory"]["claims"]) == 2
    assert len(result["memory"]["record_ids"]) == 2


# --- Scenario 6: missing or invalid evidence prevents acceptance ---

def test_missing_evidence_prevents_conflict_acceptance(tmp_path):
    store = same_event_fee_store(tmp_path / "s6.sqlite3")
    with store.db.transaction() as con:
        left, right = [row["id"] for row in con.execute("SELECT id FROM memory_records ORDER BY id")]
        con.execute("DELETE FROM units WHERE evidence_id='ev_b'")
        verdict = validate_relationship(con, "conflict", left, right)
        assert verdict["verdict"] == "ambiguous"
        assert "evidence" in " ".join(verdict["reasons"])
        rejected: list = []
        assert mark_conflicts(con, store.folder_id, rejected=rejected) == []
        assert rejected and rejected[0]["verdict"] == "ambiguous"
        assert mark_stale(con, store.folder_id) == [right]
        assert con.execute("SELECT status FROM memory_records WHERE id=?",
                           (left,)).fetchone()["status"] == "decided"


def test_invalid_evidence_prevents_supersession_acceptance(tmp_path):
    store = room_store(tmp_path / "s6b.sqlite3")
    store.add_records("run-1", [replacement_candidate()])
    with store.db.transaction() as con:
        rows = {row["title"]: row["id"] for row in con.execute("SELECT id,title FROM memory_records")}
        # Rewritten source text: the edge's own-evidence check rejects it.
        con.execute("UPDATE units SET text='Rewritten without the claim' WHERE evidence_id='ev_new'")
        rejected: list = []
        assert apply_supersession_links(con, store.folder_id, rejected=rejected) == []
        assert rejected
        assert con.execute("SELECT status FROM memory_records WHERE id=?",
                           (rows["Workshop Room A"],)).fetchone()["status"] == "decided"
        # Deleted source: validation itself is ambiguous.
        con.execute("DELETE FROM units WHERE evidence_id='ev_new'")
        verdict = validate_relationship(con, "supersession",
                                        rows["Workshop Room B"], rows["Workshop Room A"])
        assert verdict["verdict"] == "ambiguous"
        assert "evidence" in " ".join(verdict["reasons"])


def test_identical_statement_cited_twice_is_not_a_conflict(tmp_path):
    store = Store(tmp_path / "s6c.sqlite3")
    store.add_file("a.txt", [("ev_a", "The draft is due soon")])
    store.add_records("run-1", [
        candidate("Draft due March", "draft due",
                  [("ev_a", "draft is due soon")],
                  date_value="2026-03-03", date_raw=None),
        candidate("Draft due April", "draft due",
                  [("ev_a", "draft is due soon")],
                  date_value="2026-04-04", date_raw=None),
    ])
    with store.db.transaction() as con:
        left, right = [row["id"] for row in con.execute("SELECT id FROM memory_records ORDER BY id")]
        verdict = validate_relationship(con, "conflict", left, right)
        assert verdict["verdict"] == "ambiguous"
        assert "identical" in " ".join(verdict["reasons"])
        assert mark_conflicts(con, store.folder_id) == []
        # The records themselves are preserved, unmarked.
        assert {row["status"] for row in con.execute("SELECT status FROM memory_records")} == {"open"}


def test_distinct_statements_from_one_source_still_conflict(tmp_path):
    store = Store(tmp_path / "s6d.sqlite3")
    store.add_file("a.txt", [("ev_a", "Deliver the draft by March 3rd. "
                                      "Update: draft moved to March 10th instead.")])
    store.add_records("run-1", [
        candidate("Draft due", "march draft",
                  [("ev_a", "Deliver the draft by March 3rd")],
                  detail="Draft due March 3rd.",
                  date_value="2026-03-03", date_raw="March 3rd"),
        candidate("Draft due moved", "march draft",
                  [("ev_a", "draft moved to March 10th")],
                  detail="Draft moved.",
                  date_value="2026-03-10", date_raw="March 10th"),
    ])
    with store.db.transaction() as con:
        left, right = [row["id"] for row in con.execute("SELECT id FROM memory_records ORDER BY id")]
        assert validate_relationship(con, "conflict", left, right)["verdict"] == "supported"
        assert len(mark_conflicts(con, store.folder_id)) == 1


# --- Scenario 7: unverified conflicts never override answers or abstentions ---

def test_legacy_false_conflict_does_not_override_valid_answer(tmp_path):
    store = Store(tmp_path / "s7.sqlite3")
    store.add_file("notes.txt", [("ev_a", "Autumn Fest budget is Rs 25000"),
                                 ("ev_b", "Spring Fest budget is Rs 12000"),
                                 ("ev_v", "Spring Fest venue booked for June 5")])
    store.add_records("run-1", [
        candidate("Autumn Fest budget", "fest budget",
                  [("ev_a", "Autumn Fest budget is Rs 25000")],
                  record_type="decision", status="decided", date_value=None, date_raw=None),
        candidate("Spring Fest budget", "fest budget",
                  [("ev_b", "Spring Fest budget is Rs 12000")],
                  record_type="decision", status="decided", date_value=None, date_raw=None),
    ])
    with store.db.transaction() as con:
        # Simulate a stale mark from before validation existed.
        con.execute("UPDATE memory_records SET status='conflicted'")
    result = store.ask("when is Spring Fest venue booked?")
    assert result["status"] == "answered"
    assert result["values"] and result["values"][0]["value"] == "June 5"
    assert result["memory"]["used"] is False
    assert result["memory"]["conflicts"] == []


def test_legacy_false_conflict_leaves_unrelated_abstention_identical(tmp_path):
    left = Store(tmp_path / "s7a.sqlite3")
    left.add_file("a.txt", [("ev_a", "Autumn Fest budget is Rs 25000")])
    left.add_file("b.txt", [("ev_b", "Spring Fest budget is Rs 12000")])
    left.add_records("run-1", [
        candidate("Autumn Fest budget", "fest budget",
                  [("ev_a", "Autumn Fest budget is Rs 25000")],
                  record_type="decision", status="decided", date_value=None, date_raw=None),
        candidate("Spring Fest budget", "fest budget",
                  [("ev_b", "Spring Fest budget is Rs 12000")],
                  record_type="decision", status="decided", date_value=None, date_raw=None),
    ])
    with left.db.transaction() as con:
        con.execute("UPDATE memory_records SET status='conflicted'")
    right = Store(tmp_path / "s7b.sqlite3")
    right.add_file("a.txt", [("ev_a", "Autumn Fest budget is Rs 25000")])
    right.add_file("b.txt", [("ev_b", "Spring Fest budget is Rs 12000")])
    assert left.ask("what color is the bicycle?") == right.ask("what color is the bicycle?")


# --- Scenario 8: a genuine conflict is still surfaced, with validation exposed ---

def test_valid_conflict_still_surfaced_with_validation_exposed(tmp_path):
    store = same_event_fee_store(tmp_path / "s8.sqlite3")
    with store.db.transaction() as con:
        assert len(mark_conflicts(con, store.folder_id)) == 1
    valued = store.ask("what is the Autumn Fest registration fee?")
    assert valued["status"] == "conflict"
    assert {card["id"] for card in valued["evidence"]} == {"ev_a", "ev_b"}
    assert {value["value"] for value in valued["values"]} >= {"Rs 150", "Rs 200"}
    assert len(valued["memory"]["conflicts"]) == 1
    (validation,) = valued["memory"]["conflict_validations"]
    assert validation["verdict"] == "supported"
    assert validation["reasons"]
    assert set(validation["record_ids"]) == set(valued["memory"]["record_ids"])
    described = store.ask("tell me about the Autumn Fest registration")
    assert described["status"] == "conflict"
    assert "disagree" in described["answer"]


# --- Scenario 9: historical evidence is never presented as current ---

def test_historical_evidence_not_presented_as_current(tmp_path):
    store = room_store(tmp_path / "s9.sqlite3")
    store.add_records("run-1", [replacement_candidate()])
    with store.db.transaction() as con:
        assert apply_supersession_links(con, store.folder_id) != []
    current = store.ask("which room is the workshop in?")
    assert current["status"] == "answered"
    assert "Room B" in current["answer"] and "Room A" not in current["answer"]
    assert [card["id"] for card in current["evidence"]] == ["ev_new"]
    history = store.ask("where was the workshop originally?")
    assert history["status"] == "cloud_unavailable"
    old_cards = [card for card in history["evidence"] if card["id"] == "ev_old"]
    assert old_cards and all(card["historical"] is True for card in old_cards)


# --- G3: false conflict must not override an otherwise correct abstention ---

def test_g3_false_conflict_does_not_override_abstention(tmp_path):
    store = Store(tmp_path / "g3.sqlite3")
    store.add_file("autumn.txt", [("ev_afee", "Autumn Fest registration fee is Rs 200 per person")])
    store.add_file("spring.txt", [("ev_sfee", "Spring Fest registration fee is Rs 100 per person"),
                                  ("ev_pub", "Spring Fest publicity handled by volunteers")])
    store.add_records("run-1", [
        candidate("Autumn Fest registration fee", "registration fee",
                  [("ev_afee", "Autumn Fest registration fee is Rs 200")],
                  record_type="decision", status="decided", date_value=None, date_raw=None),
        candidate("Spring Fest registration fee", "registration fee",
                  [("ev_sfee", "Spring Fest registration fee is Rs 100")],
                  record_type="decision", status="decided", date_value=None, date_raw=None),
    ])
    with store.db.transaction() as con:
        assert mark_conflicts(con, store.folder_id) == []
    with store.db.connect() as con:
        assert {row["status"] for row in con.execute("SELECT status FROM memory_records")} == {"decided"}
    result = store.ask("What was the publicity budget for Spring Fest?")
    assert result["status"] != "conflict"
    assert "disagree" not in result["answer"]
    assert result["memory"]["conflicts"] == []


# --- Auditability: rejected relationships are reported, never silent ---

def test_extraction_reports_rejected_conflicts(tmp_path):
    from yaad.memory.extract import run_extraction

    store = Store(tmp_path / "audit.sqlite3")
    store.settings = Settings(db_path=tmp_path / "audit.sqlite3", cloud_enabled=True,
                              model_api_key="stub-key-for-tests")
    store.add_file("autumn.txt", [("ev_afee", "Autumn Fest registration fee is Rs 200 per person")])
    store.add_file("spring.txt", [("ev_sfee", "Spring Fest registration fee is Rs 100 per person")])

    class StubClient:
        last_call = {"provider": "Meta Model API", "model": "muse-spark-1.3", "status_code": 200,
                     "success": True, "duration_ms": 5, "input_tokens": 900,
                     "output_tokens": 300, "cached_tokens": 0}

        def reason_json(self, messages, schema, prompt_id):
            first = candidate("Autumn Fest registration fee", "registration fee",
                              [("ev_afee", "Autumn Fest registration fee is Rs 200")],
                              record_type="decision", status="decided",
                              date_value="", date_raw="", assignee="")
            second = candidate("Spring Fest registration fee", "registration fee",
                               [("ev_sfee", "Spring Fest registration fee is Rs 100")],
                               record_type="decision", status="decided",
                               date_value="", date_raw="", assignee="")
            return {"records": [first, second]}

    summary = run_extraction(store.db, store.folder_id, "run-1", store.settings, StubClient())
    assert summary["conflicts"] == []
    assert len(summary["rejected_conflicts"]) == 1
    assert summary["rejected_conflicts"][0]["verdict"] == "contradicted"
    with store.db.connect() as con:
        assert {row["status"] for row in con.execute("SELECT status FROM memory_records")} == {"decided"}


def test_duplicate_paraphrase_with_shared_evidence_still_validates(tmp_path):
    store = Store(tmp_path / "dup.sqlite3")
    store.add_file("a.txt", [("ev_a", "Venue confirmation due November 10. Venue confirmation deadline November 10.")])
    store.add_records("run-1", [
        candidate("Venue confirmation due", "venue confirmation due",
                  [("ev_a", "Venue confirmation due November 10")],
                  date_value="2026-11-10", date_raw="November 10"),
        candidate("Venue confirmation deadline", "venue confirmation deadline",
                  [("ev_a", "Venue confirmation deadline November 10")],
                  date_value="2026-11-10", date_raw="November 10"),
    ])
    with store.db.connect() as con:
        assert len(find_duplicate_candidates(con, store.folder_id)) == 1
        left, right = [row["id"] for row in con.execute("SELECT id FROM memory_records ORDER BY id")]
        assert validate_relationship(con, "duplicate", left, right)["verdict"] == "supported"
    result = store.ask("when is venue confirmation due?")
    assert result["status"] == "answered"
    assert len(result["values"]) == 1

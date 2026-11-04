"""Temporal direction-aware supersession: regression suite.

An older fact must never supersede a newer one. Supersession order
comes only from explicit evidence -- stored effective times,
sequence markers (original/interim/update/final), or one-sided
change-of-state language -- never from ingestion order (record ids,
created_at), file mtimes, or asserted dates (for date slots the
date IS the contested content). Without an order the pair stays
ambiguous and any rivalry is preserved as conflict.

Live provenance: production budget record 10 (FINAL Rs 28,000,
current) was superseded BY record 9 (March Rs 25,000, stale)
because validation compared created_at, which is equal inside a
batch insert. Venue links 7->6 were rejected for want of
replacement wording the sequential documents never use.

Deterministic fixtures on disposable databases; no model calls.
"""
from test_conversation_learning import record_statuses
from test_memory_answers import Store, candidate

from yaad.memory.auto import learn_from_message
from yaad.memory.records import apply_supersession_links, mark_conflicts
from yaad.memory.relationships import (
    has_transition,
    sequence_rank,
    temporal_order,
    validate_relationship,
)


def _budget_store(path):
    """March-update 25k vs Final-notice 28k: the live inversion shape."""
    store = Store(path)
    store.add_file("update.txt", [("ev_old", "March update: Spring Fest budget of Rs 25,000")])
    store.add_file("final.txt", [("ev_new", "Final notice: Spring Fest budget of Rs 28,000")])
    store.add_records("run-1", [
        candidate("Spring Fest budget Rs 25,000", "spring fest budget",
                  [("ev_old", "March update: Spring Fest budget of Rs 25,000")],
                  detail="March update: Spring Fest budget of Rs 25,000",
                  record_type="decision", status="decided",
                  date_value=None, date_raw=None),
        candidate("Spring Fest budget Rs 28,000", "spring fest budget",
                  [("ev_new", "Final notice: Spring Fest budget of Rs 28,000")],
                  detail="Final notice: Spring Fest budget of Rs 28,000",
                  record_type="decision", status="decided",
                  date_value=None, date_raw=None),
    ])
    return store


def _budget_ids(store):
    with store.db.connect() as con:
        rows = {row["title"]: row["id"] for row in
                con.execute("SELECT id,title FROM memory_records")}
    return {"March budget": rows["Spring Fest budget Rs 25,000"],
            "Final budget": rows["Spring Fest budget Rs 28,000"]}


def _link(store, claimant_title, target_eid, target_excerpt):
    with store.db.transaction() as con:
        cid = con.execute("SELECT id FROM memory_records WHERE title=?",
                          (claimant_title,)).fetchone()["id"]
        con.execute("INSERT INTO memory_record_evidence(record_id,evidence_id,role,excerpt)"
                    " VALUES(?,?,?,?)", (cid, target_eid, "supersedes", target_excerpt))
        return cid


def _ids(store):
    with store.db.connect() as con:
        return {row["title"]: row["id"] for row in
                con.execute("SELECT id,title FROM memory_records")}


# --- ordering helper: effective times ---

def test_effective_times_order_across_formats():
    old = {"effective_time": "2027-03-01"}
    new = {"effective_time": "2027-04-02T00:00:00+00:00"}
    assert temporal_order(new, old) == 1
    assert temporal_order(old, new) == -1
    assert temporal_order(new, dict(new)) is None  # identical moments: unordered
    assert temporal_order({"effective_time": "not-a-date"}, old) is None
    assert temporal_order({}, {}) is None


def test_effective_time_beats_contradicting_markers():
    # Explicit schema timestamps outrank linguistic evidence.
    old = {"effective_time": "2027-03-01"}
    new = {"effective_time": "2027-04-01"}
    assert temporal_order(new, old, "Original plan", "Final notice") == 1
    assert temporal_order(old, new, "Final notice", "Original plan") == -1


# --- ordering helper: sequence markers ---

def test_marker_ranks_order_generations():
    assert sequence_rank("Original plan budget") == 0
    assert sequence_rank("Interim revision") == 2  # max wins: revision outranks interim
    assert sequence_rank("March update") == 2
    assert sequence_rank("Final April notice") == 3
    assert sequence_rank("plain statement") == -1
    assert temporal_order({}, {}, "Final notice", "March update") == 1
    assert temporal_order({}, {}, "March update", "Final notice") == -1
    assert temporal_order({}, {}, "Final A", "Final B") is None
    assert temporal_order({}, {}, "plain A", "plain B") is None


def test_marker_matching_is_whole_word():
    assert sequence_rank("we will finalize the venue") == -1
    assert sequence_rank("the updater ran nightly") == -1
    assert sequence_rank("an update was posted") == 2


# --- ordering helper: transitions + forbidden sources ---

def test_one_sided_transition_licenses_newness():
    assert has_transition("workshop moved to Room B")
    assert has_transition("Correction: the budget is Rs 30,000")
    assert not has_transition("workshop is in Room A")
    assert has_transition("please move the boxes") is False  # "move" bare: no
    assert temporal_order({}, {}, "venue changed to Grand Arena",
                          "venue is Riverside Hall") == 1
    assert temporal_order({}, {}, "venue is Riverside Hall",
                          "venue changed to Grand Arena") == -1
    assert temporal_order({}, {}, "moved to A", "moved to B") is None


def test_confirmed_over_estimate_orders_but_not_within_class():
    assert temporal_order({}, {}, "cost is 18000 INR, confirmed",
                          "cost is 12000 INR, quoted") == 1
    assert temporal_order({}, {}, "cost is 12000 INR, quoted",
                          "cost is 18000 INR, confirmed") == -1
    assert temporal_order({}, {}, "confirmed cost A",
                          "confirmed cost B") is None
    assert temporal_order({}, {}, "quoted cost A",
                          "quoted cost B") is None
    assert temporal_order({}, {}, "confirmed cost A",
                          "plain cost B") is None


def test_ingestion_and_asserted_dates_never_order():
    claimant = {"id": 99, "created_at": "2026-01-01 00:00:00",
                "date_value": "2027-04-09"}
    target = {"id": 3, "created_at": "2026-06-01 00:00:00",
              "date_value": "2027-03-01"}
    # Every forbidden signal disagrees; only explicit order counts: none here.
    assert temporal_order(claimant, target, "plain A", "plain B") is None


# --- validate: inversion rejected, valid direction accepted ---

def test_older_claimant_is_rejected_as_predating(tmp_path):
    store = _budget_store(tmp_path / "inv.sqlite3")
    ids = _budget_ids(store)
    with store.db.connect() as con:
        verdict = validate_relationship(con, "supersession",
                                        ids["March budget"], ids["Final budget"])
    assert verdict["verdict"] == "contradicted"
    assert "predates" in " ".join(verdict["reasons"])
    _link(store, "Spring Fest budget Rs 25,000", "ev_new",
          "Final notice: Spring Fest budget of Rs 28,000")
    with store.db.transaction() as con:
        rejected: list = []
        assert apply_supersession_links(con, store.folder_id, rejected=rejected) == []
        assert any("predates" in entry["reason"] for entry in rejected)
    with store.db.connect() as con:
        assert {row["status"] for row in
                con.execute("SELECT status FROM memory_records")} == {"decided"}


def test_newer_claimant_supersedes_with_provenance(tmp_path):
    store = _budget_store(tmp_path / "val.sqlite3")
    ids = _budget_ids(store)
    with store.db.connect() as con:
        verdict = validate_relationship(con, "supersession",
                                        ids["Final budget"], ids["March budget"])
    assert verdict["verdict"] == "supported"
    _link(store, "Spring Fest budget Rs 28,000", "ev_old",
          "March update: Spring Fest budget of Rs 25,000")
    with store.db.transaction() as con:
        stepped = apply_supersession_links(con, store.folder_id)
        assert stepped == [ids["March budget"]]
    with store.db.connect() as con:
        rows = {row["title"]: row["status"] for row in
                con.execute("SELECT title,status FROM memory_records")}
        assert rows == {"Spring Fest budget Rs 25,000": "superseded",
                        "Spring Fest budget Rs 28,000": "decided"}
        transitions = con.execute(
            "SELECT transition,related_record_id FROM memory_transitions WHERE record_id=?",
            (ids["March budget"],)).fetchall()
        assert any(row["transition"] == "superseded"
                   and row["related_record_id"] == ids["Final budget"]
                   for row in transitions)
        # History stays inspectable: target unit and excerpts intact.
        assert con.execute("SELECT COUNT(*) c FROM units WHERE evidence_id='ev_old'"
                           ).fetchone()["c"] == 1
        assert con.execute("SELECT COUNT(*) c FROM memory_record_evidence "
                           "WHERE record_id=? AND role='supports'",
                           (ids["March budget"],)).fetchone()["c"] == 1


def test_successive_updates_settle_in_one_sweep(tmp_path):
    store = Store(tmp_path / "chain.sqlite3")
    store.add_file("o.txt", [("ev_o", "Original plan: Spring Fest budget of Rs 20,000")])
    store.add_file("i.txt", [("ev_i", "Interim: Spring Fest budget of Rs 25,000")])
    store.add_file("f.txt", [("ev_f", "Final notice: Spring Fest budget of Rs 28,000")])
    store.add_records("run-1", [
        candidate("Spring Fest budget Rs 20,000", "spring fest budget",
                  [("ev_o", "Original plan: Spring Fest budget of Rs 20,000")],
                  detail="Original plan: Spring Fest budget of Rs 20,000",
                  record_type="decision", status="decided",
                  date_value=None, date_raw=None),
        candidate("Spring Fest budget Rs 25,000", "spring fest budget",
                  [("ev_i", "Interim: Spring Fest budget of Rs 25,000")],
                  detail="Interim: Spring Fest budget of Rs 25,000",
                  record_type="decision", status="decided",
                  date_value=None, date_raw=None),
        candidate("Spring Fest budget Rs 28,000", "spring fest budget",
                  [("ev_f", "Final notice: Spring Fest budget of Rs 28,000")],
                  detail="Final notice: Spring Fest budget of Rs 28,000",
                  record_type="decision", status="decided",
                  date_value=None, date_raw=None),
    ])
    _link(store, "Spring Fest budget Rs 25,000", "ev_o",
          "Original plan: Spring Fest budget of Rs 20,000")
    _link(store, "Spring Fest budget Rs 28,000", "ev_i",
          "Interim: Spring Fest budget of Rs 25,000")
    with store.db.transaction() as con:
        apply_supersession_links(con, store.folder_id)
    with store.db.connect() as con:
        rows = {row["title"]: row["status"] for row in
                con.execute("SELECT title,status FROM memory_records")}
    assert rows == {"Spring Fest budget Rs 20,000": "superseded",
                    "Spring Fest budget Rs 25,000": "superseded",
                    "Spring Fest budget Rs 28,000": "decided"}


# --- validate: unordered pairs preserve conflict ---

def test_venue_name_chain_resolves_with_temporal_order(tmp_path):
    # Live 7->6 shape: Final-notice Crystal Pavilion over March-update
    # Grand Arena. "Pavilion" is a venue type noun (same class as
    # hall/arena); without it the rivalry detector cannot see the
    # competing values and subject identity vetoes the link.
    store = Store(tmp_path / "venue.sqlite3")
    store.add_file("u.txt", [("ev_u", "March update: Spring Fest venue is Grand Arena")])
    store.add_file("f.txt", [("ev_f", "Final notice: Spring Fest venue is Crystal Pavilion")])
    store.add_records("run-1", [
        candidate("Spring Fest venue Grand Arena", "spring fest venue grand arena",
                  [("ev_u", "March update: Spring Fest venue is Grand Arena")],
                  detail="March update: Spring Fest venue is Grand Arena",
                  record_type="decision", status="decided",
                  date_value=None, date_raw=None),
        candidate("Spring Fest venue Crystal Pavilion", "spring fest venue crystal pavilion",
                  [("ev_f", "Final notice: Spring Fest venue is Crystal Pavilion")],
                  detail="Final notice: Spring Fest venue is Crystal Pavilion",
                  record_type="decision", status="decided",
                  date_value=None, date_raw=None),
    ])
    ids = _ids(store)
    with store.db.connect() as con:
        verdict = validate_relationship(
            con, "supersession", ids["Spring Fest venue Crystal Pavilion"],
            ids["Spring Fest venue Grand Arena"])
        assert verdict["verdict"] == "supported"
        assert verdict["details"]["textual_rivalry"]["contested"] == 1
    _link(store, "Spring Fest venue Crystal Pavilion", "ev_u",
          "March update: Spring Fest venue is Grand Arena")
    with store.db.transaction() as con:
        assert apply_supersession_links(con, store.folder_id) == [
            ids["Spring Fest venue Grand Arena"]]


def test_same_date_rivals_stay_conflict(tmp_path):
    store = Store(tmp_path / "samedate.sqlite3")
    store.add_file("a.txt", [("ev_a", "Spring Fest venue costs Rs 8,000 for April 9th")])
    store.add_file("b.txt", [("ev_b", "Spring Fest venue costs Rs 9,500 for April 9th")])
    store.add_records("run-1", [
        candidate("Spring Fest venue cost A", "spring fest venue cost",
                  [("ev_a", "Spring Fest venue costs Rs 8,000 for April 9th")],
                  detail="Spring Fest venue costs Rs 8,000 for April 9th",
                  record_type="decision", status="decided",
                  date_value="2027-04-09", date_raw="April 9th"),
        candidate("Spring Fest venue cost B", "spring fest venue cost",
                  [("ev_b", "Spring Fest venue costs Rs 9,500 for April 9th")],
                  detail="Spring Fest venue costs Rs 9,500 for April 9th",
                  record_type="decision", status="decided",
                  date_value="2027-04-09", date_raw="April 9th"),
    ])
    ids = _ids(store)
    with store.db.connect() as con:
        verdict = validate_relationship(con, "supersession",
                                        ids["Spring Fest venue cost A"],
                                        ids["Spring Fest venue cost B"])
        assert verdict["verdict"] == "ambiguous"
        assert validate_relationship(con, "conflict",
                                     ids["Spring Fest venue cost A"],
                                     ids["Spring Fest venue cost B"])["verdict"] == "supported"
    with store.db.transaction() as con:
        assert len(mark_conflicts(con, store.folder_id)) == 1
    with store.db.connect() as con:
        assert {row["status"] for row in
                con.execute("SELECT status FROM memory_records")} == {"conflicted"}


def test_markerless_plain_states_never_supersede(tmp_path):
    store = Store(tmp_path / "plain.sqlite3")
    store.add_file("a.txt", [("ev_a", "Autumn Fest registration fee is Rs 150 per person")])
    store.add_file("b.txt", [("ev_b", "Autumn Fest registration fee is Rs 200 per person")])
    store.add_records("run-1", [
        candidate("Autumn fee A", "registration fee",
                  [("ev_a", "Autumn Fest registration fee is Rs 150")],
                  detail="Autumn Fest registration fee is Rs 150",
                  record_type="decision", status="decided",
                  date_value=None, date_raw=None),
        candidate("Autumn fee B", "registration fee",
                  [("ev_b", "Autumn Fest registration fee is Rs 200")],
                  detail="Autumn Fest registration fee is Rs 200",
                  record_type="decision", status="decided",
                  date_value=None, date_raw=None),
    ])
    ids = _ids(store)
    with store.db.connect() as con:
        verdict = validate_relationship(con, "supersession",
                                        ids["Autumn fee B"], ids["Autumn fee A"])
        assert verdict["verdict"] == "ambiguous"
        assert "order" in " ".join(verdict["reasons"])
    _link(store, "Autumn fee B", "ev_a", "Autumn Fest registration fee is Rs 150")
    with store.db.transaction() as con:
        assert apply_supersession_links(con, store.folder_id) == []
        assert len(mark_conflicts(con, store.folder_id)) == 1


def test_explicit_correction_supersedes_without_dates(tmp_path):
    store = Store(tmp_path / "corr.sqlite3")
    store.add_file("a.txt", [("ev_a", "Spring Fest budget is Rs 25,000")])
    store.add_file("b.txt", [("ev_b", "Correction: Spring Fest budget is Rs 30,000")])
    store.add_records("run-1", [
        candidate("Spring Fest budget", "spring fest budget",
                  [("ev_a", "Spring Fest budget is Rs 25,000")],
                  detail="Spring Fest budget is Rs 25,000",
                  record_type="decision", status="decided",
                  date_value=None, date_raw=None),
        candidate("Spring Fest budget correction", "spring fest budget",
                  [("ev_b", "Spring Fest budget is Rs 30,000")],
                  detail="Correction: Spring Fest budget is Rs 30,000",
                  record_type="decision", status="decided",
                  date_value=None, date_raw=None),
    ])
    ids = _ids(store)
    with store.db.connect() as con:
        verdict = validate_relationship(con, "supersession",
                                        ids["Spring Fest budget correction"],
                                        ids["Spring Fest budget"])
    assert verdict["verdict"] == "supported"
    _link(store, "Spring Fest budget correction", "ev_a",
          "Spring Fest budget is Rs 25,000")
    with store.db.transaction() as con:
        assert apply_supersession_links(con, store.folder_id) == [ids["Spring Fest budget"]]


def test_out_of_order_ingestion_cannot_invert(tmp_path):
    # Newer-fact record ingested FIRST (lower id, earlier created_at);
    # ingestion order must not decide supersession either way.
    store = Store(tmp_path / "ooo.sqlite3")
    store.add_file("f.txt", [("ev_new", "Final notice: Spring Fest budget of Rs 28,000")])
    store.add_file("o.txt", [("ev_old", "March update: Spring Fest budget of Rs 25,000")])
    store.add_records("run-1", [
        candidate("Spring Fest budget Rs 28,000", "spring fest budget",
                  [("ev_new", "Final notice: Spring Fest budget of Rs 28,000")],
                  detail="Final notice: Spring Fest budget of Rs 28,000",
                  record_type="decision", status="decided",
                  date_value=None, date_raw=None),
    ])
    store.add_records("run-2", [
        candidate("Spring Fest budget Rs 25,000", "spring fest budget",
                  [("ev_old", "March update: Spring Fest budget of Rs 25,000")],
                  detail="March update: Spring Fest budget of Rs 25,000",
                  record_type="decision", status="decided",
                  date_value=None, date_raw=None),
    ])
    ids = _ids(store)
    with store.db.transaction() as con:
        con.execute("UPDATE memory_records SET created_at='2026-01-01 00:00:00' "
                    "WHERE title='Spring Fest budget Rs 28,000'")
        con.execute("UPDATE memory_records SET created_at='2026-06-01 00:00:00' "
                    "WHERE title='Spring Fest budget Rs 25,000'")
    with store.db.connect() as con:
        inverted = validate_relationship(con, "supersession",
                                         ids["Spring Fest budget Rs 25,000"],
                                         ids["Spring Fest budget Rs 28,000"])
        assert inverted["verdict"] == "contradicted"
        assert "predates" in " ".join(inverted["reasons"])
        ordered = validate_relationship(con, "supersession",
                                        ids["Spring Fest budget Rs 28,000"],
                                        ids["Spring Fest budget Rs 25,000"])
        assert ordered["verdict"] == "supported"


def test_conversation_correction_supersedes_document(tmp_path):
    store = Store(tmp_path / "conv.sqlite3")
    store.add_file("plan.txt", [("ev_doc", "The budget is Rs 25,000")])
    store.add_records("run-1", [
        candidate("Budget", "budget", [("ev_doc", "budget is Rs 25,000")],
                  detail="The budget is Rs 25,000",
                  record_type="decision", status="decided",
                  date_value=None, date_raw=None),
    ])
    outcome = learn_from_message(store.db, store.settings, store.folder_id,
                                 "Correction: the budget is Rs 30,000.")
    assert outcome["action"] == "superseded"
    statuses = record_statuses(store.db)
    assert statuses[outcome["rival_id"]] == "superseded"
    with store.db.connect() as con:
        origin = con.execute("SELECT origin FROM memory_records WHERE id=?",
                             (outcome["record_id"],)).fetchone()["origin"]
    assert origin == "conversation"


def test_stored_effective_time_orders_unmarked_pair(tmp_path):
    store = Store(tmp_path / "eff.sqlite3")
    store.add_file("a.txt", [("ev_a", "Spring Fest budget is Rs 25,000")])
    store.add_file("b.txt", [("ev_b", "Spring Fest budget is Rs 28,000")])
    store.add_records("run-1", [
        candidate("Spring Fest budget A", "spring fest budget",
                  [("ev_a", "Spring Fest budget is Rs 25,000")],
                  detail="Spring Fest budget is Rs 25,000",
                  record_type="decision", status="decided",
                  date_value=None, date_raw=None),
        candidate("Spring Fest budget B", "spring fest budget",
                  [("ev_b", "Spring Fest budget is Rs 28,000")],
                  detail="Spring Fest budget is Rs 28,000",
                  record_type="decision", status="decided",
                  date_value=None, date_raw=None),
    ])
    ids = _ids(store)
    with store.db.transaction() as con:
        con.execute("UPDATE memory_records SET effective_time='2027-03-01' WHERE id=?",
                    (ids["Spring Fest budget A"],))
        con.execute("UPDATE memory_records SET effective_time='2027-04-01' WHERE id=?",
                    (ids["Spring Fest budget B"],))
    with store.db.connect() as con:
        assert validate_relationship(con, "supersession",
                                     ids["Spring Fest budget B"],
                                     ids["Spring Fest budget A"])["verdict"] == "supported"
        assert validate_relationship(con, "supersession",
                                     ids["Spring Fest budget A"],
                                     ids["Spring Fest budget B"])["verdict"] == "contradicted"

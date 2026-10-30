"""Venue supersession behavior with model-shaped candidates.

These pin the acceptance criterion: supersede when explicit evidence
supports it, retain the conflict otherwise. They simulate extraction
outputs deterministically; live link-emission rates are an evaluation
matter, not a unit-test claim.
"""
from test_memory_answers import Store, candidate
from yaad.memory.records import apply_supersession_links, mark_conflicts


def _store(path, files, records):
    store = Store(path)
    for name, units in files:
        store.add_file(name, units)
    store.add_records("run-1", records)
    return store


def _venue(title, key, eid, excerpt, detail, kind="explicit", status="decided",
           record_type="decision"):
    return candidate(title, key, [(eid, excerpt)], record_type=record_type,
                     status=status, date_value=None, date_raw=None,
                     statement_kind=kind, detail=detail)


def _link(store, claimant, target_eid, excerpt):
    with store.db.transaction() as con:
        con.execute("INSERT INTO memory_record_evidence(record_id,evidence_id,role,excerpt)"
                    " VALUES(?,?, 'supersedes', ?)", (claimant, target_eid, excerpt))
        return apply_supersession_links(con, store.folder_id)


def _statuses(store):
    with store.db.connect() as con:
        return {r["id"]: r["status"] for r in
                con.execute("SELECT id,status FROM memory_records ORDER BY id")}


def test_clear_venue_replacement_supersedes(tmp_path):
    store = _store(tmp_path / "v1.sqlite3",
                   [("a.txt", [("ev_a", "Spring Fest venue is Riverside Hall, confirmed by committee")]),
                    ("b.txt", [("ev_b", "Spring Fest final confirmation: the venue is Grand Arena")])],
                   [_venue("Venue Riverside", "spring fest venue", "ev_a",
                           "Spring Fest venue is Riverside Hall",
                           "Spring Fest venue is Riverside Hall, confirmed by committee"),
                    _venue("Venue Grand Arena", "fest venue final", "ev_b",
                           "Spring Fest final confirmation: the venue is Grand Arena",
                           "Spring Fest final confirmation: the venue is Grand Arena")])
    assert _link(store, 2, "ev_a", "Spring Fest venue is Riverside Hall") == [1]
    assert _statuses(store) == {1: "superseded", 2: "decided"}
    result = store.ask("What is the Spring Fest venue?")
    assert result["status"] == "answered" and "Grand Arena" in result["answer"]


def test_proposed_venue_never_supersedes(tmp_path):
    store = _store(tmp_path / "v2.sqlite3",
                   [("a.txt", [("ev_a", "Spring Fest venue is Riverside Hall, confirmed by committee")]),
                    ("b.txt", [("ev_b", "Proposal: move Spring Fest venue to Grand Arena")])],
                   [_venue("Venue Riverside", "spring fest venue", "ev_a",
                           "Spring Fest venue is Riverside Hall",
                           "Spring Fest venue is Riverside Hall, confirmed by committee"),
                    _venue("Proposed Grand Arena", "spring fest venue", "ev_b",
                           "Proposal: move Spring Fest venue to Grand Arena",
                           "Proposal: move Spring Fest venue to Grand Arena",
                           kind="proposal", status="open", record_type="commitment")])
    # commitment-type proposals are the valid shape; a decision-shaped
    # proposal candidate is rejected at validation, so model the link
    # attempt directly: even with a link row, validation must refuse.
    with store.db.transaction() as con:
        con.execute("INSERT INTO memory_record_evidence(record_id,evidence_id,role,excerpt)"
                    " VALUES(2,'ev_a','supersedes','Spring Fest venue is Riverside Hall')")
        assert apply_supersession_links(con, store.folder_id) == []
    assert _statuses(store)[1] == "decided"


def test_competing_venues_without_resolution_stay_conflicted(tmp_path):
    store = _store(tmp_path / "v3.sqlite3",
                   [("a.txt", [("ev_a", "Council booked Riverside Hall for Spring Fest")]),
                    ("b.txt", [("ev_b", "Council booked Grand Arena for Spring Fest on March 3rd")])],
                   [_venue("Venue Riverside", "spring fest", "ev_a",
                           "Council booked Riverside Hall for Spring Fest",
                           "Council booked Riverside Hall for Spring Fest"),
                    candidate("Venue Grand Arena", "spring fest",
                              [("ev_b", "Council booked Grand Arena for Spring Fest on March 3rd")],
                              record_type="decision", status="decided",
                              date_value="2027-03-03", date_raw="March 3rd",
                              detail="Council booked Grand Arena for Spring Fest")])
    with store.db.transaction() as con:
        pairs = mark_conflicts(con, store.folder_id)
    assert pairs, "competing confirmed venues must at least conflict"
    result = store.ask("What is the Spring Fest venue?")
    assert result["status"] == "conflict"


def test_multi_step_venue_chain_closes_transitively(tmp_path):
    store = _store(tmp_path / "v4.sqlite3",
                   [("a.txt", [("ev_a", "Spring Fest venue is Riverside Hall, confirmed by committee")]),
                    ("b.txt", [("ev_b", "Spring Fest venue moved to Grand Arena, confirmed by committee")]),
                    ("c.txt", [("ev_c", "Spring Fest final confirmation: the venue is Crystal Pavilion")])],
                   [_venue("Venue Riverside", "spring fest venue", "ev_a",
                           "Spring Fest venue is Riverside Hall",
                           "Spring Fest venue is Riverside Hall, confirmed by committee"),
                    _venue("Venue Grand Arena", "fest venue moved", "ev_b",
                           "Spring Fest venue moved to Grand Arena",
                           "Spring Fest venue moved to Grand Arena, confirmed by committee"),
                    _venue("Venue Crystal Pavilion", "fest venue final", "ev_c",
                           "Spring Fest final confirmation: the venue is Crystal Pavilion",
                           "Spring Fest final confirmation: the venue is Crystal Pavilion")])
    with store.db.transaction() as con:
        con.execute("INSERT INTO memory_record_evidence(record_id,evidence_id,role,excerpt)"
                    " VALUES(2,'ev_a','supersedes','Spring Fest venue is Riverside Hall')")
        con.execute("INSERT INTO memory_record_evidence(record_id,evidence_id,role,excerpt)"
                    " VALUES(3,'ev_b','supersedes','Spring Fest venue moved to Grand Arena')")
        mark_conflicts(con, store.folder_id)
        stepped = apply_supersession_links(con, store.folder_id)
    assert set(stepped) == {1, 2}
    assert _statuses(store) == {1: "superseded", 2: "superseded", 3: "decided"}


def test_ambiguous_venue_identity_retains_conflict(tmp_path):
    # Same venue words, different fests: no rivalry, no supersession.
    store = _store(tmp_path / "v5.sqlite3",
                   [("a.txt", [("ev_a", "Spring Fest venue is Riverside Hall")]),
                    ("b.txt", [("ev_b", "Autumn Fest venue is Riverside Hall")])],
                   [_venue("Spring venue Riverside", "spring fest venue", "ev_a",
                           "Spring Fest venue is Riverside Hall",
                           "Spring Fest venue is Riverside Hall"),
                    _venue("Autumn venue Riverside", "autumn fest venue", "ev_b",
                           "Autumn Fest venue is Riverside Hall",
                           "Autumn Fest venue is Riverside Hall")])
    result = store.ask("What is the Spring Fest venue?")
    assert result["status"] == "answered" and "Riverside Hall" in result["answer"]
    assert _statuses(store) == {1: "decided", 2: "decided"}

"""Competing textual claims: venues, people, and names beyond date/amount slots.

Rivalry detection used to cover stored date/amount slots only, so two
records asserting different venues for the same event were synthesized
into one settled "answered" response. These tests pin the conservative
fix: same event + same attribute dimension + disjoint proper-noun values
with independent evidence validates as a conflict; anything ambiguous
stays out of both conflict marking and settled synthesis.

Deterministic fixtures on disposable databases; no model calls, no
production database contact.
"""
from test_claim_relevance import assert_units_identical
from test_memory_answers import Store, candidate
from yaad.memory.records import apply_supersession_links, mark_conflicts
from yaad.memory.relationships import SUPPORTED, validate_relationship


def venue_pair_store(path):
    """Same event, competing venues. R2 carries a one-sided date so the
    two records keep distinct keys without asserting rival slot values."""
    store = Store(path)
    store.add_file("a.txt", [("ev_a", "Council booked Riverside Hall for Spring Fest")])
    store.add_file("b.txt", [("ev_b", "Council booked Grand Arena for Spring Fest on March 3rd")])
    store.add_records("run-1", [
        candidate("Council Spring Fest venue Riverside Hall", "spring fest",
                  [("ev_a", "Council booked Riverside Hall for Spring Fest")],
                  record_type="decision", status="decided", date_value=None, date_raw=None,
                  detail="Council booked Riverside Hall for Spring Fest"),
        candidate("Council Spring Fest venue Grand Arena", "spring fest",
                  [("ev_b", "Council booked Grand Arena for Spring Fest on March 3rd")],
                  record_type="decision", status="decided",
                  date_value="2026-03-03", date_raw="March 3rd",
                  detail="Council booked Grand Arena for Spring Fest"),
    ])
    return store


def test_t1_same_event_competing_venues_conflict(tmp_path):
    store = venue_pair_store(tmp_path / "t1.sqlite3")
    with store.db.transaction() as con:
        pairs = [tuple(sorted(pair)) for pair in mark_conflicts(con, store.folder_id)]
        assert pairs == [(1, 2)]
        assert {row["status"] for row in
                con.execute("SELECT status FROM memory_records")} == {"conflicted"}
    result = store.ask("where is Spring Fest?")
    assert result["status"] == "conflict"
    assert "Riverside Hall" in result["answer"] and "Grand Arena" in result["answer"]
    assert {card["file"] for card in result["evidence"]} == {"/src/a.txt", "/src/b.txt"}
    assert len(result["memory"]["conflicts"]) == 1
    (validation,) = result["memory"]["conflict_validations"]
    assert validation["verdict"] == SUPPORTED


def test_t2_different_events_same_venues_no_conflict(tmp_path):
    store = Store(tmp_path / "t2.sqlite3")
    store.add_file("a.txt", [("ev_a", "Council booked Riverside Hall for Spring Fest")])
    store.add_file("b.txt", [("ev_b", "Committee booked Grand Arena for Autumn Meet")])
    store.add_records("run-1", [
        candidate("Council Spring Fest venue Riverside Hall", "spring fest",
                  [("ev_a", "Council booked Riverside Hall for Spring Fest")],
                  record_type="decision", status="decided", date_value=None, date_raw=None,
                  detail="Council booked Riverside Hall for Spring Fest"),
        candidate("Committee Autumn Meet venue Grand Arena", "autumn meet",
                  [("ev_b", "Committee booked Grand Arena for Autumn Meet")],
                  record_type="decision", status="decided", date_value=None, date_raw=None,
                  detail="Committee booked Grand Arena for Autumn Meet"),
    ])
    with store.db.transaction() as con:
        assert mark_conflicts(con, store.folder_id) == []
        assert {row["status"] for row in
                con.execute("SELECT status FROM memory_records")} == {"decided"}
        verdict = validate_relationship(con, "conflict", 1, 2)
        assert verdict["verdict"] != SUPPORTED
    result = store.ask("where is Spring Fest?")
    assert result["status"] != "conflict"
    assert result["memory"]["conflicts"] == []


def test_t3_proposal_does_not_override_confirmation(tmp_path):
    store = Store(tmp_path / "t3.sqlite3")
    store.add_file("a.txt", [("ev_a", "Council booked Riverside Hall for Spring Fest")])
    store.add_file("b.txt", [("ev_b", "Council booked Grand Arena for Spring Fest")])
    store.add_records("run-1", [
        candidate("Council Spring Fest venue Riverside Hall", "spring fest",
                  [("ev_a", "Council booked Riverside Hall for Spring Fest")],
                  record_type="decision", status="decided", date_value=None, date_raw=None,
                  detail="Council booked Riverside Hall for Spring Fest"),
        candidate("Proposal to move Spring Fest to Grand Arena", "spring fest",
                  [("ev_b", "Council booked Grand Arena for Spring Fest")],
                  record_type="open_question", status="open", statement_kind="proposal",
                  date_value=None, date_raw=None,
                  detail="Proposal to move Spring Fest to Grand Arena"),
    ])
    with store.db.transaction() as con:
        assert mark_conflicts(con, store.folder_id) == []
    result = store.ask("where is Spring Fest?")
    assert result["status"] == "answered"
    assert "Riverside Hall" in result["answer"]
    assert "Proposed:" in result["answer"]
    assert result["memory"]["conflicts"] == []


def test_t4_validated_newer_venue_supersedes(tmp_path):
    store = venue_pair_store(tmp_path / "t4.sqlite3")
    with store.db.transaction() as con:
        con.execute("INSERT INTO memory_record_evidence(record_id,evidence_id,role,excerpt)"
                    " VALUES(2,'ev_a','supersedes','Council booked Riverside Hall for Spring Fest')")
        rejected: list = []
        assert apply_supersession_links(con, store.folder_id, rejected=rejected) == [1]
        assert rejected == []
        assert con.execute("SELECT status FROM memory_records WHERE id=1").fetchone()["status"] \
            == "superseded"
    result = store.ask("where is Spring Fest?")
    assert result["status"] == "answered"
    assert "Grand Arena" in result["answer"]
    assert "Riverside Hall" not in result["answer"]


def test_t5_same_venue_different_dates_still_conflict(tmp_path):
    store = Store(tmp_path / "t5.sqlite3")
    store.add_file("a.txt", [("ev_a", "Spring Fest opens March 1st at Riverside Hall")])
    store.add_file("b.txt", [("ev_b", "Spring Fest opens April 2nd at Riverside Hall")])
    store.add_records("run-1", [
        candidate("Spring Fest opens March 1st", "spring fest",
                  [("ev_a", "Spring Fest opens March 1st at Riverside Hall")],
                  record_type="decision", status="decided",
                  date_value="2026-03-01", date_raw="March 1st",
                  detail="Spring Fest opens March 1st at Riverside Hall"),
        candidate("Spring Fest opens April 2nd", "spring fest",
                  [("ev_b", "Spring Fest opens April 2nd at Riverside Hall")],
                  record_type="decision", status="decided",
                  date_value="2026-04-02", date_raw="April 2nd",
                  detail="Spring Fest opens April 2nd at Riverside Hall"),
    ])
    with store.db.transaction() as con:
        pairs = [tuple(sorted(pair)) for pair in mark_conflicts(con, store.folder_id)]
        assert pairs == [(1, 2)]
    result = store.ask("when does Spring Fest open?")
    assert result["status"] == "conflict"


def test_t6_compatible_claims_combine_normally(tmp_path):
    store = Store(tmp_path / "t6.sqlite3")
    store.add_file("a.txt", [("ev_a", "Council booked Riverside Hall for Spring Fest"),
                             ("ev_c", "Doors open at noon on May 5th for Spring Fest")])
    store.add_records("run-1", [
        candidate("Council Spring Fest venue Riverside Hall", "spring fest",
                  [("ev_a", "Council booked Riverside Hall for Spring Fest")],
                  record_type="decision", status="decided", date_value=None, date_raw=None,
                  detail="Council booked Riverside Hall for Spring Fest"),
        candidate("Spring Fest doors open at noon", "spring fest",
                  [("ev_c", "Doors open at noon on May 5th for Spring Fest")],
                  record_type="decision", status="decided",
                  date_value="2026-05-05", date_raw="May 5th",
                  detail="Doors open at noon on May 5th for Spring Fest"),
    ])
    with store.db.transaction() as con:
        assert mark_conflicts(con, store.folder_id) == []
    result = store.ask("spring fest plans?")
    assert result["status"] == "answered"
    assert len(result["memory"]["claims"]) == 2
    assert "Riverside Hall" in result["answer"] and "noon" in result["answer"]


def test_t7_different_entities_no_false_conflict(tmp_path):
    store = Store(tmp_path / "t7.sqlite3")
    store.add_file("a.txt", [("ev_a", "Alice leads the Website Project")])
    store.add_file("b.txt", [("ev_b", "Bob leads the Mobile Project")])
    store.add_records("run-1", [
        candidate("Alice leads Website Project", "Website Project",
                  [("ev_a", "Alice leads the Website Project")],
                  record_type="decision", status="decided", date_value=None, date_raw=None,
                  detail="Alice leads the Website Project"),
        candidate("Bob leads Mobile Project", "Mobile Project",
                  [("ev_b", "Bob leads the Mobile Project")],
                  record_type="decision", status="decided", date_value=None, date_raw=None,
                  detail="Bob leads the Mobile Project"),
    ])
    with store.db.transaction() as con:
        assert mark_conflicts(con, store.folder_id) == []
        verdict = validate_relationship(con, "conflict", 1, 2)
        assert verdict["verdict"] != SUPPORTED
    result = store.ask("who leads the website project?")
    assert result["status"] != "conflict"
    assert result["memory"]["conflicts"] == []


def test_t8_ambiguous_identity_no_forced_conflict(tmp_path):
    store = Store(tmp_path / "t8.sqlite3")
    notice = ("Council is deciding between Riverside Hall and Grand Arena "
              "for Spring Fest, decision due July 7th")
    store.add_file("a.txt", [("ev_a", notice)])
    store.add_records("run-1", [
        candidate("Council Spring Fest venue Riverside Hall", "spring fest",
                  [("ev_a", notice)],
                  record_type="decision", status="decided", date_value=None, date_raw=None,
                  detail="Council booked Riverside Hall for Spring Fest"),
        candidate("Council Spring Fest venue Grand Arena", "spring fest",
                  [("ev_a", notice)],
                  record_type="decision", status="decided",
                  date_value="2026-07-07", date_raw="July 7th",
                  detail="Council booked Grand Arena for Spring Fest"),
    ])
    with store.db.transaction() as con:
        # One statement cited twice is not two independent witnesses.
        assert mark_conflicts(con, store.folder_id) == []
        verdict = validate_relationship(con, "conflict", 1, 2)
        assert verdict["verdict"] != SUPPORTED
    # Ambiguous all the same: the values must be listed with qualification,
    # never settled into one answer and never forced into a conflict.
    result = store.ask("where is Spring Fest?")
    assert result["status"] == "unverified"
    assert result["memory"]["conflicts"] == []
    assert "Riverside Hall" in result["answer"] and "Grand Arena" in result["answer"]


def test_t9_tier_c_safeguards_hold_for_venue_records(tmp_path):
    store = venue_pair_store(tmp_path / "t9.sqlite3")
    twin = Store(tmp_path / "t9t.sqlite3")
    twin.add_file("a.txt", [("ev_a", "Council booked Riverside Hall for Spring Fest")])
    twin.add_file("b.txt", [("ev_b", "Council booked Grand Arena for Spring Fest on March 3rd")])
    question = "Who booked the council hall?"
    result = store.ask(question)
    reference = twin.ask(question)
    assert result["memory"]["claims"] == []
    assert result["memory"]["conflicts"] == []
    assert len(result["memory"]["context_claims"]) == 2
    assert_units_identical(result, reference)


def test_t10_genuine_amount_conflict_unchanged(tmp_path):
    store = Store(tmp_path / "t10.sqlite3")
    store.add_file("a.txt", [("ev_a", "Council approved the library grant of Rs 50000")])
    store.add_file("b.txt", [("ev_b", "Council approved the library grant of Rs 75000")])
    store.add_records("run-1", [
        candidate("Council library grant Rs 50000", "library grant",
                  [("ev_a", "Council approved the library grant of Rs 50000")],
                  record_type="decision", status="decided", date_value=None, date_raw=None,
                  detail="Council approved the library grant of Rs 50000"),
        candidate("Council library grant Rs 75000", "library grant",
                  [("ev_b", "Council approved the library grant of Rs 75000")],
                  record_type="decision", status="decided", date_value=None, date_raw=None,
                  detail="Council approved the library grant of Rs 75000"),
    ])
    with store.db.transaction() as con:
        pairs = [tuple(sorted(pair)) for pair in mark_conflicts(con, store.folder_id)]
        assert pairs == [(1, 2)]
    result = store.ask("what is the library grant amount?")
    assert result["status"] == "conflict"
    assert {value["value"] for value in result["values"]} == {"Rs 50000", "Rs 75000"}


def test_answer_level_unmarked_rivals_still_conflict(tmp_path):
    """Serve-time promotion: even when the sweep never marked the pair,
    incompatible venue values cannot settle into an answered response."""
    store = venue_pair_store(tmp_path / "a1.sqlite3")
    result = store.ask("where is Spring Fest?")
    assert result["status"] == "conflict"
    assert "Riverside Hall" in result["answer"] and "Grand Arena" in result["answer"]


def test_answer_level_person_rivals_conflict(tmp_path):
    """People dimension end to end: same project, competing leads with
    independent evidence validate and surface as a conflict."""
    store = Store(tmp_path / "a2.sqlite3")
    store.add_file("a.txt", [("ev_a", "Alice leads the Website Project")])
    store.add_file("b.txt", [("ev_b", "Bob leads the Website Project, appointed June 1st")])
    store.add_records("run-1", [
        candidate("Alice leads Website Project", "Website Project",
                  [("ev_a", "Alice leads the Website Project")],
                  record_type="decision", status="decided", date_value=None, date_raw=None,
                  detail="Alice leads the Website Project"),
        candidate("Bob leads Website Project", "Website Project",
                  [("ev_b", "Bob leads the Website Project, appointed June 1st")],
                  record_type="decision", status="decided",
                  date_value="2026-06-01", date_raw="June 1st",
                  detail="Bob leads the website project"),
    ])
    with store.db.transaction() as con:
        pairs = [tuple(sorted(pair)) for pair in mark_conflicts(con, store.folder_id)]
        assert pairs == [(1, 2)]
    result = store.ask("who leads the website project?")
    assert result["status"] == "conflict"
    assert "Alice" in result["answer"] and "Bob" in result["answer"]


# --- Follow-up fixes: ordering, backstop precision, lowercase/numeric ---

def lowercase_venue_store(path):
    """Same event, lowercase labeled venues: backstop-grade evidence
    only, never formal conflict material."""
    store = Store(path)
    store.add_file("a.txt", [("ev_a", "The venue is riverside hall for Spring Fest")])
    store.add_file("b.txt", [("ev_b", "Venue: grand arena for Spring Fest, decided March 3rd")])
    store.add_records("run-1", [
        candidate("Spring Fest venue riverside hall", "spring fest",
                  [("ev_a", "The venue is riverside hall for Spring Fest")],
                  record_type="decision", status="decided", date_value=None, date_raw=None,
                  detail="The venue is riverside hall for Spring Fest"),
        candidate("Spring Fest venue grand arena", "spring fest",
                  [("ev_b", "Venue: grand arena for Spring Fest, decided March 3rd")],
                  record_type="decision", status="decided",
                  date_value="2026-03-03", date_raw="March 3rd",
                  detail="Venue: grand arena for Spring Fest"),
    ])
    return store


def test_f1_production_order_mark_then_supersede_resolves(tmp_path):
    """Production sweep order (conflicts before supersession) must not
    strand a legitimate replacement: the conflicted claimant steps
    aside once its own partnership revalidates, and the next sweep
    clears it."""
    store = venue_pair_store(tmp_path / "f1.sqlite3")
    with store.db.transaction() as con:
        con.execute("INSERT INTO memory_record_evidence(record_id,evidence_id,role,excerpt)"
                    " VALUES(2,'ev_a','supersedes','Council booked Riverside Hall for Spring Fest')")
        pairs = [tuple(sorted(pair)) for pair in mark_conflicts(con, store.folder_id)]
        assert pairs == [(1, 2)]
        rejected: list = []
        assert apply_supersession_links(con, store.folder_id, rejected=rejected) == [1]
        assert rejected == []
        assert mark_conflicts(con, store.folder_id) == []
        assert [(row["id"], row["status"]) for row in
                con.execute("SELECT id,status FROM memory_records ORDER BY id")] == [
            (1, "superseded"), (2, "decided")]
        kinds = [row["transition"] for row in
                 con.execute("SELECT transition FROM memory_transitions ORDER BY id")]
        assert "superseded" in kinds and "conflict_cleared" in kinds
    result = store.ask("where is Spring Fest?")
    assert result["status"] == "answered"
    assert "Grand Arena" in result["answer"]
    assert "Riverside Hall" not in result["answer"]


def test_f2_genuine_conflict_stays_without_supersession(tmp_path):
    store = venue_pair_store(tmp_path / "f2.sqlite3")
    with store.db.transaction() as con:
        first = [tuple(sorted(pair)) for pair in mark_conflicts(con, store.folder_id)]
        second = [tuple(sorted(pair)) for pair in mark_conflicts(con, store.folder_id)]
        assert first == second == [(1, 2)]
        assert {row["status"] for row in
                con.execute("SELECT status FROM memory_records")} == {"conflicted"}
    result = store.ask("where is Spring Fest?")
    assert result["status"] == "conflict"
    assert "Riverside Hall" in result["answer"] and "Grand Arena" in result["answer"]


def test_f3_compatible_same_venue_claims_join(tmp_path):
    """Booking plus praise for the same venue is agreement, not
    rivalry: the backstop must compare attributed values, not all
    nouns."""
    store = Store(tmp_path / "f3.sqlite3")
    store.add_file("a.txt", [("ev_a", "Council booked Riverside Hall for Spring Fest")])
    store.add_file("b.txt", [("ev_b", "Committee praised Riverside Hall for Spring Fest on March 3rd")])
    store.add_records("run-1", [
        candidate("Council Spring Fest venue Riverside Hall", "spring fest",
                  [("ev_a", "Council booked Riverside Hall for Spring Fest")],
                  record_type="decision", status="decided", date_value=None, date_raw=None,
                  detail="Council booked Riverside Hall for Spring Fest"),
        candidate("Committee Spring Fest venue Riverside Hall", "spring fest",
                  [("ev_b", "Committee praised Riverside Hall for Spring Fest on March 3rd")],
                  record_type="decision", status="decided",
                  date_value="2026-03-03", date_raw="March 3rd",
                  detail="Committee praised Riverside Hall for Spring Fest"),
    ])
    with store.db.transaction() as con:
        assert mark_conflicts(con, store.folder_id) == []
    result = store.ask("where is Spring Fest?")
    assert result["status"] == "answered"
    assert "Council booked Riverside Hall" in result["answer"]
    assert "Committee praised Riverside Hall" in result["answer"]
    assert result["memory"]["conflicts"] == []


def test_f4_lowercase_venues_qualified_not_settled(tmp_path):
    store = lowercase_venue_store(tmp_path / "f4.sqlite3")
    with store.db.transaction() as con:
        assert mark_conflicts(con, store.folder_id) == []
        verdict = validate_relationship(con, "conflict", 1, 2)
        assert verdict["verdict"] != SUPPORTED
    result = store.ask("where is Spring Fest?")
    assert result["status"] == "unverified"
    assert result["memory"]["conflicts"] == []
    assert "riverside hall" in result["answer"] and "grand arena" in result["answer"]


def test_f4_participles_are_not_values(tmp_path):
    """'The venue is booked' vs 'The venue is confirmed' agree the
    venue exists; predicates must never read as rival names."""
    store = Store(tmp_path / "f4b.sqlite3")
    store.add_file("a.txt", [("ev_a", "The venue is booked for Spring Fest")])
    store.add_file("b.txt", [("ev_b", "The venue is confirmed for Spring Fest, decided March 3rd")])
    store.add_records("run-1", [
        candidate("Spring Fest venue booked", "spring fest",
                  [("ev_a", "The venue is booked for Spring Fest")],
                  record_type="decision", status="decided", date_value=None, date_raw=None,
                  detail="The venue is booked for Spring Fest"),
        candidate("Spring Fest venue confirmed", "spring fest",
                  [("ev_b", "The venue is confirmed for Spring Fest, decided March 3rd")],
                  record_type="decision", status="decided",
                  date_value="2026-03-03", date_raw="March 3rd",
                  detail="The venue is confirmed for Spring Fest"),
    ])
    with store.db.transaction() as con:
        assert mark_conflicts(con, store.folder_id) == []
    result = store.ask("where is Spring Fest?")
    assert result["status"] == "answered"
    assert "booked" in result["answer"] and "confirmed" in result["answer"]


def test_f5_room_numbers_qualified_not_settled(tmp_path):
    store = Store(tmp_path / "f5.sqlite3")
    store.add_file("a.txt", [("ev_a", "Meeting room 5 for Spring Fest")])
    store.add_file("b.txt", [("ev_b", "Meeting room 7 for Spring Fest, decided March 3rd")])
    store.add_records("run-1", [
        candidate("Spring Fest meeting room 5", "spring fest",
                  [("ev_a", "Meeting room 5 for Spring Fest")],
                  record_type="decision", status="decided", date_value=None, date_raw=None,
                  detail="Meeting room 5 for Spring Fest"),
        candidate("Spring Fest meeting room 7", "spring fest",
                  [("ev_b", "Meeting room 7 for Spring Fest, decided March 3rd")],
                  record_type="decision", status="decided",
                  date_value="2026-03-03", date_raw="March 3rd",
                  detail="Meeting room 7 for Spring Fest"),
    ])
    with store.db.transaction() as con:
        assert mark_conflicts(con, store.folder_id) == []
    result = store.ask("which room for spring fest?")
    assert result["status"] == "unverified"
    assert result["memory"]["conflicts"] == []
    assert "room 5" in result["answer"].casefold() and "room 7" in result["answer"].casefold()


def test_f6_unrelated_numbers_never_rival(tmp_path):
    """Bare counts without room structure are out of scope: no
    rivalry, no conflict, both passages still cited."""
    store = Store(tmp_path / "f6.sqlite3")
    store.add_file("a.txt", [("ev_a", "Spring Fest had 5 attendees")])
    store.add_file("b.txt", [("ev_b", "Spring Fest had 7 attendees, decided March 3rd")])
    store.add_records("run-1", [
        candidate("Spring Fest 5 attendees", "spring fest",
                  [("ev_a", "Spring Fest had 5 attendees")],
                  record_type="decision", status="decided", date_value=None, date_raw=None,
                  detail="Spring Fest had 5 attendees"),
        candidate("Spring Fest 7 attendees", "spring fest",
                  [("ev_b", "Spring Fest had 7 attendees, decided March 3rd")],
                  record_type="decision", status="decided",
                  date_value="2026-03-03", date_raw="March 3rd",
                  detail="Spring Fest had 7 attendees"),
    ])
    with store.db.transaction() as con:
        assert mark_conflicts(con, store.folder_id) == []
        verdict = validate_relationship(con, "conflict", 1, 2)
        assert verdict["verdict"] != SUPPORTED
    result = store.ask("how many attended spring fest?")
    assert result["status"] != "conflict"
    assert result["memory"]["conflicts"] == []
    assert {card["file"] for card in result["evidence"]} == {"/src/a.txt", "/src/b.txt"}


def test_f7_compatible_claims_still_answer(tmp_path):
    store = Store(tmp_path / "f7.sqlite3")
    store.add_file("a.txt", [("ev_a", "Council booked Riverside Hall for Spring Fest"),
                             ("ev_c", "Doors open at noon on May 5th for Spring Fest"),
                             ("ev_d", "Contact Alice by May 6th for Spring Fest passes")])
    store.add_records("run-1", [
        candidate("Council Spring Fest venue Riverside Hall", "spring fest",
                  [("ev_a", "Council booked Riverside Hall for Spring Fest")],
                  record_type="decision", status="decided", date_value=None, date_raw=None,
                  detail="Council booked Riverside Hall for Spring Fest"),
        candidate("Spring Fest doors open at noon", "spring fest",
                  [("ev_c", "Doors open at noon on May 5th for Spring Fest")],
                  record_type="decision", status="decided",
                  date_value="2026-05-05", date_raw="May 5th",
                  detail="Doors open at noon on May 5th for Spring Fest"),
        candidate("Spring Fest passes contact Alice", "spring fest",
                  [("ev_d", "Contact Alice by May 6th for Spring Fest passes")],
                  record_type="decision", status="decided", date_value="2026-05-06",
                  date_raw="May 6th",
                  detail="Contact Alice by May 6th for Spring Fest passes"),
    ])
    with store.db.transaction() as con:
        assert mark_conflicts(con, store.folder_id) == []
    result = store.ask("spring fest plans?")
    assert result["status"] == "answered"
    assert len(result["memory"]["claims"]) == 3
    assert "Riverside Hall" in result["answer"] and "noon" in result["answer"] \
        and "Alice" in result["answer"]


def test_f8_conflicts_name_both_values(tmp_path):
    store = venue_pair_store(tmp_path / "f8.sqlite3")
    with store.db.transaction() as con:
        mark_conflicts(con, store.folder_id)
    result = store.ask("where is Spring Fest?")
    assert result["status"] == "conflict"
    assert "Records disagree on venues: Riverside Hall (a.txt) " \
        "versus Grand Arena (b.txt)." in result["answer"]

    vague = Store(tmp_path / "f8b.sqlite3")
    vague.add_file("a.txt", [("ev_a", "Riverside Hall will host Spring Fest")])
    vague.add_file("b.txt", [("ev_b", "Grand Arena will host Spring Fest, decided March 3rd")])
    vague.add_records("run-1", [
        candidate("Riverside Hall Spring Fest booking", "spring fest",
                  [("ev_a", "Riverside Hall will host Spring Fest")],
                  record_type="decision", status="decided", date_value=None, date_raw=None,
                  detail="The venue is booked for Spring Fest"),
        candidate("Grand Arena Spring Fest booking", "spring fest",
                  [("ev_b", "Grand Arena will host Spring Fest, decided March 3rd")],
                  record_type="decision", status="decided",
                  date_value="2026-03-03", date_raw="March 3rd",
                  detail="The venue is booked for Spring Fest"),
    ])
    with vague.db.transaction() as con:
        pairs = [tuple(sorted(pair)) for pair in mark_conflicts(con, vague.folder_id)]
        assert pairs == [(1, 2)]
    vague_result = vague.ask("where is Spring Fest?")
    assert vague_result["status"] == "conflict"
    assert "Riverside Hall" in vague_result["answer"] \
        and "Grand Arena" in vague_result["answer"]


def test_f9_title_nouns_do_not_force_rivalry(tmp_path):
    """Same matter, different projects: title piles must not mint
    values, and the anchor veto must still contradict."""
    store = Store(tmp_path / "f9.sqlite3")
    store.add_file("a.txt", [("ev_a", "Alice leads the Website Project")])
    store.add_file("b.txt", [("ev_b", "Bob leads the Mobile Project, appointed June 1st")])
    store.add_records("run-1", [
        candidate("Alice Website Project Kickoff", "leads project",
                  [("ev_a", "Alice leads the Website Project")],
                  record_type="decision", status="decided", date_value=None, date_raw=None,
                  detail="Alice leads the Website Project"),
        candidate("Bob Mobile Project Kickoff", "leads project",
                  [("ev_b", "Bob leads the Mobile Project, appointed June 1st")],
                  record_type="decision", status="decided",
                  date_value="2026-06-01", date_raw="June 1st",
                  detail="Bob leads the Mobile Project"),
    ])
    with store.db.transaction() as con:
        assert mark_conflicts(con, store.folder_id) == []
        verdict = validate_relationship(con, "conflict", 1, 2)
        assert verdict["verdict"] == "contradicted"
    result = store.ask("who leads website mobile project?")
    assert result["status"] != "conflict"
    assert result["memory"]["conflicts"] == []


def test_f10_name_dimension_rivalry(tmp_path):
    store = Store(tmp_path / "f10.sqlite3")
    store.add_file("a.txt", [("ev_a", "Project name is Falcon for the Website Project")])
    store.add_file("b.txt", [("ev_b", "Project name is Kestrel for the Website Project, decided June 1st")])
    store.add_records("run-1", [
        candidate("Website Project name Falcon", "website project",
                  [("ev_a", "Project name is Falcon for the Website Project")],
                  record_type="decision", status="decided", date_value=None, date_raw=None,
                  detail="Project name is Falcon for the Website Project"),
        candidate("Website Project name Kestrel", "website project",
                  [("ev_b", "Project name is Kestrel for the Website Project, decided June 1st")],
                  record_type="decision", status="decided",
                  date_value="2026-06-01", date_raw="June 1st",
                  detail="Project name is Kestrel for the Website Project"),
    ])
    with store.db.transaction() as con:
        pairs = [tuple(sorted(pair)) for pair in mark_conflicts(con, store.folder_id)]
        assert pairs == [(1, 2)]
    result = store.ask("what is the Website Project name?")
    assert result["status"] == "conflict"
    assert "Falcon" in result["answer"] and "Kestrel" in result["answer"]


def test_f11_shared_venue_date_rivalry_names_dates(tmp_path):
    """Slot path keeps precedence when venue nouns are shared: rival
    dates validate exactly as before, now with named values. One file,
    so the units path cannot own the conflict and memory synthesis
    speaks."""
    store = Store(tmp_path / "f11.sqlite3")
    store.add_file("a.txt", [("ev_a", "Council booked Riverside Hall for Spring Fest on March 1st"),
                             ("ev_b", "Correction: Council booked Riverside Hall for Spring Fest on April 2nd")])
    store.add_records("run-1", [
        candidate("Council Spring Fest Riverside Hall March", "spring fest",
                  [("ev_a", "Council booked Riverside Hall for Spring Fest on March 1st")],
                  record_type="decision", status="decided",
                  date_value="2026-03-01", date_raw="March 1st",
                  detail="Council booked Riverside Hall for Spring Fest"),
        candidate("Council Spring Fest Riverside Hall April", "spring fest",
                  [("ev_b", "Correction: Council booked Riverside Hall for Spring Fest on April 2nd")],
                  record_type="decision", status="decided",
                  date_value="2026-04-02", date_raw="April 2nd",
                  detail="Council booked Riverside Hall for Spring Fest"),
    ])
    with store.db.transaction() as con:
        pairs = [tuple(sorted(pair)) for pair in mark_conflicts(con, store.folder_id)]
        assert pairs == [(1, 2)]
    result = store.ask("when is Spring Fest?")
    assert result["status"] == "conflict"
    assert "Records disagree on dates: March 1st (a.txt) " \
        "versus April 2nd (a.txt)." in result["answer"]


def test_f12_tier_c_and_abstention_hold(tmp_path):
    store = Store(tmp_path / "f12.sqlite3")
    store.add_file("a.txt", [("ev_a", "Council booked the venue riverside hall for Spring Fest")])
    store.add_file("b.txt", [("ev_b", "Council booked venue grand arena for Spring Fest on March 3rd")])
    store.add_records("run-1", [
        candidate("Council Spring Fest venue riverside hall", "spring fest",
                  [("ev_a", "Council booked the venue riverside hall for Spring Fest")],
                  record_type="decision", status="decided", date_value=None, date_raw=None,
                  detail="Council booked the venue riverside hall for Spring Fest"),
        candidate("Council Spring Fest venue grand arena", "spring fest",
                  [("ev_b", "Council booked venue grand arena for Spring Fest on March 3rd")],
                  record_type="decision", status="decided", date_value="2026-03-03",
                  date_raw="March 3rd",
                  detail="Council booked venue grand arena for Spring Fest"),
    ])
    twin = Store(tmp_path / "f12t.sqlite3")
    twin.add_file("a.txt", [("ev_a", "Council booked the venue riverside hall for Spring Fest")])
    twin.add_file("b.txt", [("ev_b", "Council booked venue grand arena for Spring Fest on March 3rd")])
    question = "Who booked the council hall?"
    result = store.ask(question)
    reference = twin.ask(question)
    assert result["memory"]["claims"] == []
    assert result["memory"]["conflicts"] == []
    assert len(result["memory"]["context_claims"]) == 2
    assert_units_identical(result, reference)
    abstained = store.ask("what is the refund policy?")
    assert abstained["status"] in ("unverified", "not_found", "cloud_unavailable")
    assert "riverside" not in (abstained["answer"] or "").casefold()

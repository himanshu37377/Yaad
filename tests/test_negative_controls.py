"""Negative controls from the post-V2 read-only audit: negated
confirmation, incidental amounts, stale-middle chains, and photo
substring false hits. Each pins behavior the safeguards must NOT do.
"""
from test_memory_answers import Store, candidate
from test_photo_answers import add_image
from yaad.memory.records import (
    apply_supersession_links,
    generate_confirmed_quote_candidates,
    mark_conflicts,
)
from yaad.query.photos import _question_hits


def test_negated_confirmation_generates_no_candidate(tmp_path):
    store = Store(tmp_path / "neg.sqlite3")
    store.add_file("a.txt", [("ev_a", "Spring Fest budget is Rs 20000, quoted by the council")])
    store.add_file("b.txt", [("ev_b", "Spring Fest budget of Rs 25000 is not confirmed yet")])
    store.add_records("run-1", [
        candidate("Spring Fest budget Rs 20000", "spring fest budget",
                  [("ev_a", "Spring Fest budget is Rs 20000")],
                  record_type="decision", status="decided", date_value=None, date_raw=None,
                  detail="Spring Fest budget is Rs 20000, quoted by the council"),
        candidate("Spring Fest budget Rs 25000", "spring fest budget",
                  [("ev_b", "Spring Fest budget of Rs 25000 is not confirmed yet")],
                  record_type="decision", status="decided", date_value=None, date_raw=None,
                  detail="Spring Fest budget of Rs 25000 is not confirmed yet")])
    with store.db.transaction() as con:
        pairs = mark_conflicts(con, store.folder_id)
        assert generate_confirmed_quote_candidates(con, store.folder_id, pairs) == 0
        assert apply_supersession_links(con, store.folder_id) == []


def test_venue_deposit_does_not_carry_budget_slot(tmp_path):
    store = Store(tmp_path / "dep.sqlite3")
    store.add_file("a.txt", [("ev_a", "Autumn Fest venue is Riverside Hall, deposit Rs 5000")])
    store.add_records("run-1", [candidate(
        "Autumn Fest venue Riverside Hall", "autumn fest venue",
        [("ev_a", "Autumn Fest venue is Riverside Hall, deposit Rs 5000")],
        record_type="decision", status="decided", date_value=None, date_raw=None,
        detail="Autumn Fest venue is Riverside Hall, deposit Rs 5000")])
    result = store.ask("What is the Autumn Fest budget?")
    assert result["memory"]["claims"] == []
    assert result["memory"]["context_claims"], "deposit record must stay contextual"


def test_stale_middle_breaks_transitive_chain(tmp_path):
    store = Store(tmp_path / "stalechain.sqlite3")
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
                  detail="Registration deadline is April 9, 2027 for Spring Fest"),
    ])
    with store.db.transaction() as con:
        con.execute("INSERT INTO memory_record_evidence(record_id,evidence_id,role,excerpt)"
                    " VALUES(2,'ev_a','supersedes','Registration deadline is March 1, 2027')")
        con.execute("INSERT INTO memory_record_evidence(record_id,evidence_id,role,excerpt)"
                    " VALUES(3,'ev_b','supersedes','Registration deadline is April 2, 2027')")
        # The middle record's supporting evidence disappears.
        con.execute("DELETE FROM units WHERE evidence_id='ev_b'")
        mark_conflicts(con, store.folder_id)
        stepped = apply_supersession_links(con, store.folder_id)
    assert 1 not in stepped, "transitivity must stop at the stale middle"
    with store.db.connect() as con:
        status = con.execute(
            "SELECT status FROM memory_records WHERE id=1").fetchone()["status"]
    assert status != "superseded"


def test_red_query_gets_no_boost_from_covered():
    photo = {"file": "/src/covered_wagon.jpg",
             "description": {"caption": "A covered wagon in a field",
                             "visible_text": ""},
             "quote": "A covered wagon in a field"}
    assert _question_hits(photo, "show photos of the red bicycle") == 0

"""Adversarial memory conflicts over seeded records (not just units).

A1: Room A vs Room B is backstop-grade rivalry (single-letter room
    identifiers): the pair must surface as qualified disagreement with
    both citations, never settle into one answer, and never force a
    formal conflict the evidence cannot validate.
A2: A fee supersession chain (Rs 100 -> Rs 150 -> Rs 200) resolves to
    the current value while superseded records, their provenance, and
    the ledger history stay inspectable.
A3: Paraphrase-date duplicates (due/deadline/November trio across two
    files) group into one claim with one value: no conflict, no
    qualification, no corruption.

Deterministic fixtures on disposable databases; no model calls, no
production database contact.
"""
from test_memory_answers import Store, candidate

from yaad.memory.records import apply_supersession_links, mark_conflicts
from yaad.memory.relationships import SUPPORTED, validate_relationship


def test_a1_room_letters_surface_qualified_disagreement(tmp_path):
    store = Store(tmp_path / "a1.sqlite3")
    store.add_file("a.txt", [("ev_a", "The workshop is in Room A")])
    store.add_file("b.txt", [("ev_b", "The workshop is in Room B, decided March 3rd")])
    store.add_records("run-1", [
        candidate("Workshop Room A", "workshop room",
                  [("ev_a", "workshop is in Room A")],
                  record_type="decision", status="decided",
                  date_value=None, date_raw=None,
                  detail="The workshop is in Room A"),
        candidate("Workshop Room B", "workshop room",
                  [("ev_b", "workshop is in Room B, decided March 3rd")],
                  record_type="decision", status="decided",
                  date_value="2026-03-03", date_raw="March 3rd",
                  detail="The workshop is in Room B"),
    ])
    with store.db.transaction() as con:
        # Backstop-grade evidence only: no sweep marking, no formal
        # validation — conservative rejection holds for single letters.
        assert mark_conflicts(con, store.folder_id) == []
        assert validate_relationship(con, "conflict", 1, 2)["verdict"] != SUPPORTED
    result = store.ask("which room is the workshop in?")
    assert result["status"] == "unverified"
    assert result["memory"]["conflicts"] == []
    assert "Room A" in result["answer"] and "Room B" in result["answer"]
    assert {card["id"] for card in result["evidence"]} == {"ev_a", "ev_b"}


def _fee_chain_store(path):
    store = Store(path)
    store.add_file("fee_v1.txt", [("ev_1", "Spring Fest registration fee is Rs 100")])
    store.add_file("fee_v2.txt", [("ev_2", "Updated Spring Fest registration fee is Rs 150")])
    store.add_file("fee_v3.txt", [("ev_3", "Final Spring Fest registration fee is Rs 200")])
    store.add_records("run-1", [
        candidate("Spring Fest registration fee Rs 100", "spring fest registration fee",
                  [("ev_1", "Spring Fest registration fee is Rs 100")],
                  record_type="decision", status="decided",
                  date_value=None, date_raw=None,
                  detail="Spring Fest registration fee is Rs 100"),
        candidate("Updated Spring Fest registration fee Rs 150", "updated spring registration fee",
                  [("ev_2", "Updated Spring Fest registration fee is Rs 150")],
                  record_type="decision", status="decided",
                  date_value=None, date_raw=None,
                  detail="Updated Spring Fest registration fee is Rs 150"),
        candidate("Spring Fest registration fee Rs 200 final", "final spring registration fee",
                  [("ev_3", "final Spring Fest registration fee is Rs 200")],
                  record_type="decision", status="decided",
                  date_value=None, date_raw=None,
                  detail="final Spring Fest registration fee is Rs 200"),
    ])
    return store


def test_a2_fee_chain_resolves_current_with_history_preserved(tmp_path):
    store = _fee_chain_store(tmp_path / "a2.sqlite3")
    with store.db.transaction() as con:
        assert mark_conflicts(con, store.folder_id) == []
        con.execute("INSERT INTO memory_record_evidence(record_id,evidence_id,role,excerpt)"
                    " VALUES(2,'ev_1','supersedes','Spring Fest registration fee is Rs 100')")
        con.execute("INSERT INTO memory_record_evidence(record_id,evidence_id,role,excerpt)"
                    " VALUES(3,'ev_2','supersedes','Updated Spring Fest registration fee is Rs 150')")
        rejected: list = []
        stepped = apply_supersession_links(con, store.folder_id, rejected=rejected)
        # Simultaneous links apply in SELECT order: whichever direct
        # edge lands second may find its claimant already stepped down;
        # transitive closure re-resolves it against the winner. Only
        # that benign ordering artifact may appear here — never an
        # unrelatedness or ambiguity rejection.
        assert all("only a current record can supersede" in entry["reason"]
                   for entry in rejected)
        assert set(stepped) == {1, 2}
        assert [(row["id"], row["status"]) for row in
                con.execute("SELECT id,status FROM memory_records ORDER BY id")] == [
            (1, "superseded"), (2, "superseded"), (3, "decided")]
        # History preserved: superseded rows keep their supports and the
        # ledger records both steps with their winners.
        assert con.execute("SELECT COUNT(*) AS n FROM memory_record_evidence"
                           " WHERE record_id IN (1,2) AND role='supports'").fetchone()["n"] == 2
        steps = {row["record_id"]: row["related_record_id"] for row in con.execute(
            "SELECT record_id,related_record_id FROM memory_transitions"
            " WHERE transition='superseded'")}
        assert set(steps) == {1, 2}
        assert steps[2] == 3
        # R1's winner depends on link order: direct (2) or transitive (3).
        assert steps[1] in (2, 3)
    result = store.ask("What is the Spring Fest registration fee?")
    assert result["status"] == "answered"
    assert "Rs 200" in result["answer"]
    assert "Rs 100" not in result["answer"] and "Rs 150" not in result["answer"]
    assert {value["value"] for value in result["values"]} == {"Rs 200"}
    assert result["evidence"] and all(card["historical"] is False for card in result["evidence"])
    assert "/src/fee_v3.txt" in {card["file"] for card in result["evidence"]}
    history = store.ask("list superseded records")
    assert history["status"] == "answered"
    assert "Rs 100" in history["answer"] and "Rs 150" in history["answer"]
    assert {card["file"] for card in history["evidence"]} == {"/src/fee_v1.txt", "/src/fee_v2.txt"}


def test_a3_paraphrase_date_trio_yields_one_claim(tmp_path):
    store = Store(tmp_path / "a3.sqlite3")
    store.add_file("due.txt", [("ev_due", "Spring Fest venue confirmation due November 10")])
    store.add_file("dead.txt", [("ev_dead", "Spring Fest venue confirmation deadline November 10")])
    store.add_records("run-1", [
        candidate("Spring Fest venue confirmation due", "spring fest venue confirmation due",
                  [("ev_due", "Spring Fest venue confirmation due November 10")],
                  date_value="2026-11-10", date_raw="November 10"),
        candidate("Spring Fest venue confirmation deadline",
                  "spring fest venue confirmation deadline",
                  [("ev_dead", "Spring Fest venue confirmation deadline November 10")],
                  date_value="2026-11-10", date_raw="November 10"),
        candidate("Fest venue confirmation November", "venue confirmation november fest",
                  [("ev_due", "Spring Fest venue confirmation due November 10")],
                  date_value="2026-11-10", date_raw="November 10"),
    ])
    with store.db.transaction() as con:
        assert mark_conflicts(con, store.folder_id) == []
    result = store.ask("when is venue confirmation due?")
    assert result["status"] == "answered"
    assert result["memory"]["conflicts"] == []
    assert len(result["memory"]["claims"]) == 1
    assert sorted(result["memory"]["claims"][0]["record_ids"]) == [1, 2, 3]
    assert len(result["values"]) == 1
    assert result["values"][0]["value"] == "November 10"

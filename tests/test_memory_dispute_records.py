"""Dispute-describing memory records must serve `conflict`, not `answered`.

A single record whose own statement describes a dispute ("sources
disagree: Rs 8,000 vs Rs 9,500 ... disputed") previously had no path to
`conflict` status (every conflict path needs two records) and was
served verbatim as `answered` on live C01/C02/C07/C08/C09. Extraction
must reject dispute summaries (one record per rival side), the sweep
must mark legacy dispute rows conflicted, planning must surface them
as single-side conflicts, and review must upgrade dispute answers
citing two files. Stub clients only; no network.
"""
import json
import sys
from pathlib import Path

import pytest
from test_memory_answers import Store, candidate

from yaad.memory.extract import RULES
from yaad.memory.records import (
    compute_record_key,
    mark_conflicts,
    states_dispute,
    upsert_records,
    validate_candidate,
)
from yaad.query.memory_answer import plan_memory, synthesize

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "eval" / "benchmark_v1"))

EV_A = ("ev_a", "The caterer sent its quote for Spring Fest catering.")
EV_B = ("ev_b", "The committee noted its estimate for Spring Fest catering.")

CATERING_DISPUTE = (
    "The sources disagree: the caterer quotes Rs 8,000 for Spring Fest "
    "catering while the committee notes Rs 9,500. The catering cost is disputed.")
DATE_DISPUTE = (
    "Registration closes 2027-04-09 per source A; source B says registration "
    "will close 2027-04-11. The closing date is disputed.")


def _legacy_dispute(store, title, detail, key_phrase, evidence):
    """Direct-SQL legacy row: simulates a stored dispute summary that
    predates the extraction rejection."""
    with store.db.transaction() as con:
        key = compute_record_key("decision", "explicit", None, None,
                                 key_phrase, "multi")
        cursor = con.execute(
            "INSERT INTO memory_records(folder_id,record_type,title,detail,status,"
            "statement_kind,record_key,extraction_run,origin)"
            " VALUES(?,?,?,?,?,?,?,?,?)",
            (store.folder_id, "decision", title, detail, "decided",
             "explicit", key, "legacy", "document"))
        rid = cursor.lastrowid
        for eid, excerpt in evidence:
            con.execute(
                "INSERT INTO memory_record_evidence(record_id,evidence_id,role,excerpt)"
                " VALUES(?,?,?,?)", (rid, eid, "supports", excerpt))
    return rid


def _catering_store(tmp_path, name="d.sqlite3"):
    store = Store(tmp_path / name)
    store.add_file("source_a.txt", [EV_A])
    store.add_file("source_b.txt", [EV_B])
    return store


def _sweep(store):
    with store.db.transaction() as con:
        return mark_conflicts(con, store.folder_id)


def _status(store, record_id):
    with store.db.connect() as con:
        return con.execute("SELECT status FROM memory_records WHERE id=?",
                           (record_id,)).fetchone()["status"]


# --- extraction / validation ---

def test_dispute_summary_candidate_rejected(tmp_path):
    store = Store(tmp_path / "v.sqlite3")
    store.add_file("a.txt", [("ev_a", "The caterer quotes Rs 8,000 for Spring Fest catering.")])
    store.add_file("b.txt", [("ev_b", "The committee notes Rs 9,500 for Spring Fest catering.")])
    bad = candidate("Catering cost dispute", "spring catering",
                    [("ev_a", "caterer quotes Rs 8,000 for Spring Fest catering"),
                     ("ev_b", "committee notes Rs 9,500 for Spring Fest catering")],
                    record_type="decision", status="decided",
                    detail=CATERING_DISPUTE)
    errors = validate_candidate(bad, dict(store.unit_texts))
    assert any("dispute summary" in err for err in errors)
    with pytest.raises(ValueError, match="dispute summary"):
        store.add_records("run-1", [bad])
    sides = [
        candidate("Catering cost", "catering quotes Rs",
                  [("ev_a", "caterer quotes Rs 8,000 for Spring Fest catering")],
                  record_type="decision", status="decided", date_value=None, date_raw=None,
                  detail="The caterer quotes Rs 8,000 for Spring Fest catering."),
        candidate("Catering cost", "committee notes catering",
                  [("ev_b", "committee notes Rs 9,500 for Spring Fest catering")],
                  record_type="decision", status="decided", date_value=None, date_raw=None,
                  detail="The committee notes Rs 9,500 for Spring Fest catering."),
    ]
    for side in sides:
        assert validate_candidate(side, dict(store.unit_texts)) == []
    assert "Rival values are separate records" in RULES


def test_multi_amount_without_lexicon_accepted(tmp_path):
    assert states_dispute("Fee", "The fee is Rs 100 plus Rs 20 deposit.") is False
    store = Store(tmp_path / "m.sqlite3")
    store.add_file("a.txt", [("ev_a", "Spring Fest catering fee is Rs 100 plus Rs 20 deposit.")])
    cand = candidate("Catering fee", "catering fee",
                     [("ev_a", "Spring Fest catering fee is Rs 100 plus Rs 20 deposit")],
                     record_type="decision", status="decided", date_value=None, date_raw=None,
                     detail="The Spring Fest catering fee is Rs 100 plus Rs 20 deposit.")
    assert validate_candidate(cand, dict(store.unit_texts)) == []


def test_negated_dispute_accepted(tmp_path):
    detail = ("There is no dispute: the Rs 3,000 snack budget plus Rs 200 "
              "delivery stands for Spring Fest.")
    assert states_dispute("Snack budget", detail) is False
    store = Store(tmp_path / "n.sqlite3")
    store.add_file("a.txt", [("ev_a", "Spring Fest snack budget Rs 3,000 plus Rs 200 delivery.")])
    cand = candidate("Snack budget", "snack budget",
                     [("ev_a", "Spring Fest snack budget Rs 3,000 plus Rs 200 delivery")],
                     record_type="decision", status="decided", date_value=None, date_raw=None, detail=detail)
    assert validate_candidate(cand, dict(store.unit_texts)) == []


def test_contrastive_mention_accepted(tmp_path):
    detail = ("Spring Fest venue is Crystal Pavilion, not the other hall. "
              "Booking fee Rs 500.")
    assert states_dispute("Venue", detail) is False
    store = Store(tmp_path / "c.sqlite3")
    store.add_file("a.txt", [("ev_a", "Spring Fest venue Crystal Pavilion booking fee Rs 500.")])
    cand = candidate("Venue", "venue pavilion",
                     [("ev_a", "Spring Fest venue Crystal Pavilion booking fee Rs 500")],
                     record_type="decision", status="decided", date_value=None, date_raw=None, detail=detail)
    assert validate_candidate(cand, dict(store.unit_texts)) == []


# --- storage sweep ---

def _date_store(tmp_path, name="dd.sqlite3"):
    store = Store(tmp_path / name)
    store.add_file("source_a.txt",
                   [("ev_a", "Registration closes on the posted date per source A for Spring Fest.")])
    store.add_file("source_b.txt",
                   [("ev_b", "Registration closes on the posted date per source B for Spring Fest.")])
    return store


def test_dispute_record_marked_conflicted_with_ledger(tmp_path):
    store = _date_store(tmp_path)
    rid = _legacy_dispute(store, "Closing date dispute", DATE_DISPUTE,
                          "registration closes",
                          [("ev_a", store.unit_texts["ev_a"]),
                           ("ev_b", store.unit_texts["ev_b"])])
    assert _status(store, rid) == "decided"
    _sweep(store)
    assert _status(store, rid) == "conflicted"
    with store.db.connect() as con:
        rows = [dict(row) for row in con.execute(
            "SELECT transition,related_record_id FROM memory_transitions WHERE record_id=?",
            (rid,))]
    assert rows == [{"transition": "conflict_marked", "related_record_id": None}]


def test_dispute_mark_clears_when_edited(tmp_path):
    store = _date_store(tmp_path)
    rid = _legacy_dispute(store, "Closing date dispute", DATE_DISPUTE,
                          "registration closes",
                          [("ev_a", store.unit_texts["ev_a"]),
                           ("ev_b", store.unit_texts["ev_b"])])
    _sweep(store)
    assert _status(store, rid) == "conflicted"
    with store.db.transaction() as con:
        con.execute("UPDATE memory_records SET detail=? WHERE id=?",
                    ("Registration closes 2027-04-09 for Spring Fest.", rid))
    _sweep(store)
    assert _status(store, rid) == "decided"
    with store.db.connect() as con:
        kinds = [row["transition"] for row in con.execute(
            "SELECT transition FROM memory_transitions WHERE record_id=? ORDER BY id",
            (rid,))]
    assert kinds == ["conflict_marked", "conflict_cleared"]


def test_dispute_mark_idempotent(tmp_path):
    store = _date_store(tmp_path)
    rid = _legacy_dispute(store, "Closing date dispute", DATE_DISPUTE,
                          "registration closes",
                          [("ev_a", store.unit_texts["ev_a"]),
                           ("ev_b", store.unit_texts["ev_b"])])
    _sweep(store)
    _sweep(store)
    assert _status(store, rid) == "conflicted"
    with store.db.connect() as con:
        count = con.execute(
            "SELECT COUNT(*) AS n FROM memory_transitions WHERE record_id=?",
            (rid,)).fetchone()["n"]
    assert count == 1


# --- retrieval + serving ---

def _seeded_catering_dispute(tmp_path, name="s.sqlite3"):
    store = _catering_store(tmp_path, name)
    _legacy_dispute(store, "Catering cost dispute", CATERING_DISPUTE,
                    "spring catering",
                    [("ev_a", store.unit_texts["ev_a"]),
                     ("ev_b", store.unit_texts["ev_b"])])
    _sweep(store)
    return store


def _assert_single_dispute_conflict(result):
    assert result["status"] == "conflict"
    assert "Rs 8,000" in result["answer"] and "Rs 9,500" in result["answer"]
    assert {card["file"] for card in result["evidence"]} == {
        "/src/source_a.txt", "/src/source_b.txt"}
    assert len(result["memory"]["conflicts"]) == 1
    assert len(result["memory"]["conflicts"][0]) == 1


def test_c01_style_single_dispute_serves_conflict(tmp_path):
    store = _seeded_catering_dispute(tmp_path)
    result = store.ask("How much does the fest catering cost?")
    _assert_single_dispute_conflict(result)


def test_c02_style_paraphrase_same_matter(tmp_path):
    store = _seeded_catering_dispute(tmp_path, "p.sqlite3")
    result = store.ask("What is the catering price for Spring Fest?")
    _assert_single_dispute_conflict(result)


def test_c09_style_agreement_question(tmp_path):
    store = _seeded_catering_dispute(tmp_path, "g.sqlite3")
    result = store.ask("Do the sources agree on the catering cost?")
    _assert_single_dispute_conflict(result)


def test_date_dispute_c07_c08_style(tmp_path):
    store = _date_store(tmp_path, "e.sqlite3")
    _legacy_dispute(store, "Closing date dispute", DATE_DISPUTE,
                    "registration closes",
                    [("ev_a", store.unit_texts["ev_a"]),
                     ("ev_b", store.unit_texts["ev_b"])])
    _sweep(store)
    for question in ("When does registration close?",
                     "What is the registration closing date?"):
        result = store.ask(question)
        assert result["status"] == "conflict", question
        assert "2027-04-09" in result["answer"] and "2027-04-11" in result["answer"], question
        assert {card["file"] for card in result["evidence"]} == {
            "/src/source_a.txt", "/src/source_b.txt"}


def test_pair_path_unchanged_c04_style(tmp_path):
    store = Store(tmp_path / "pair.sqlite3")
    store.add_file("a.txt", [("ev_a", "Venue hire is Rs 18,000 for Spring Fest.")])
    store.add_file("b.txt", [("ev_b", "Venue hire is Rs 21,000 for Spring Fest.")])
    store.add_records("run-1", [
        candidate("Venue hire side A", "venue hire",
                  [("ev_a", "Venue hire is Rs 18,000 for Spring Fest")],
                  record_type="decision", status="decided", date_value=None, date_raw=None,
                  detail="Venue hire is Rs 18,000 for Spring Fest."),
        candidate("Venue hire side B", "venue hire",
                  [("ev_b", "Venue hire is Rs 21,000 for Spring Fest")],
                  record_type="decision", status="decided", date_value=None, date_raw=None,
                  detail="Venue hire is Rs 21,000 for Spring Fest."),
    ])
    from yaad.query.hybrid import hybrid_search
    _sweep(store)
    question = "What is the Spring Fest venue hire cost?"
    rows = hybrid_search(store.db, question, store.folder_id)
    plan = plan_memory(store.db, question, store.folder_id, rows, local=True)
    assert len(plan["conflicts"]) == 1 and len(plan["conflicts"][0]) == 2
    from yaad.query.memory_answer import fetch_support_rows
    wanted = [eid for pair in plan["conflicts"] for claim in pair
              for eid in claim["evidence_ids"]]
    synth = synthesize(question, plan, fetch_support_rows(store.db, store.folder_id, wanted))
    assert synth["status"] == "conflict"
    assert synth["answer"].startswith("Records disagree on amounts:")


# --- answered-case regressions ---

def test_single_value_record_still_answered(tmp_path):
    store = Store(tmp_path / "snack.sqlite3")
    store.add_file("memo.txt", [("ev_m", "The committee decided the snack budget is Rs 3,000.")])
    store.add_records("run-1", [
        candidate("Snack budget", "snack budget",
                  [("ev_m", "committee decided the snack budget is Rs 3,000")],
                  record_type="decision", status="decided", date_value=None, date_raw=None,
                  detail="The committee decided the snack budget is Rs 3,000.")])
    result = store.ask("What is the snack budget?")
    assert result["status"] == "answered"
    assert "Rs 3,000" in result["answer"]
    assert result["memory"]["conflicts"] == []


def test_listing_record_still_answered(tmp_path):
    store = Store(tmp_path / "list.sqlite3")
    store.add_file("rooms.txt", [("ev_r", "Rooms 5, 6 and 7 are available for Spring Fest meetings.")])
    store.add_records("run-1", [
        candidate("Meeting rooms", "rooms available meetings",
                  [("ev_r", "Rooms 5, 6 and 7 are available for Spring Fest meetings")],
                  record_type="decision", status="decided", date_value=None, date_raw=None,
                  detail="Rooms 5, 6 and 7 are available for Spring Fest meetings.")])
    result = store.ask("Which rooms are available for the Spring Fest meeting?")
    assert result["status"] == "answered"
    assert result["memory"]["conflicts"] == []


def test_supersession_winner_still_answered(tmp_path):
    store = Store(tmp_path / "sup.sqlite3")
    store.add_file("fees.txt", [("ev_f", "Spring Fest registration fee Rs 150, previously Rs 100.")])
    store.add_records("run-1", [
        candidate("Registration fee", "registration fee",
                  [("ev_f", "Spring Fest registration fee Rs 150")],
                  record_type="decision", status="decided", date_value=None, date_raw=None,
                  detail="Spring Fest registration fee is Rs 150."),
        candidate("Old registration fee", "previously",
                  [("ev_f", "previously Rs 100")],
                  record_type="decision", status="decided", date_value=None, date_raw=None,
                  detail="Spring Fest registration fee was Rs 100."),
    ])
    with store.db.transaction() as con:
        con.execute("UPDATE memory_records SET status='superseded' WHERE title=?",
                    ("Old registration fee",))
    _sweep(store)
    result = store.ask("What is the Spring Fest registration fee?")
    assert result["status"] == "answered"
    assert "Rs 150" in result["answer"]
    assert result["memory"]["conflicts"] == []


def test_review_upgrade_only_with_two_files(tmp_path, monkeypatch):
    from yaad.config import Settings
    from yaad.query.answer import answer_question

    store = Store(tmp_path / "rev.sqlite3")
    store.settings = Settings(db_path=store.db.path, cloud_enabled=True,
                              model_api_key="stub", model_provider="cloud")
    store.add_file("a.txt", [("ev_a", "Caterer quotes Rs 8,000 and committee notes Rs 9,500 for Spring Fest catering.")])
    store.add_file("b.txt", [("ev_b", "Spring Fest catering memo mentions the fest files.")])

    class StubReview:
        def __init__(self, evidence_ids):
            self.evidence_ids = evidence_ids
            self.last_call = {"provider": "stub", "model": "stub",
                              "status_code": 200, "success": True}

        def reason_json(self, messages, schema, prompt_id):
            return {"status": "answered",
                    "answer": ("Sources disagree: the caterer quotes Rs 8,000 "
                               "while the committee notes Rs 9,500."),
                    "claims": [{"claim": ("Catering figures are Rs 8,000 and Rs 9,500 "
                                          "for Spring Fest catering."),
                                "evidence_ids": list(self.evidence_ids)}]}

    question = "How much does the fest catering cost?"
    monkeypatch.setattr("yaad.llm.get_client", lambda settings: StubReview(["ev_a"]))
    single = answer_question(store.db, question, store.folder_id, store.settings)
    assert single["status"] == "answered"
    monkeypatch.setattr("yaad.llm.get_client",
                        lambda settings: StubReview(["ev_a", "ev_b"]))
    pair = answer_question(store.db, question, store.folder_id, store.settings)
    assert pair["status"] == "conflict"
    assert {card["file"] for card in pair["evidence"]} == {"/src/a.txt", "/src/b.txt"}


def test_seed_folder_has_no_dispute_records(tmp_path):
    import run as bv1_run
    from yaad.db import Database

    db = Database(tmp_path / "seed.sqlite3")
    corpus = ROOT / "eval" / "benchmark_v1" / "corpus" / "contradiction"
    with db.transaction() as con:
        con.execute("INSERT INTO folders(id,path,cloud_consent) VALUES(1,'/c',1)")
        for pos, name in enumerate(("source_a.txt", "source_b.txt", "memo.txt"), start=1):
            text = (corpus / name).read_text(encoding="utf-8")
            con.execute(
                "INSERT INTO files(id,folder_id,path,sha256,kind,size,mtime,status)"
                " VALUES(?,1,?,'h','text',10,1.0,'indexed')", (pos, f"/c/{name}"))
            con.execute(
                "INSERT INTO units(file_id,evidence_id,kind,locator,text) VALUES(?,?,'text','{}',?)",
                (pos, f"ev_{pos}", text))
    assert bv1_run.seed_folder(db, 1, "contradiction", "test-seed") == 7
    with db.connect() as con:
        rows = [dict(row) for row in con.execute("SELECT title,detail FROM memory_records")]
    assert rows
    assert not any(states_dispute(row["title"], row["detail"]) for row in rows)

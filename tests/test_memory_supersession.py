"""Stage 4: supersession safety and historical-evidence handling.

Supersession edges are validated (distinct resolving replacement evidence,
resolving link, current explicit target); failures are flagged, never
applied. Superseded units stay inspectable but cannot ground settled
answers. Offline except stubbed semantic clients; no spend.
"""
from test_memory_answers import Store, candidate

from yaad.config import Settings
from yaad.memory.extract import run_extraction
from yaad.memory.records import apply_supersession_links


class StubClient:
    last_call = {"provider": "Meta Model API", "model": "muse-spark-1.3", "status_code": 200,
                 "success": True, "duration_ms": 5, "input_tokens": 900,
                 "output_tokens": 300, "cached_tokens": 0}

    def __init__(self, records):
        self.records = records

    def reason_json(self, messages, schema, prompt_id):
        return {"records": self.records}


def extracted(item):
    item["date_value"] = item.get("date_value") or ""
    item["date_raw"] = item.get("date_raw") or ""
    item["assignee"] = item.get("assignee") or ""
    return item


def room_fixture(path):
    store = Store(path)
    store.settings = Settings(db_path=path, cloud_enabled=True, model_api_key="stub-key-for-tests")
    store.add_file("a.txt", [("ev_old", "The workshop is in Room A")])
    store.add_file("b.txt", [("ev_new", "Correction: the workshop moved to Room B")])
    return store


def test_valid_room_replacement_marks_old_and_answers_new(tmp_path):
    store = room_fixture(tmp_path / "room.sqlite3")
    old = extracted(candidate("Workshop Room A", "workshop room",
                              [("ev_old", "workshop is in Room A")],
                              record_type="decision", status="decided",
                              date_value=None, date_raw=None))
    new = extracted(candidate("Workshop Room B", "workshop room moved",
                              [("ev_new", "workshop moved to Room B")],
                              detail="Workshop Room B",
                              record_type="decision", status="decided",
                              date_value=None, date_raw=None))
    new["evidence"].append({"evidence_id": "ev_old", "role": "supersedes",
                            "excerpt": "workshop is in Room A"})
    summary = run_extraction(store.db, store.folder_id, "run-1", store.settings,
                             StubClient([old, new]))
    assert summary["superseded"] != [] and summary["rejected_supersession"] == []
    with store.db.connect() as con:
        statuses = {r["title"]: r["status"] for r in
                    con.execute("SELECT title,status FROM memory_records")}
    assert statuses == {"Workshop Room A": "superseded", "Workshop Room B": "decided"}
    store.settings = Settings(db_path=tmp_path / "room.sqlite3", cloud_enabled=False)
    result = store.ask("which room is the workshop in?")
    assert result["status"] == "answered" and "Room B" in result["answer"]
    assert [card["id"] for card in result["evidence"]] == ["ev_new"]


def test_bogus_links_rejected_and_old_record_keeps_status(tmp_path):
    store = Store(tmp_path / "bogus.sqlite3")
    store.add_file("a.txt", [("ev_old", "The workshop is in Room A")])
    store.add_file("b.txt", [("ev_new", "Correction: the workshop moved to Room B"),
                             ("ev_third", "Neutral filler note"),
                             ("ev_gone2", "Vanished note")])
    store.add_records("run-1", [
        candidate("Workshop Room A", "workshop room", [("ev_old", "workshop is in Room A")],
                  record_type="decision", status="decided", date_value=None, date_raw=None),
        candidate("Workshop Room B", "workshop room moved", [("ev_new", "workshop moved to Room B")],
                  detail="Workshop Room B", record_type="decision", status="decided",
                  date_value=None, date_raw=None),
    ])
    with store.db.transaction() as con:
        ids = {r["title"]: r["id"] for r in con.execute("SELECT id,title FROM memory_records")}
        # Bogus edge 1: names evidence no record supports.
        con.execute("INSERT INTO memory_record_evidence(record_id,evidence_id,role,excerpt)"
                    " VALUES(?, 'ev_third', 'supersedes', 'Neutral filler note')",
                    (ids["Workshop Room B"],))
        # Bogus edge 2: names a deleted unit.
        con.execute("DELETE FROM units WHERE evidence_id='ev_gone2'")
        con.execute("INSERT INTO memory_record_evidence(record_id,evidence_id,role,excerpt)"
                    " VALUES(?, 'ev_gone2', 'supersedes', 'Vanished note')",
                    (ids["Workshop Room A"],))
        rejected: list = []
        assert apply_supersession_links(con, store.folder_id, rejected=rejected) == []
        assert {entry["reason"] for entry in rejected} == {
            "no record supports the named evidence", "supersession link evidence unresolved"}
        assert con.execute("SELECT status FROM memory_records WHERE id=?",
                           (ids["Workshop Room A"],)).fetchone()["status"] == "decided"


def test_self_reference_and_stale_target_rejected(tmp_path):
    store = Store(tmp_path / "self.sqlite3")
    store.add_file("a.txt", [("ev_a", "Workshop is in Room A"),
                             ("ev_b", "Workshop moved to Room B"),
                             ("ev_s", "Legacy statement here")])
    store.add_records("run-1", [
        candidate("Room A", "workshop room", [("ev_a", "Workshop is in Room A")],
                  record_type="decision", status="decided", date_value=None, date_raw=None),
        candidate("Room B", "workshop room moved", [("ev_b", "Workshop moved to Room B")],
                  detail="Room B", record_type="decision", status="decided",
                  date_value=None, date_raw=None),
        candidate("Legacy", "legacy statement", [("ev_s", "Legacy statement here")],
                  record_type="decision", status="decided", date_value=None, date_raw=None),
    ])
    with store.db.transaction() as con:
        ids = {r["title"]: r["id"] for r in con.execute("SELECT id,title FROM memory_records")}
        con.execute("UPDATE memory_records SET status='stale' WHERE id=?", (ids["Legacy"],))
        # Self-referential edge: B supports ev_b and supersedes ev_b.
        con.execute("INSERT INTO memory_record_evidence(record_id,evidence_id,role,excerpt)"
                    " VALUES(?, 'ev_b', 'supersedes', 'Workshop moved to Room B')", (ids["Room B"],))
        # Edge at a stale target.
        con.execute("INSERT INTO memory_record_evidence(record_id,evidence_id,role,excerpt)"
                    " VALUES(?, 'ev_s', 'supersedes', 'Legacy statement here')", (ids["Room B"],))
        rejected: list = []
        assert apply_supersession_links(con, store.folder_id, rejected=rejected) == []
        assert {entry["reason"] for entry in rejected} == {
            "self-referential supersession link", "target not current"}
        assert con.execute("SELECT status FROM memory_records WHERE id=?",
                           (ids["Room A"],)).fetchone()["status"] == "decided"
        assert con.execute("SELECT status FROM memory_records WHERE id=?",
                           (ids["Legacy"],)).fetchone()["status"] == "stale"


# Regression: a structurally valid edge across unrelated matters must not
# demote the old record; the rejection stays inspectable.
def test_unrelated_statement_cannot_supersede_existing_decision(tmp_path):
    store = Store(tmp_path / "unrel.sqlite3")
    store.add_file("a.txt", [("ev_old", "The workshop is in Room A")])
    store.add_file("b.txt", [("ev_catering", "Catering budget is Rs 5000 confirmed")])
    store.add_records("run-1", [
        candidate("Workshop Room A", "workshop room", [("ev_old", "workshop is in Room A")],
                  record_type="decision", status="decided", date_value=None, date_raw=None),
        candidate("Catering budget", "catering budget",
                  [("ev_catering", "Catering budget is Rs 5000")],
                  detail="Catering budget", record_type="decision", status="decided",
                  date_value=None, date_raw=None,
                  evidence=[{"evidence_id": "ev_catering", "role": "supports",
                             "excerpt": "Catering budget is Rs 5000"},
                            {"evidence_id": "ev_old", "role": "supersedes",
                             "excerpt": "workshop is in Room A"}]),
    ])
    with store.db.transaction() as con:
        ids = {r["title"]: r["id"] for r in con.execute("SELECT id,title FROM memory_records")}
        rejected: list = []
        stepped = apply_supersession_links(con, store.folder_id, rejected=rejected)
        assert stepped == []
        assert any("unrelated" in entry["reason"] for entry in rejected)
        assert con.execute("SELECT status FROM memory_records WHERE id=?",
                           (ids["Workshop Room A"],)).fetchone()["status"] == "decided"


def test_paraphrased_replacement_with_disjoint_key_phrase_applies(tmp_path):
    store = Store(tmp_path / "para.sqlite3")
    store.add_file("a.txt", [("ev_old", "The workshop is in Room A")])
    store.add_file("b.txt", [("ev_new", "The workshop venue changed to Room B")])
    store.add_records("run-1", [
        candidate("Workshop Room A", "workshop room", [("ev_old", "workshop is in Room A")],
                  record_type="decision", status="decided", date_value=None, date_raw=None),
        candidate("Workshop venue Room B", "venue changed",
                  [("ev_new", "workshop venue changed to Room B")],
                  detail="Workshop venue Room B", record_type="decision", status="decided",
                  date_value=None, date_raw=None,
                  evidence=[{"evidence_id": "ev_new", "role": "supports",
                             "excerpt": "workshop venue changed to Room B"},
                            {"evidence_id": "ev_old", "role": "supersedes",
                             "excerpt": "workshop is in Room A"}]),
    ])
    with store.db.transaction() as con:
        ids = {r["title"]: r["id"] for r in con.execute("SELECT id,title FROM memory_records")}
        rejected: list = []
        stepped = apply_supersession_links(con, store.folder_id, rejected=rejected)
        assert stepped == [ids["Workshop Room A"]]
        assert rejected == []
        assert con.execute("SELECT status FROM memory_records WHERE id=?",
                           (ids["Workshop Room A"],)).fetchone()["status"] == "superseded"


def test_extraction_reports_rejected_links(tmp_path):
    store = room_fixture(tmp_path / "rep.sqlite3")
    new = extracted(candidate("Workshop Room B", "workshop room moved",
                              [("ev_new", "workshop moved to Room B")],
                              detail="Workshop Room B",
                              record_type="decision", status="decided",
                              date_value=None, date_raw=None))
    new["evidence"].append({"evidence_id": "ev_new", "role": "supersedes",
                            "excerpt": "workshop moved to Room B"})
    summary = run_extraction(store.db, store.folder_id, "run-1", store.settings, StubClient([new]))
    assert summary["superseded"] == []
    assert [entry["reason"] for entry in summary["rejected_supersession"]] == [
        "self-referential supersession link"]


def test_superseded_units_cannot_ground_settled_answers(tmp_path):
    store = Store(tmp_path / "hist.sqlite3")
    store.add_file("old.txt", [("ev_old", "The deadline is March 3rd")])
    store.add_file("new.txt", [("ev_new", "The deadline has moved")])
    store.add_records("run-1", [
        candidate("Deadline March 3rd", "deadline march",
                  [("ev_old", "deadline is March 3rd")],
                  date_value="2026-03-03", date_raw="March 3rd", status="open"),
        candidate("Deadline moved", "deadline moved",
                  [("ev_new", "deadline has moved")],
                  detail="Deadline moved", record_type="decision", status="decided",
                  date_value=None, date_raw=None,
                  evidence=[{"evidence_id": "ev_new", "role": "supports",
                             "excerpt": "deadline has moved"},
                            {"evidence_id": "ev_old", "role": "supersedes",
                             "excerpt": "deadline is March 3rd"}]),
    ])
    with store.db.transaction() as con:
        assert apply_supersession_links(con, store.folder_id) != []
    result = store.ask("when is the deadline?")
    assert result["status"] != "answered"
    assert result["values"] == []
    old_card = next(card for card in result["evidence"] if card["id"] == "ev_old")
    assert old_card["historical"] is True
    assert "March 3rd" in old_card["quote"]


def test_semantic_citing_only_history_is_demoted(tmp_path, monkeypatch):
    from yaad.llm import meta_client

    store = Store(tmp_path / "sem.sqlite3")
    store.settings = Settings(db_path=tmp_path / "sem.sqlite3", cloud_enabled=True,
                              model_api_key="stub-key-for-tests")
    store.add_file("old.txt", [("ev_old", "The deadline is March 3rd")])
    store.add_file("new.txt", [("ev_new", "The deadline has moved, see the office board")])
    store.add_records("run-1", [
        candidate("Deadline March 3rd", "deadline march",
                  [("ev_old", "deadline is March 3rd")],
                  date_value="2026-03-03", date_raw="March 3rd", status="open"),
        candidate("Deadline moved", "deadline moved",
                  [("ev_new", "deadline has moved")],
                  detail="Deadline moved", record_type="decision", status="decided",
                  date_value=None, date_raw=None,
                  evidence=[{"evidence_id": "ev_new", "role": "supports",
                             "excerpt": "deadline has moved"},
                            {"evidence_id": "ev_old", "role": "supersedes",
                             "excerpt": "deadline is March 3rd"}]),
    ])
    with store.db.transaction() as con:
        assert apply_supersession_links(con, store.folder_id) != []

    seen: list = []

    class HistoryCitingClient:
        last_call = {"provider": "Meta Model API", "model": "muse-spark-1.3", "status_code": 200,
                     "success": True, "duration_ms": 4, "input_tokens": 40, "output_tokens": 8,
                     "cached_tokens": 0}

        def __init__(self, _settings):
            pass

        def reason_json(self, messages, schema, prompt_id):
            import json as _json
            seen.append(_json.loads(messages[0]["content"]))
            return {"status": "answered", "answer": "The deadline is March 3rd.",
                    "claims": [{"claim": "The deadline is March 3rd.",
                                "evidence_ids": ["ev_old"]}]}

    monkeypatch.setattr(meta_client, "MetaClient", HistoryCitingClient)
    result = store.ask("when is the deadline?")
    assert result["status"] == "unverified"
    assert {source["evidence_id"]: source["historical"] for source in seen[0]["sources"]}["ev_old"] is True


def test_consent_revocation_still_blocks_new_evidence(tmp_path):
    store = room_fixture(tmp_path / "cons.sqlite3")
    old = extracted(candidate("Workshop Room A", "workshop room",
                              [("ev_old", "workshop is in Room A")],
                              record_type="decision", status="decided",
                              date_value=None, date_raw=None))
    new = extracted(candidate("Workshop Room B", "workshop room moved",
                              [("ev_new", "workshop moved to Room B")],
                              detail="Workshop Room B",
                              record_type="decision", status="decided",
                              date_value=None, date_raw=None))
    new["evidence"].append({"evidence_id": "ev_old", "role": "supersedes",
                            "excerpt": "workshop is in Room A"})
    run_extraction(store.db, store.folder_id, "run-1", store.settings, StubClient([old, new]))
    with store.db.transaction() as con:
        con.execute("UPDATE files SET cloud_consent=0 WHERE path='/src/b.txt'")
    store.settings = Settings(db_path=tmp_path / "cons.sqlite3", cloud_enabled=False)
    result = store.ask("which room is the workshop in?")
    assert result["memory"]["used"] is False
    assert result["status"] != "answered"
    old_card = next(card for card in result["evidence"] if card["id"] == "ev_old")
    assert old_card["historical"] is True

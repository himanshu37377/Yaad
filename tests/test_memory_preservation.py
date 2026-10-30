"""Evidence preservation and source-change lifecycle.

Union semantics: a later run that cites fewer references must not discard
previously validated corroboration. Old rows survive only while they
revalidate against live units; drops are reported in pruned_evidence, never
silent. Untouched records keep their rows (provenance) and go stale rather
than appearing current. Deterministic fixtures, stubbed client, no spend.
"""
import os
import tempfile
from pathlib import Path

os.environ.setdefault("YAAD_DB_PATH", str(Path(tempfile.gettempdir()) / "yaad-memory-preserve-tests.sqlite3"))

import pytest

from yaad.api import main
from yaad.config import Settings
from yaad.db import Database
from yaad.memory.extract import run_extraction
from yaad.memory.records import mark_stale, resolving_supports, upsert_records


class StubClient:
    def __init__(self, records):
        self.records = records
        self.last_call = {"provider": "Meta Model API", "model": "muse-spark-1.3",
                          "status_code": 200, "success": True, "duration_ms": 5,
                          "input_tokens": 900, "output_tokens": 300, "cached_tokens": 0}

    def reason_json(self, messages, schema, prompt_id):
        return {"records": self.records}


TEXTS = {
    "ev_a": "Deliver the draft by March 3rd",
    "ev_b": "Draft deadline March 3rd confirmed",
    "ev_c": "Unrelated planning note",
}


def candidate(excerpts, **over):
    base = {"record_type": "deadline", "title": "Draft due", "detail": "Draft due March 3rd.",
            "date_value": "2026-03-03", "date_raw": "March 3rd", "assignee": "",
            "status": "open", "statement_kind": "explicit", "key_phrase": "deliver draft",
            "evidence": [{"evidence_id": eid, "role": "supports", "excerpt": text}
                         for eid, text in excerpts]}
    base.update(over)
    return base


@pytest.fixture
def store(tmp_path):
    database = Database(tmp_path / "preserve.sqlite3")
    with database.transaction() as con:
        con.execute("INSERT INTO folders(path,cloud_consent) VALUES('/src',1)")
        folder_id = con.execute("SELECT id FROM folders").fetchone()["id"]
        con.execute("INSERT INTO files(folder_id,path,sha256,kind,size,mtime,status,cloud_consent)"
                    " VALUES(?,?,?,?,?,?,?,?)",
                    (folder_id, "/src/n.txt", "0" * 64, "text", 10, 0.0, "indexed", 1))
        file_id = con.execute("SELECT id FROM files").fetchone()["id"]
        for eid, text in TEXTS.items():
            con.execute("INSERT INTO units(file_id,evidence_id,kind,locator,text) VALUES(?,?,?,?,?)",
                        (file_id, eid, "text_span", '{"type":"text_span"}', text))
    settings = Settings(db_path=tmp_path / "preserve.sqlite3", cloud_enabled=True,
                        model_api_key="stub-key-for-tests")
    return database, folder_id, settings


def rows(database, record_id=None):
    with database.connect() as con:
        sql = "SELECT evidence_id,role,excerpt FROM memory_record_evidence"
        args: list = []
        if record_id is not None:
            sql += " WHERE record_id=?"
            args.append(record_id)
        return [dict(r) for r in con.execute(sql, args)]


def test_later_run_with_fewer_links_preserves_corroboration(store):
    database, folder_id, settings = store
    run_extraction(database, folder_id, "run-1", settings, StubClient(
        [candidate([("ev_a", TEXTS["ev_a"]), ("ev_b", TEXTS["ev_b"])])]))
    summary = run_extraction(database, folder_id, "run-2", settings, StubClient(
        [candidate([("ev_a", TEXTS["ev_a"])])]))
    assert summary["pruned_evidence"] == []
    assert {(r["evidence_id"], r["role"]) for r in rows(database)} == {
        ("ev_a", "supports"), ("ev_b", "supports")}
    with database.connect() as con:
        record_id = con.execute("SELECT id FROM memory_records").fetchone()["id"]
        assert resolving_supports(con, record_id) == 2


def test_retouch_drops_only_invalid_rows_and_reports_them(store):
    database, folder_id, settings = store
    run_extraction(database, folder_id, "run-1", settings, StubClient(
        [candidate([("ev_a", TEXTS["ev_a"]), ("ev_b", TEXTS["ev_b"])])]))
    with database.transaction() as con:
        con.execute("DELETE FROM units WHERE evidence_id='ev_b'")
    summary = run_extraction(database, folder_id, "run-2", settings, StubClient(
        [candidate([("ev_a", TEXTS["ev_a"])])]))
    assert summary["pruned_evidence"] == [{"record_id": 1, "evidence_id": "ev_b",
                                           "role": "supports",
                                           "reason": "evidence_id unresolved"}]
    assert {r["evidence_id"] for r in rows(database)} == {"ev_a"}
    with database.connect() as con:
        assert con.execute("SELECT status FROM memory_records").fetchone()["status"] == "open"


def test_untouched_record_keeps_dead_rows_but_goes_stale(store, monkeypatch):
    database, folder_id, settings = store
    run_extraction(database, folder_id, "run-1", settings, StubClient(
        [candidate([("ev_a", TEXTS["ev_a"])])]))
    with database.transaction() as con:
        con.execute("DELETE FROM units WHERE evidence_id='ev_a'")
        assert mark_stale(con, folder_id) == [1]
    # Provenance retained: the row still cites the vanished unit.
    assert [r["evidence_id"] for r in rows(database)] == ["ev_a"]
    monkeypatch.setattr(main, "db", database)
    record = main.list_memory(folder_id=folder_id)["records"][0]
    assert record["status"] == "stale"
    assert record["evidence"][0]["file"] is None
    assert record["resolving_supports"] == 0
    assert record["serving"]["verdict"] == "excluded"


def test_changed_text_with_same_id_stops_supporting(store, monkeypatch):
    database, folder_id, settings = store
    run_extraction(database, folder_id, "run-1", settings, StubClient(
        [candidate([("ev_a", TEXTS["ev_a"])])]))
    with database.transaction() as con:
        # Same evidence_id, rewritten text (e.g. model redescription).
        con.execute("UPDATE units SET text='Completely rewritten without the claim' WHERE evidence_id='ev_a'")
        assert mark_stale(con, folder_id) == [1]
    monkeypatch.setattr(main, "db", database)
    record = main.list_memory(folder_id=folder_id)["records"][0]
    assert record["serving"]["verdict"] == "excluded"


def test_consent_revocation_does_not_rewrite_history(store):
    database, folder_id, settings = store
    run_extraction(database, folder_id, "run-1", settings, StubClient(
        [candidate([("ev_a", TEXTS["ev_a"]), ("ev_c", TEXTS["ev_c"])])]))
    with database.transaction() as con:
        con.execute("INSERT INTO files(folder_id,path,sha256,kind,size,mtime,status,cloud_consent)"
                    " VALUES(?,?,?,?,?,?,?,?)",
                    (folder_id, "/src/p.txt", "1" * 64, "text", 10, 0.0, "indexed", 1))
        other = con.execute("SELECT id FROM files WHERE path='/src/p.txt'").fetchone()["id"]
        con.execute("UPDATE units SET file_id=? WHERE evidence_id='ev_c'", (other,))
        con.execute("UPDATE files SET cloud_consent=0 WHERE id=?", (other,))
    summary = run_extraction(database, folder_id, "run-2", settings, StubClient(
        [candidate([("ev_a", TEXTS["ev_a"])])]))
    # ev_c still exists with identical text: kept despite revoked consent.
    assert summary["pruned_evidence"] == []
    assert {r["evidence_id"] for r in rows(database)} == {"ev_a", "ev_c"}


def test_union_and_conflict_interplay_keeps_evidence(store):
    database, folder_id, settings = store
    run_extraction(database, folder_id, "run-1", settings, StubClient(
        [candidate([("ev_a", TEXTS["ev_a"])], key_phrase="march draft")]))
    moved = candidate([("ev_b", TEXTS["ev_b"])], title="Draft due moved",
                      detail="Draft moved.", date_value="2026-03-10", date_raw=None,
                      key_phrase="march draft")
    run_extraction(database, folder_id, "run-2", settings, StubClient(
        [candidate([("ev_b", TEXTS["ev_b"])], key_phrase="march draft"), moved]))
    with database.connect() as con:
        statuses = {r["title"]: (r["status"], resolving_supports(con, r["id"]))
                    for r in con.execute("SELECT id,title,status FROM memory_records")}
    assert statuses == {"Draft due": ("conflicted", 2), "Draft due moved": ("conflicted", 1)}


def test_unchanged_repeat_adds_no_duplicate_rows(store):
    database, folder_id, settings = store
    cand = candidate([("ev_a", TEXTS["ev_a"]), ("ev_b", TEXTS["ev_b"])])
    run_extraction(database, folder_id, "run-1", settings, StubClient([cand]))
    run_extraction(database, folder_id, "run-2", settings, StubClient([cand]))
    with database.connect() as con:
        assert con.execute("SELECT COUNT(*) n FROM memory_records").fetchone()["n"] == 1
        assert con.execute("SELECT COUNT(*) n FROM memory_record_evidence").fetchone()["n"] == 2
        assert con.execute("SELECT extraction_run FROM memory_records").fetchone()[0] == "run-2"

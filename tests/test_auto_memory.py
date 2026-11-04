"""Automatic memory: learns after indexing and from user statements.

All model access is stubbed; gates, provenance, conflicts, supersession,
and failure isolation run against the real pipelines (no network, no
spend)."""
import pytest
from fastapi import HTTPException

from yaad.api import main
from yaad.config import Settings
from yaad.db import Database
from yaad.memory.auto import (
    auto_extract_folder,
    learn_from_message,
    schedule_auto_memory,
    schedule_conversation_learning,
    wait_auto_memory,
)
from yaad.memory.extract import run_extraction


class StubClient:
    def __init__(self, records):
        self.records = records
        self.calls = 0
        self.last_call = {"provider": "Meta Model API", "model": "muse-spark-1.3",
                          "status_code": 200, "success": True, "duration_ms": 5,
                          "input_tokens": 900, "output_tokens": 300, "cached_tokens": 0}

    def reason_json(self, messages, schema, prompt_id):
        self.calls += 1
        return {"records": self.records}


class RaisingClient:
    last_call = {}

    def reason_json(self, messages, schema, prompt_id):
        raise RuntimeError("provider unavailable")


def deadline_candidate(evidence_id):
    return {"record_type": "deadline", "title": "Draft due",
            "detail": "Draft due March 3rd.", "date_value": "2026-03-03",
            "date_raw": "March 3rd", "assignee": "", "status": "open",
            "statement_kind": "explicit", "key_phrase": "deliver draft",
            "evidence": [{"evidence_id": evidence_id, "role": "supports",
                          "excerpt": "Deliver the draft by March 3rd"}]}


@pytest.fixture
def store(tmp_path):
    database = Database(tmp_path / "auto.sqlite3")
    settings = Settings(db_path=tmp_path / "auto.sqlite3", cloud_enabled=True,
                        model_api_key="stub-key-for-tests")
    with database.transaction() as con:
        folder_id = con.execute(
            "INSERT INTO folders(path,cloud_consent) VALUES(?,?)",
            (str(tmp_path / "docs"), 1)).lastrowid
        file_id = con.execute(
            """INSERT INTO files(folder_id,path,sha256,kind,size,mtime,status,cloud_consent)
               VALUES(?,?,?,?,?,?,?,?)""",
            (folder_id, str(tmp_path / "docs" / "plan.txt"), "a" * 64,
             "text", 10, 1.0, "indexed", 1)).lastrowid
        for evidence_id, text in [
                ("ev_a1", "The event is planned."),
                ("ev_a2", "Deliver the draft by March 3rd."),
                ("ev_a3", "Update: draft moved to March 10th instead.")]:
            con.execute(
                "INSERT INTO units(file_id,evidence_id,kind,locator,text) VALUES(?,?,?,?,?)",
                (file_id, evidence_id, "text_span", "{}", text)).lastrowid
    return database, settings, folder_id


def seed_deadline(database, settings, folder_id):
    run_extraction(database, folder_id, "seed", settings,
                   StubClient([deadline_candidate("ev_a2")]))
    with database.connect() as con:
        return con.execute("SELECT id FROM memory_records").fetchone()["id"]


def record(database, record_id):
    with database.connect() as con:
        return dict(con.execute("SELECT * FROM memory_records WHERE id=?",
                                (record_id,)).fetchone())


# --- automatic extraction after indexing ---------------------------------

def test_auto_extract_learns_once_then_watermark_skips(store):
    database, settings, folder_id = store
    stub = StubClient([deadline_candidate("ev_a2")])
    summary = auto_extract_folder(database, settings, folder_id, client=stub)
    assert summary["accepted"] == 1 and stub.calls == 1
    # Evidence unchanged since the successful pass: no second model call.
    assert auto_extract_folder(database, settings, folder_id, client=stub) is None
    assert stub.calls == 1
    with database.connect() as con:
        assert con.execute("SELECT COUNT(*) c FROM memory_records").fetchone()["c"] == 1


def test_auto_extract_respects_cloud_gate_and_consent(store, tmp_path):
    database, settings, folder_id = store
    off = Settings(db_path=settings.db_path, cloud_enabled=False,
                   model_api_key="stub-key-for-tests")
    assert auto_extract_folder(database, off, folder_id,
                               client=StubClient([deadline_candidate("ev_a2")])) is None
    with database.transaction() as con:
        con.execute("UPDATE folders SET cloud_consent=0 WHERE id=?", (folder_id,))
    assert auto_extract_folder(database, settings, folder_id,
                               client=StubClient([deadline_candidate("ev_a2")])) is None
    with database.connect() as con:
        assert con.execute("SELECT COUNT(*) c FROM memory_records").fetchone()["c"] == 0


def test_auto_extract_failure_is_isolated_and_retryable(store):
    database, settings, folder_id = store
    failed = auto_extract_folder(database, settings, folder_id, client=RaisingClient())
    assert failed["error"] == "RuntimeError"
    with database.connect() as con:
        assert con.execute("SELECT COUNT(*) c FROM memory_records").fetchone()["c"] == 0
        assert con.execute("SELECT COUNT(*) c FROM units").fetchone()["c"] == 3
    # Failure did not set the watermark: a later healthy pass still learns.
    summary = auto_extract_folder(database, settings, folder_id,
                                  client=StubClient([deadline_candidate("ev_a2")]))
    assert summary["accepted"] == 1


def test_scheduled_auto_memory_runs_in_background(store):
    database, settings, folder_id = store
    schedule_auto_memory(database, settings, [folder_id],
                         client=StubClient([deadline_candidate("ev_a2")]))
    wait_auto_memory(timeout=10)
    with database.connect() as con:
        assert con.execute("SELECT COUNT(*) c FROM memory_records").fetchone()["c"] == 1


# --- conversation learning ------------------------------------------------

def test_statement_supersedes_with_provenance_and_history(store):
    database, settings, folder_id = store
    old_id = seed_deadline(database, settings, folder_id)
    outcome = learn_from_message(
        database, settings, folder_id,
        "Update: the draft deadline moved to March 10, 2026.",
        client=StubClient([]))
    assert outcome["action"] == "superseded" and outcome["rival_id"] == old_id
    assert record(database, old_id)["status"] == "superseded"
    new = record(database, outcome["record_id"])
    assert new["date_value"] == "2026-03-10" and new["status"] == "open"
    with database.connect() as con:
        provenance = con.execute(
            """SELECT f.path FROM memory_record_evidence e
               JOIN units u ON u.evidence_id=e.evidence_id
               JOIN files f ON f.id=u.file_id
               WHERE e.record_id=? AND e.role='supports'""",
            (new["id"],)).fetchone()
        transitions = [row["transition"] for row in con.execute(
            "SELECT transition FROM memory_transitions WHERE record_id=?", (old_id,))]
    assert provenance["path"].startswith("yaad-conversation://")
    assert "superseded" in transitions


def test_statement_without_replacement_language_conflicts(store):
    database, settings, folder_id = store
    old_id = seed_deadline(database, settings, folder_id)
    outcome = learn_from_message(
        database, settings, folder_id, "The draft is due March 10, 2026.",
        client=StubClient([]))
    assert outcome["action"] == "conflict"
    assert record(database, old_id)["status"] == "conflicted"
    assert record(database, outcome["record_id"])["status"] == "conflicted"


def test_questions_and_restatements_never_teach(store):
    database, settings, folder_id = store
    seed_deadline(database, settings, folder_id)
    assert learn_from_message(database, settings, folder_id,
                              "What is the draft deadline?",
                              client=StubClient([]))["action"] == "ignored"
    assert learn_from_message(database, settings, folder_id,
                              "The draft is due March 3, 2026.",
                              client=StubClient([]))["action"] == "ignored"
    with database.connect() as con:
        assert con.execute("SELECT COUNT(*) c FROM memory_records").fetchone()["c"] == 1


def test_scheduled_conversation_learning_is_background(store):
    database, settings, folder_id = store
    seed_deadline(database, settings, folder_id)
    schedule_conversation_learning(
        database, settings, folder_id,
        "Update: the draft deadline moved to March 10, 2026.")
    wait_auto_memory(timeout=10)
    with database.connect() as con:
        statuses = {row["date_value"]: row["status"] for row in
                    con.execute("SELECT date_value,status FROM memory_records")}
    assert statuses == {"2026-03-03": "superseded", "2026-03-10": "open"}


# --- API wiring -------------------------------------------------------------

def test_index_and_ask_schedule_learning(monkeypatch):
    captured = {}
    monkeypatch.setattr(main, "indexer",
                        type("FakeIndexer", (), {
                            "index_folder": lambda self, fid=None: {"indexed": 0}})())
    monkeypatch.setattr(main, "schedule_auto_memory",
                        lambda db, s, ids: captured.setdefault("indexed", ids))
    monkeypatch.setattr(main, "answer_question",
                        lambda db, q, fid, s, t=None: {"status": "not_found"})
    monkeypatch.setattr(main, "schedule_conversation_learning",
                        lambda db, s, fid, q: captured.setdefault("asked", (fid, q)))
    main.run_index(main.IndexRequest(folder_id=3))
    main.ask(main.AskRequest(question="What changed?", folder_id=3))
    assert captured["indexed"] == [3]
    assert captured["asked"] == (3, "What changed?")


def test_delete_memory_endpoint_removes_record(store, monkeypatch):
    database, settings, folder_id = store
    record_id = seed_deadline(database, settings, folder_id)
    monkeypatch.setattr(main, "db", database)
    assert main.delete_memory(record_id) == {"deleted": record_id}
    with database.connect() as con:
        assert con.execute("SELECT COUNT(*) c FROM memory_records").fetchone()["c"] == 0
    with pytest.raises(HTTPException) as missing:
        main.delete_memory(record_id)
    assert missing.value.status_code == 404

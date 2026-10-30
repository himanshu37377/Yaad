"""Verification stage: serving safety, run visibility, operational safety.

All stubbed (no network, no spend); proves the serving contract over stored
state, rejection visibility, failure atomicity, and that indexing/answering
never trigger extraction.
"""
import os
import tempfile
from pathlib import Path

import pytest
from fastapi import HTTPException

os.environ.setdefault("YAAD_DB_PATH", str(Path(tempfile.gettempdir()) / "yaad-memory-serving-tests.sqlite3"))

from yaad.api import main
from yaad.config import Settings
from yaad.db import Database
import yaad.llm.meta_client as meta_client
from yaad.memory.extract import run_extraction
from yaad.memory.records import (
    apply_supersession_links,
    mark_conflicts,
    mark_stale,
    upsert_records,
)
from yaad.query.answer import answer_question
from yaad.scan.worker import Indexer


class StubClient:
    def __init__(self, records=None, error=None):
        self.records = records or []
        self.error = error
        self.last_call = {"provider": "Meta Model API", "model": "muse-spark-1.3",
                          "status_code": 200, "success": True, "duration_ms": 5,
                          "input_tokens": 900, "output_tokens": 300, "cached_tokens": 0}

    def reason_json(self, messages, schema, prompt_id):
        if self.error:
            raise self.error
        return {"records": self.records}


def candidate(**over):
    base = {"record_type": "decision", "title": "T", "detail": "D",
            "date_value": None, "date_raw": None, "assignee": None,
            "status": "decided", "statement_kind": "explicit", "key_phrase": "alpha beta",
            "evidence": [{"evidence_id": "ev1", "role": "supports", "excerpt": "alpha beta gamma"}]}
    base.update(over)
    return base


@pytest.fixture
def store(tmp_path):
    database = Database(tmp_path / "srv.sqlite3")
    folder = tmp_path / "src"
    folder.mkdir()
    settings = Settings(db_path=tmp_path / "srv.sqlite3", cloud_enabled=True,
                        model_api_key="stub-key-for-tests")
    indexer = Indexer(database, Settings(db_path=tmp_path / "srv.sqlite3", cloud_enabled=False))
    folder_id = indexer.add_folder(str(folder), True)
    with database.transaction() as con:
        con.execute("INSERT INTO files(folder_id,path,sha256,kind,size,mtime,status,cloud_consent)"
                    " VALUES(?,?,?,?,?,?,?,?)",
                    (folder_id, str(folder / "n.txt"), "0" * 64, "text", 10, 0.0, "indexed", 1))
        file_id = con.execute("SELECT id FROM files").fetchone()["id"]
        con.execute("INSERT INTO units(file_id,evidence_id,kind,locator,text) VALUES(?,?,?,?,?)",
                    (file_id, "ev1", "text_span", '{"type":"text_span"}', "alpha beta gamma"))
        con.execute("INSERT INTO units(file_id,evidence_id,kind,locator,text) VALUES(?,?,?,?,?)",
                    (file_id, "ev2", "text_span", '{"type":"text_span"}', "alpha beta gamma delta"))
    return database, indexer, folder, folder_id, settings


def verdicts(database, folder_id, monkeypatch):
    monkeypatch.setattr(main, "db", database)
    return {r["title"]: r for r in main.list_memory(folder_id=folder_id)["records"]}


# --- B: serving eligibility over stored state ---

def test_unresolving_and_stale_records_are_excluded(store, monkeypatch):
    database, _, _, folder_id, _ = store
    with database.transaction() as con:
        upsert_records(con, folder_id, "run-1",
                       [candidate(title="Vanishing claim")],
                       {"ev1": "alpha beta gamma"})
        con.execute("DELETE FROM units WHERE evidence_id='ev1'")
        assert mark_stale(con, folder_id) != []
    rows = verdicts(database, folder_id, monkeypatch)
    assert rows["Vanishing claim"]["status"] == "stale"
    assert rows["Vanishing claim"]["resolving_supports"] == 0
    assert rows["Vanishing claim"]["serving"]["verdict"] == "excluded"


def test_conflicted_records_are_excluded_but_inspectable_as_a_pair(store, monkeypatch):
    database, _, _, folder_id, _ = store
    first = candidate(record_type="deadline", title="Due March", status="open",
                      date_value="2026-03-03", date_raw=None,
                      key_phrase="alpha beta",
                      evidence=[{"evidence_id": "ev1", "role": "supports", "excerpt": "alpha beta gamma"}])
    second = candidate(record_type="deadline", title="Due April", status="open",
                       date_value="2026-04-04", date_raw=None,
                       key_phrase="alpha beta",
                       evidence=[{"evidence_id": "ev2", "role": "supports", "excerpt": "alpha beta delta"}])
    with database.transaction() as con:
        # Same claim signature, different asserted dates: separate, then conflicted.
        upsert_records(con, folder_id, "run-1", [first, second],
                       {"ev1": "alpha beta gamma", "ev2": "alpha beta delta"})
        assert mark_conflicts(con, folder_id) != []
    rows = verdicts(database, folder_id, monkeypatch)
    assert {rows["Due March"]["status"], rows["Due April"]["status"]} == {"conflicted"}
    assert rows["Due March"]["serving"]["verdict"] == "excluded"
    assert rows["Due April"]["serving"]["verdict"] == "excluded"
    assert "conflict" in rows["Due March"]["serving"]["reason"]


def test_superseded_proposal_inferred_and_unresolved_verdicts(store, monkeypatch):
    database, _, _, folder_id, _ = store
    old = candidate(title="Old hall", key_phrase="alpha gamma")
    new = candidate(title="New hall", key_phrase="beta delta",
                    evidence=[{"evidence_id": "ev2", "role": "supports", "excerpt": "beta gamma delta"},
                              {"evidence_id": "ev1", "role": "supersedes", "excerpt": "alpha beta gamma"}])
    proposal = candidate(record_type="commitment", title="Maybe hall", status="open",
                         statement_kind="proposal", key_phrase="alpha gamma",
                         evidence=[{"evidence_id": "ev2", "role": "supports",
                                    "excerpt": "alpha beta gamma delta"}])
    inferred = candidate(title="Inferred hall", statement_kind="inferred", key_phrase="gamma delta",
                         evidence=[{"evidence_id": "ev2", "role": "supports",
                                    "excerpt": "beta gamma delta"}])
    question = candidate(record_type="open_question", title="Which hall?", status="open",
                         statement_kind="unresolved", key_phrase="alpha gamma",
                         evidence=[{"evidence_id": "ev2", "role": "supports",
                                    "excerpt": "alpha beta gamma delta"}])
    with database.transaction() as con:
        upsert_records(con, folder_id, "run-1", [old, new, proposal, inferred, question],
                       {"ev1": "alpha beta gamma", "ev2": "alpha beta gamma delta"})
        assert apply_supersession_links(con, folder_id) != []
    rows = verdicts(database, folder_id, monkeypatch)
    assert rows["Old hall"]["status"] == "superseded"
    assert rows["Old hall"]["serving"]["verdict"] == "excluded"
    assert rows["New hall"]["serving"]["verdict"] == "eligible"
    assert rows["Maybe hall"]["serving"]["verdict"] == "labeled"
    assert "propos" in rows["Maybe hall"]["serving"]["reason"]
    assert rows["Inferred hall"]["serving"]["verdict"] == "excluded"
    assert "nference" in rows["Inferred hall"]["serving"]["reason"]
    assert rows["Which hall?"]["serving"]["verdict"] == "undecided_only"


# --- C: run-result visibility and failure atomicity ---

def test_all_rejections_reported_with_counts_consistent(store, monkeypatch):
    database, indexer, folder, folder_id, settings = store
    (folder / "n.txt").write_text("alpha beta gamma", encoding="utf-8")
    indexer.index_folder(folder_id)
    with database.connect() as con:
        eid = con.execute("SELECT evidence_id FROM units").fetchone()["evidence_id"]
    good = dict(candidate(title="Kept", evidence=[{"evidence_id": eid, "role": "supports",
                                                   "excerpt": "alpha beta gamma"}]))
    bad_date = dict(candidate(title="Bad date", date_value="someday"))
    bad_evidence = dict(candidate(title="Bad evidence",
                                  evidence=[{"evidence_id": "ev-nope", "role": "supports",
                                             "excerpt": "alpha beta gamma"}]))
    monkeypatch.setattr(main, "db", database)
    monkeypatch.setattr(main, "settings", settings)
    monkeypatch.setattr(meta_client, "MetaClient", lambda _s: StubClient([good, bad_date, bad_evidence]))
    summary = main.extract_memory(main.MemoryExtractRequest(folder_id=folder_id))
    assert summary["returned"] == 3
    assert summary["accepted"] == 1
    assert summary["returned"] == summary["accepted"] + len(summary["rejected"])
    assert {r["title"] for r in summary["rejected"]} == {"Bad date", "Bad evidence"}
    assert all(r["errors"] for r in summary["rejected"])


def test_spend_cap_failure_persists_receipt_but_no_records(store, monkeypatch):
    database, indexer, folder, folder_id, settings = store
    (folder / "n.txt").write_text("alpha beta gamma", encoding="utf-8")
    indexer.index_folder(folder_id)
    with database.connect() as con:
        eid = con.execute("SELECT evidence_id FROM units").fetchone()["evidence_id"]
    client = StubClient([candidate(evidence=[{"evidence_id": eid, "role": "supports",
                                              "excerpt": "alpha beta gamma"}])])
    client.last_call["output_tokens"] = 10_000_000
    monkeypatch.setattr(main, "db", database)
    monkeypatch.setattr(main, "settings", settings)
    monkeypatch.setattr(meta_client, "MetaClient", lambda _s: client)
    with pytest.raises(HTTPException) as failed:
        main.extract_memory(main.MemoryExtractRequest(folder_id=folder_id))
    assert failed.value.status_code == 409
    with database.connect() as con:
        assert con.execute("SELECT COUNT(*) n FROM memory_records").fetchone()["n"] == 0
        row = con.execute("SELECT success,error_code FROM egress_log").fetchone()
        assert (row["success"], row["error_code"]) == (0, "spend_cap_exceeded")


def test_model_failure_persists_receipt_and_reports_error(store, monkeypatch):
    database, indexer, folder, folder_id, settings = store
    (folder / "n.txt").write_text("alpha beta gamma", encoding="utf-8")
    indexer.index_folder(folder_id)
    monkeypatch.setattr(main, "db", database)
    monkeypatch.setattr(main, "settings", settings)
    monkeypatch.setattr(meta_client, "MetaClient", lambda _s: StubClient(error=RuntimeError("provider down")))
    with pytest.raises(HTTPException) as failed:
        main.extract_memory(main.MemoryExtractRequest(folder_id=folder_id))
    assert failed.value.status_code == 409
    with database.connect() as con:
        assert con.execute("SELECT COUNT(*) n FROM memory_records").fetchone()["n"] == 0
        row = con.execute("SELECT success,error_code FROM egress_log").fetchone()
        assert (row["success"], row["error_code"]) == (0, "RuntimeError")


def test_unconsented_folder_refused_without_writes(store, monkeypatch):
    database, _, _, folder_id, settings = store
    with database.transaction() as con:
        con.execute("UPDATE folders SET cloud_consent=0 WHERE id=?", (folder_id,))
    monkeypatch.setattr(main, "db", database)
    monkeypatch.setattr(main, "settings", settings)
    monkeypatch.setattr(meta_client, "MetaClient", lambda _s: StubClient([]))
    with pytest.raises(HTTPException) as refused:
        main.extract_memory(main.MemoryExtractRequest(folder_id=folder_id))
    assert refused.value.status_code == 409
    with database.connect() as con:
        assert con.execute("SELECT COUNT(*) n FROM memory_records").fetchone()["n"] == 0
        assert con.execute("SELECT COUNT(*) n FROM egress_log").fetchone()["n"] == 0


# --- D: extraction is explicit-only ---

def test_indexing_never_triggers_extraction(store):
    database, indexer, folder, folder_id, _ = store
    (folder / "n.txt").write_text("alpha beta gamma. Deliverable due March 3rd.", encoding="utf-8")
    indexer.index_folder(folder_id)
    (folder / "n.txt").write_text("alpha beta gamma. Deliverable due March 10th instead.", encoding="utf-8")
    indexer.index_folder(folder_id)
    with database.connect() as con:
        assert con.execute("SELECT COUNT(*) n FROM memory_records").fetchone()["n"] == 0
        assert con.execute("SELECT COUNT(*) n FROM egress_log WHERE purpose='memory extraction'").fetchone()["n"] == 0


def test_answering_never_triggers_extraction(store, monkeypatch):
    from yaad.llm import meta_client

    database, indexer, folder, folder_id, settings = store
    (folder / "n.txt").write_text("alpha beta gamma", encoding="utf-8")
    indexer.index_folder(folder_id)
    seen = []

    class RecordingClient:
        last_call = {"provider": "Meta Model API", "model": "muse-spark-1.3", "status_code": 200,
                     "success": True, "duration_ms": 4, "input_tokens": 40, "output_tokens": 8}

        def __init__(self, _settings):
            pass

        def reason_json(self, messages, schema, prompt_id):
            seen.append(prompt_id)
            return {"status": "answered", "answer": "alpha beta gamma.",
                    "claims": [{"claim": "alpha beta gamma.", "evidence_ids": ["unused"]}]}

    monkeypatch.setattr(meta_client, "MetaClient", RecordingClient)
    answer_question(database, "what is alpha?", folder_id, settings)
    assert "memory-extraction-v1" not in seen
    with database.connect() as con:
        assert con.execute("SELECT COUNT(*) n FROM memory_records").fetchone()["n"] == 0
        assert con.execute("SELECT COUNT(*) n FROM egress_log WHERE purpose='memory extraction'").fetchone()["n"] == 0

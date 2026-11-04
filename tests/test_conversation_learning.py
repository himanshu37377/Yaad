"""Conversation learning trust rules (Phase 3 regression suite).

A user statement is evidence of what the user said -- never an
independent verification of the world. The pipeline therefore:

* validates a conversation candidate BEFORE persisting its evidence,
  so a rejected statement leaves no unit and no FTS trace;
* marks persisted statements with conversation provenance (file
  scheme + unit meta), and discloses that provenance when an answer
  rests solely on them;
* routes rival supersession through the same deterministic
  relationship validation as document-derived links, so a statement
  about a different subject cannot supersede it;
* still learns legitimate corrections (with replacement language,
  a statement supersedes through validation, with history).

Model access is stubbed or absent; the rival path is deterministic.
"""
import json

from test_memory_answers import Store, candidate

import yaad.memory.auto as auto
from yaad.config import Settings
from yaad.memory.auto import learn_from_message
from yaad.memory.records import apply_supersession_links  # noqa: F401  (parity reference)
from yaad.query.memory_answer import fetch_support_rows, plan_memory, synthesize
from yaad.query.retrieve import search

DOC_TEXT = "Deliver the draft by March 3rd."


def seeded_store(tmp_path, name="conv.sqlite3"):
    store = Store(tmp_path / name)
    store.settings = Settings(db_path=tmp_path / name, cloud_enabled=False)
    store.add_file("plan.txt", [("ev_doc", DOC_TEXT)])
    store.add_records("run-1", [
        candidate("Draft due", "deliver draft", [("ev_doc", "Deliver the draft by March 3rd")],
                  detail="Draft due March 3rd.",
                  date_value="2026-03-03", date_raw="March 3rd"),
    ])
    return store


def conversation_units(db):
    with db.connect() as con:
        return [dict(row) for row in con.execute(
            """SELECT u.evidence_id,u.text,u.meta FROM units u
               JOIN files f ON f.id=u.file_id
               WHERE f.path LIKE 'yaad-conversation://%'""")]


def record_statuses(db):
    with db.connect() as con:
        return {row["id"]: row["status"] for row in
                con.execute("SELECT id,status FROM memory_records")}


def test_correction_supersedes_with_conversation_provenance(tmp_path):
    store = seeded_store(tmp_path)
    outcome = learn_from_message(store.db, store.settings, store.folder_id,
                                 "Update: the draft deadline moved to March 10, 2026.")
    assert outcome["action"] == "superseded"
    statuses = record_statuses(store.db)
    assert statuses[outcome["rival_id"]] == "superseded"
    assert statuses[outcome["record_id"]] == "open"
    units = conversation_units(store.db)
    assert len(units) == 1
    assert json.loads(units[0]["meta"])["origin"] == "conversation"
    with store.db.connect() as con:
        transitions = [row["transition"] for row in con.execute(
            "SELECT transition FROM memory_transitions WHERE record_id=?",
            (outcome["rival_id"],))]
        support_files = [row["path"] for row in con.execute(
            """SELECT f.path FROM memory_record_evidence e
               JOIN units u ON u.evidence_id=e.evidence_id
               JOIN files f ON f.id=u.file_id
               WHERE e.record_id=? AND e.role='supports'""", (outcome["record_id"],))]
    assert "superseded" in transitions
    assert support_files and all(
        path.startswith("yaad-conversation://") for path in support_files)


def test_statement_without_replacement_language_conflicts_not_supersedes(tmp_path):
    store = seeded_store(tmp_path)
    outcome = learn_from_message(store.db, store.settings, store.folder_id,
                                 "The draft is due March 10, 2026.")
    assert outcome["action"] == "conflict"
    statuses = record_statuses(store.db)
    assert statuses[outcome["rival_id"]] == "conflicted"
    assert "superseded" not in statuses.values()


def test_rival_path_reports_lifecycle_decision(tmp_path):
    store = seeded_store(tmp_path)
    outcome = learn_from_message(store.db, store.settings, store.folder_id,
                                 "Update: the draft deadline moved to March 10, 2026.")
    assert outcome["action"] == "superseded"
    assert outcome["decision"]["kind"] == "supersession"
    assert outcome["decision"]["rival_record_id"] == outcome["rival_id"]
    assert outcome["decision"]["trust"] == 2
    fresh = seeded_store(tmp_path, name="conv-plain.sqlite3")
    plain = learn_from_message(fresh.db, fresh.settings, fresh.folder_id,
                               "The draft is due March 10, 2026.")
    assert plain["decision"]["kind"] == "contradiction"


def test_replacement_markers_match_lifecycle_taxonomy(tmp_path):
    """Every legacy replacement phrasing still routes to supersession
    through the shared taxonomy (parity, not new behavior)."""
    for message in ("The draft deadline increased to March 20, 2026.",
                    "The draft deadline shifted to March 20, 2026.",
                    "The draft deadline was superseded by March 20, 2026."):
        store = seeded_store(tmp_path, name=f"par-{abs(hash(message)) % 9999}.sqlite3")
        outcome = learn_from_message(store.db, store.settings, store.folder_id, message)
        assert outcome["decision"]["kind"] == "supersession", message
        assert outcome["action"] in ("superseded", "conflict", "created"), message


def test_cross_subject_statement_cannot_supersede(tmp_path):
    """Shared words (fest/fee) match a rival, but the statement names a
    different event: relationship validation must refuse the link,
    exactly as it would for a document-derived proposal."""
    store = Store(tmp_path / "cross.sqlite3")
    store.settings = Settings(db_path=tmp_path / "cross.sqlite3", cloud_enabled=False)
    store.add_file("fees.txt", [("ev_fee", "Autumn Fest registration fee is Rs 100 per person")])
    store.add_records("run-1", [
        candidate("Autumn Fest registration fee", "registration fee",
                  [("ev_fee", "Autumn Fest registration fee is Rs 100")],
                  detail="Autumn Fest registration fee is Rs 100",
                  record_type="decision", status="decided",
                  date_value=None, date_raw=None),
    ])
    outcome = learn_from_message(store.db, store.settings, store.folder_id,
                                 "Spring Fest fee increased to Rs 200")
    assert outcome["action"] != "superseded"
    with store.db.connect() as con:
        original = con.execute(
            "SELECT status FROM memory_records WHERE title LIKE 'Autumn%'").fetchone()
        superseded = con.execute(
            "SELECT COUNT(*) c FROM memory_transitions WHERE transition='superseded'"
        ).fetchone()["c"]
    assert original["status"] == "decided"
    assert superseded == 0


def test_rejected_candidate_persists_nothing(tmp_path, monkeypatch):
    store = seeded_store(tmp_path)
    monkeypatch.setattr(auto, "validate_candidate", lambda cand, units: ["forced failure"])
    outcome = learn_from_message(store.db, store.settings, store.folder_id,
                                 "Update: the draft deadline moved to March 10, 2026.")
    assert outcome["learned"] is False and outcome["action"] == "rejected"
    assert outcome["reasons"] == ["forced failure"]
    assert conversation_units(store.db) == []
    with store.db.connect() as con:
        assert con.execute("SELECT COUNT(*) c FROM memory_records").fetchone()["c"] == 1


class BogusClient:
    last_call = {"provider": "Meta Model API", "model": "muse-spark-1.3",
                 "status_code": 200, "success": True, "duration_ms": 3,
                 "input_tokens": 100, "output_tokens": 40, "cached_tokens": 0}

    def reason_json(self, messages, schema, prompt_id):
        return {"records": [{
            "record_type": "decision", "title": "Workshop budget",
            "detail": "Workshop budget Rs 9999", "date_value": "",
            "date_raw": "", "assignee": "Arjun", "status": "decided",
            "statement_kind": "explicit", "key_phrase": "workshop budget",
            "evidence": [{"evidence_id": "ev_missing", "role": "supports",
                          "excerpt": "budget is Rs 9999"}]}]}


def test_rejected_scoped_statement_leaves_no_fts_trace(tmp_path):
    """A new-matter statement whose extraction candidates all fail
    validation keeps existing only as the user's chat message: no
    record, no conversation unit, nothing for retrieval to surface."""
    store = Store(tmp_path / "scoped.sqlite3")
    settings = Settings(db_path=tmp_path / "scoped.sqlite3", cloud_enabled=True,
                        model_api_key="stub-key-for-tests")
    outcome = learn_from_message(store.db, settings, store.folder_id,
                                 "We decided the workshop budget is Rs 9999.",
                                 client=BogusClient())
    assert outcome["learned"] is False
    assert conversation_units(store.db) == []
    assert search(store.db, "budget 9999", store.folder_id, 10) == []
    with store.db.connect() as con:
        assert con.execute("SELECT COUNT(*) c FROM memory_records").fetchone()["c"] == 0


def test_repeated_statement_creates_no_duplicate(tmp_path):
    store = seeded_store(tmp_path)
    message = "Update: the draft deadline moved to March 10, 2026."
    first = learn_from_message(store.db, store.settings, store.folder_id, message)
    assert first["action"] == "superseded"
    second = learn_from_message(store.db, store.settings, store.folder_id, message)
    assert second["learned"] is False
    with store.db.connect() as con:
        rows = con.execute("SELECT date_value,status FROM memory_records").fetchall()
    assert len(rows) == 2
    assert [row["date_value"] for row in rows].count("2026-03-10") == 1
    assert len(conversation_units(store.db)) == 1


def test_conversation_only_answer_discloses_provenance(tmp_path):
    store = seeded_store(tmp_path)
    learn_from_message(store.db, store.settings, store.folder_id,
                       "Update: the draft deadline moved to March 10, 2026.")
    plan = plan_memory(store.db, "when is the draft due?", store.folder_id, [])
    assert plan["claims"], "the learned record must match the question"
    wanted = [eid for claim in plan["claims"] for eid in claim["evidence_ids"]]
    support_rows = fetch_support_rows(store.db, store.folder_id, wanted)
    result = synthesize("when is the draft due?", plan, support_rows)
    assert result is not None and result["status"] == "answered"
    assert result["answer"].startswith("Based on what you told me: ")
    assert "2026-03-10" in result["answer"] or "March 10" in result["answer"]

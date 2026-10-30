"""Temporal Memory Ledger: append-only history of validated transitions.

Every validated memory transition (creation, update, conflict mark/clear,
supersession, stale mark/restore) appends one entry preserving previous
and new state, timestamp, transition type, reason, and supporting
evidence. Ledger rows are never modified or removed by app logic; reruns
that change nothing append nothing. History is queryable per record,
folder, kind, and time window, served over the API, and summarized by a
narrow deterministic "what changed" answer path.

Deterministic fixtures on disposable databases; no model calls, no
production database contact.
"""
import os
import re
import tempfile
from pathlib import Path

os.environ.setdefault("YAAD_DB_PATH", str(Path(tempfile.gettempdir()) / "yaad-ledger-api-tests.sqlite3"))

import pytest

from test_memory_answers import Store, candidate
from yaad.memory.ledger import (
    list_transitions,
    previous_decision,
    record_history,
)

TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")


def fee_pair_store(path):
    store = Store(path)
    store.add_file("a.txt", [("ev_a", "Autumn Fest registration fee is Rs 150 per person")])
    store.add_file("b.txt", [("ev_b", "Autumn Fest registration fee is Rs 200 per person")])
    store.add_records("run-1", [
        candidate("Autumn Fest registration fee", "registration fee",
                  [("ev_a", "Autumn Fest registration fee is Rs 150")],
                  record_type="decision", status="decided", date_value=None, date_raw=None),
        candidate("Autumn Fest registration fee", "registration fee",
                  [("ev_b", "Autumn Fest registration fee is Rs 200")],
                  record_type="decision", status="decided", date_value=None, date_raw=None),
    ])
    return store


def transitions_of(store, **filters):
    with store.db.connect() as con:
        return list_transitions(con, store.folder_id, **filters)


# --- capture: creation and update ---

def test_creation_logged_with_state_evidence_and_source(tmp_path):
    store = fee_pair_store(tmp_path / "c1.sqlite3")
    entries = transitions_of(store, kinds="record_created")
    assert len(entries) == 2
    for entry in entries:
        assert entry["prev_state"] is None
        assert entry["new_state"]["title"] == "Autumn Fest registration fee"
        assert entry["new_state"]["status"] == "decided"
        assert entry["reason"] == "accepted by validated extraction"
        assert entry["source"] == "extraction:run-1"
        assert TIMESTAMP.match(entry["created_at"])
        (support,) = entry["evidence"]
        assert support["excerpt"].startswith("Autumn Fest registration fee is Rs")
        assert support["role"] == "supports"
    assert {e["new_state"]["amount_value"] for e in entries} == {"INR150", "INR200"}


def test_update_logged_only_on_material_change(tmp_path):
    store = Store(tmp_path / "c2.sqlite3")
    store.add_file("a.txt", [("ev_a", "The draft is due March 3rd")])
    base = candidate("Draft due March 3rd", "draft due",
                     [("ev_a", "draft is due March 3rd")])
    store.add_records("run-1", [base])
    assert len(transitions_of(store)) == 1
    # Identical re-extraction: ids refresh, nothing appended.
    store.add_records("run-2", [dict(base)])
    assert len(transitions_of(store)) == 1
    changed = dict(base, detail="Draft due March 3rd, all teams delayed.")
    store.add_records("run-3", [changed])
    entries = transitions_of(store, kinds="record_updated")
    assert len(entries) == 1
    (entry,) = entries
    assert entry["prev_state"]["detail"] == "Draft due March 3rd"
    assert entry["new_state"]["detail"] == "Draft due March 3rd, all teams delayed."
    assert entry["reason"] == "re-extraction changed detail"
    assert entry["source"] == "extraction:run-3"


def test_extraction_run_source_propagates(tmp_path):
    from yaad.config import Settings
    from yaad.memory.extract import run_extraction

    store = Store(tmp_path / "c3.sqlite3")
    store.settings = Settings(db_path=tmp_path / "c3.sqlite3", cloud_enabled=True,
                              model_api_key="stub-key-for-tests")
    store.add_file("a.txt", [("ev_a", "Autumn Fest registration fee is Rs 150 per person")])
    store.add_file("b.txt", [("ev_b", "Autumn Fest registration fee is Rs 200 per person")])

    class StubClient:
        last_call = {"provider": "Meta Model API", "model": "muse-spark-1.3", "status_code": 200,
                     "success": True, "duration_ms": 5, "input_tokens": 900,
                     "output_tokens": 300, "cached_tokens": 0}

        def reason_json(self, messages, schema, prompt_id):
            first = candidate("Autumn Fest registration fee", "registration fee",
                              [("ev_a", "Autumn Fest registration fee is Rs 150")],
                              record_type="decision", status="decided",
                              date_value="", date_raw="", assignee="")
            second = candidate("Autumn Fest registration fee", "registration fee",
                               [("ev_b", "Autumn Fest registration fee is Rs 200")],
                               record_type="decision", status="decided",
                               date_value="", date_raw="", assignee="")
            return {"records": [first, second]}

    run_extraction(store.db, store.folder_id, "run-9", store.settings, StubClient())
    entries = transitions_of(store)
    assert entries
    assert {entry["source"] for entry in entries} == {"extraction:run-9"}
    assert {entry["transition"] for entry in entries} == {"record_created", "conflict_marked"}


# --- capture: conflict lifecycle ---

def test_conflict_marked_and_cleared_logged(tmp_path):
    from yaad.memory.records import mark_conflicts, mark_stale

    store = fee_pair_store(tmp_path / "c4.sqlite3")
    with store.db.transaction() as con:
        assert len(mark_conflicts(con, store.folder_id)) == 1
        left, right = [row["id"] for row in con.execute("SELECT id FROM memory_records ORDER BY id")]
        marked = list_transitions(con, store.folder_id, kinds="conflict_marked")
        assert len(marked) == 2
        by_record = {entry["record_id"]: entry for entry in marked}
        assert by_record[left]["related_record_id"] == right
        assert by_record[left]["prev_state"]["status"] == "decided"
        assert by_record[left]["new_state"]["status"] == "conflicted"
        assert "rival amounts INR150 vs INR200" in by_record[left]["reason"]
        assert {item["record_id"] for item in by_record[left]["evidence"]} == {left, right}
        # Partner goes stale: the survivor is cleared and the clearing is logged.
        con.execute("DELETE FROM units WHERE evidence_id='ev_b'")
        assert mark_stale(con, store.folder_id) == [right]
        mark_conflicts(con, store.folder_id)
        cleared = list_transitions(con, store.folder_id, kinds="conflict_cleared")
        assert len(cleared) == 1
        assert cleared[0]["record_id"] == left
        assert cleared[0]["prev_state"]["status"] == "conflicted"
        assert cleared[0]["new_state"]["status"] == "decided"


# --- capture: supersession, staleness, restoration ---

def room_store(path):
    store = Store(path)
    store.add_file("a.txt", [("ev_old", "The workshop is in Room A")])
    store.add_file("b.txt", [("ev_new", "Correction: the workshop moved to Room B")])
    store.add_records("run-1", [
        candidate("Workshop Room A", "workshop room", [("ev_old", "workshop is in Room A")],
                  record_type="decision", status="decided", date_value=None, date_raw=None),
    ])
    return store


def test_supersession_logged_with_claimant_and_link(tmp_path):
    from yaad.memory.records import apply_supersession_links

    store = room_store(tmp_path / "c5.sqlite3")
    store.add_records("run-2", [candidate(
        "Workshop Room B", "workshop room moved", [("ev_new", "workshop moved to Room B")],
        detail="Workshop Room B", record_type="decision", status="decided",
        date_value=None, date_raw=None,
        evidence=[{"evidence_id": "ev_new", "role": "supports",
                   "excerpt": "workshop moved to Room B"},
                  {"evidence_id": "ev_old", "role": "supersedes",
                   "excerpt": "workshop is in Room A"}])])
    with store.db.transaction() as con:
        rows = {row["title"]: row["id"] for row in con.execute("SELECT id,title FROM memory_records")}
        assert apply_supersession_links(con, store.folder_id) == [rows["Workshop Room A"]]
        entries = list_transitions(con, store.folder_id, kinds="superseded")
        assert len(entries) == 1
        (entry,) = entries
        assert entry["record_id"] == rows["Workshop Room A"]
        assert entry["related_record_id"] == rows["Workshop Room B"]
        assert entry["prev_state"]["status"] == "decided"
        assert entry["new_state"]["status"] == "superseded"
        assert f"record {rows['Workshop Room B']}" in entry["reason"]
        assert "ev_old" in entry["reason"]
        roles = {(item["record_id"], item["role"]) for item in entry["evidence"]}
        assert (rows["Workshop Room A"], "supports") in roles
        assert (rows["Workshop Room B"], "supports") in roles
        assert (rows["Workshop Room B"], "supersedes") in roles


def test_stale_marked_and_restored_logged(tmp_path):
    from yaad.memory.records import mark_stale

    store = Store(tmp_path / "c6.sqlite3")
    store.add_file("a.txt", [("ev_a", "The draft is due March 3rd")])
    cand = candidate("Draft due March 3rd", "draft due",
                     [("ev_a", "draft is due March 3rd")])
    store.add_records("run-1", [cand])
    with store.db.transaction() as con:
        (record_id,) = con.execute("SELECT id FROM memory_records").fetchone()
        con.execute("DELETE FROM units WHERE evidence_id='ev_a'")
        assert mark_stale(con, store.folder_id) == [record_id]
        (entry,) = list_transitions(con, store.folder_id, kinds="stale_marked")
        assert entry["record_id"] == record_id
        assert entry["new_state"]["status"] == "stale"
        assert "resolving supports" in entry["reason"]
        # The unresolving supports are snapshotted before they are gone.
        assert [item["evidence_id"] for item in entry["evidence"]] == ["ev_a"]
    with store.db.transaction() as con:
        file_id = con.execute("SELECT id FROM files").fetchone()["id"]
        con.execute("INSERT INTO units(file_id,evidence_id,kind,locator,text) VALUES(?,?,?,?,?)",
                    (file_id, "ev_a", "text_span", '{"type":"text_span"}',
                     "The draft is due March 3rd"))
    store.add_records("run-2", [cand])
    entries = transitions_of(store, kinds="restored")
    assert len(entries) == 1
    assert entries[0]["prev_state"]["status"] == "stale"
    assert entries[0]["new_state"]["status"] == "open"


# --- append-only and idempotent ---

def test_sweep_rerun_appends_nothing(tmp_path):
    from yaad.memory.records import (
        apply_supersession_links,
        mark_conflicts,
        mark_stale,
    )

    store = fee_pair_store(tmp_path / "c7.sqlite3")
    with store.db.transaction() as con:
        mark_conflicts(con, store.folder_id)
        apply_supersession_links(con, store.folder_id)
        mark_stale(con, store.folder_id)
        before = len(list_transitions(con, store.folder_id))
        mark_conflicts(con, store.folder_id)
        apply_supersession_links(con, store.folder_id)
        mark_stale(con, store.folder_id)
        assert len(list_transitions(con, store.folder_id)) == before
        assert before == 4  # 2 created + 2 marked


def test_lifecycle_history_intact_and_ordered(tmp_path):
    from yaad.memory.records import mark_conflicts, mark_stale

    store = fee_pair_store(tmp_path / "c8.sqlite3")
    with store.db.transaction() as con:
        left, right = [row["id"] for row in con.execute("SELECT id FROM memory_records ORDER BY id")]
        mark_conflicts(con, store.folder_id)
        con.execute("DELETE FROM units WHERE evidence_id='ev_b'")
        mark_stale(con, store.folder_id)
        mark_conflicts(con, store.folder_id)
        con.execute("DELETE FROM units WHERE evidence_id='ev_a'")
        mark_stale(con, store.folder_id)
        history = record_history(con, left)
    assert [entry["transition"] for entry in history] == [
        "record_created", "conflict_marked", "conflict_cleared", "stale_marked"]
    assert [entry["id"] for entry in history] == sorted(entry["id"] for entry in history)
    assert history[0]["prev_state"] is None
    assert history[1]["prev_state"]["status"] == "decided"
    assert history[2]["prev_state"]["status"] == "conflicted"
    assert history[2]["new_state"]["status"] == "decided"
    assert history[3]["new_state"]["status"] == "stale"
    # Nothing was overwritten: the creation snapshot still holds the original.
    assert history[0]["new_state"]["amount_value"] == "INR150"


def test_ledger_table_has_no_modifying_statements_in_source():
    import re
    from pathlib import Path

    roots = [Path(__file__).parents[1] / "yaad"]
    assert roots[0].is_dir()
    pattern = re.compile(r"\bUPDATE\s+memory_transitions\b|\bDELETE\s+FROM\s+memory_transitions\b",
                         re.IGNORECASE)
    offenders = []
    for path in sorted(roots[0].rglob("*.py")):
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            code = line.split("#", 1)[0]
            if pattern.search(code):
                offenders.append(f"{path}:{lineno}")
    assert offenders == []


# --- queries ---

def test_list_transitions_filters(tmp_path):
    from yaad.memory.records import mark_conflicts

    store = fee_pair_store(tmp_path / "c9.sqlite3")
    with store.db.transaction() as con:
        left, right = [row["id"] for row in con.execute("SELECT id FROM memory_records ORDER BY id")]
        mark_conflicts(con, store.folder_id)
        assert len(list_transitions(con, store.folder_id)) == 4
        assert len(list_transitions(con, store.folder_id, record_id=left)) == 2
        assert len(list_transitions(con, store.folder_id, kinds=["conflict_marked"])) == 2
        assert list_transitions(con, store.folder_id, kinds="superseded") == []
        assert len(list_transitions(con, store.folder_id, limit=1)) == 1
        assert len(list_transitions(con, store.folder_id, since="2999-01-01")) == 0
        assert len(list_transitions(con, store.folder_id, until="2999-01-01")) == 4
        assert len(list_transitions(con, 424242)) == 0
        newest = list_transitions(con, store.folder_id, limit=2)
        oldest = list_transitions(con, store.folder_id, limit=2, newest_first=False)
        assert [e["id"] for e in newest] == sorted([e["id"] for e in newest], reverse=True)
        assert [e["id"] for e in oldest] == sorted(e["id"] for e in oldest)
        with pytest.raises(ValueError):
            list_transitions(con, store.folder_id, kinds="not_a_kind")


def test_previous_decision_returns_superseded_predecessor(tmp_path):
    from yaad.memory.records import apply_supersession_links

    store = room_store(tmp_path / "c10.sqlite3")
    store.add_records("run-2", [candidate(
        "Workshop Room B", "workshop room moved", [("ev_new", "workshop moved to Room B")],
        detail="Workshop Room B", record_type="decision", status="decided",
        date_value=None, date_raw=None,
        evidence=[{"evidence_id": "ev_new", "role": "supports",
                   "excerpt": "workshop moved to Room B"},
                  {"evidence_id": "ev_old", "role": "supersedes",
                   "excerpt": "workshop is in Room A"}])])
    with store.db.connect() as con:
        rows = {row["title"]: row["id"] for row in con.execute("SELECT id,title FROM memory_records")}
    with store.db.transaction() as con:
        apply_supersession_links(con, store.folder_id)
        previous = previous_decision(con, store.folder_id, rows["Workshop Room B"])
        assert previous["record_id"] == rows["Workshop Room A"]
        assert previous["title"] == "Workshop Room A"
        assert TIMESTAMP.match(previous["superseded_at"])
        assert previous["evidence"]
        assert previous_decision(con, store.folder_id, rows["Workshop Room A"]) is None


# --- API ---

def test_api_lists_transitions_with_filters(tmp_path, monkeypatch):
    from fastapi import HTTPException

    from yaad.api import main
    from yaad.memory.records import mark_conflicts

    store = fee_pair_store(tmp_path / "c11.sqlite3")
    with store.db.transaction() as con:
        mark_conflicts(con, store.folder_id)
    monkeypatch.setattr(main, "db", store.db)
    payload = main.memory_transitions(folder_id=store.folder_id)
    assert len(payload["transitions"]) == 4
    entry = payload["transitions"][0]
    assert {"id", "folder_id", "record_id", "transition", "prev_state", "new_state",
            "reason", "evidence", "source", "created_at"} <= set(entry)
    assert len(main.memory_transitions(folder_id=store.folder_id,
                                       kind="conflict_marked")["transitions"]) == 2
    assert main.memory_transitions(folder_id=store.folder_id, record_id=1)["transitions"]
    assert main.memory_transitions(limit=1)["transitions"].__len__() == 1
    with pytest.raises(HTTPException) as missing:
        main.memory_transitions(folder_id=424242)
    assert missing.value.status_code == 404
    with pytest.raises(HTTPException) as bad_kind:
        main.memory_transitions(kind="not_a_kind")
    assert bad_kind.value.status_code == 400


# --- ask integration: narrow deterministic "what changed" path ---

def test_ask_what_changed_lists_transitions(tmp_path):
    store = room_store(tmp_path / "c12.sqlite3")
    result = store.ask("what changed?")
    assert result["status"] == "answered"
    assert result["memory"]["used"] is True
    assert "Workshop Room A" in result["answer"]
    assert "Recorded" in result["answer"]
    assert [card["id"] for card in result["evidence"]] == ["ev_old"]
    assert len(result["memory"]["transitions"]) == 1
    assert result["memory"]["transitions"][0]["transition"] == "record_created"


def test_ask_changes_intent_is_narrow(tmp_path):
    from yaad.query.memory_answer import detect_changes_intent

    for question in ("what changed?", "What has changed in my notes?",
                     "show recent changes", "list changes to the budget",
                     "change history"):
        assert detect_changes_intent(question) is True, question
    for question in ("was the proposal accepted?", "what is the registration fee?",
                     "when did the venue change?", "tell me about the draft",
                     "what color is the bicycle?"):
        assert detect_changes_intent(question) is False, question
    store = room_store(tmp_path / "c13.sqlite3")
    result = store.ask("when did the venue change?")
    assert "transitions" not in result["memory"]
    assert result["memory"]["used"] is False


def test_changes_respect_consent_and_removed_sources(tmp_path):
    store = room_store(tmp_path / "c14.sqlite3")
    with store.db.transaction() as con:
        con.execute("UPDATE folders SET cloud_consent=0 WHERE id=?", (store.folder_id,))
    revoked = store.ask("what changed?")
    assert revoked["answer"] == "No recorded changes in the selected scope."
    assert revoked["memory"]["used"] is False

    live = room_store(tmp_path / "c15.sqlite3")
    with live.db.transaction() as con:
        con.execute("DELETE FROM units WHERE evidence_id='ev_old'")
    removed = live.ask("what changed?")
    assert removed["status"] == "answered"
    assert "source removed" in removed["answer"]
    assert removed["evidence"] == []

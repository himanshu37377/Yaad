"""Memory answer integration: eligible records join the units pipeline.

Offline is offline (cloud disabled) so synthesis is deterministic; one test
uses a recording model client to prove injected rows reach semantic review.
"""
import json

from yaad.config import Settings
from yaad.db import Database
from yaad.memory.records import apply_supersession_links, mark_conflicts, mark_stale, upsert_records
from yaad.query.answer import answer_question

LOCATOR = '{"type":"text_span","section":"notes"}'


class Store:
    def __init__(self, path):
        self.db = Database(path)
        self.settings = Settings(db_path=path, cloud_enabled=False)
        self.unit_texts: dict[str, str] = {}
        with self.db.transaction() as con:
            con.execute("INSERT INTO folders(path,cloud_consent) VALUES('/src',1)")
            self.folder_id = con.execute("SELECT id FROM folders").fetchone()["id"]

    def add_file(self, name, units, consent=1):
        with self.db.transaction() as con:
            con.execute("INSERT INTO files(folder_id,path,sha256,kind,size,mtime,status,cloud_consent)"
                        " VALUES(?,?,?,?,?,?,?,?)",
                        (self.folder_id, f"/src/{name}", "0" * 64, "text", 10, 0.0, "indexed", consent))
            file_id = con.execute("SELECT id FROM files WHERE path=?", (f"/src/{name}",)).fetchone()["id"]
            for entry in units:
                eid, text = entry[0], entry[1]
                locator = entry[2] if len(entry) > 2 else LOCATOR
                con.execute("INSERT INTO units(file_id,evidence_id,kind,locator,text) VALUES(?,?,?,?,?)",
                            (file_id, eid, "text_span", locator, text))
                self.unit_texts[eid] = text
        return file_id

    def add_records(self, run_id, candidates):
        with self.db.transaction() as con:
            return upsert_records(con, self.folder_id, run_id, candidates, dict(self.unit_texts))

    def ask(self, question, channels=None):
        extra = {} if channels is None else {"channels": channels}
        return answer_question(self.db, question, self.folder_id, self.settings, **extra)


def candidate(title, key_phrase, excerpts, **over):
    base = {"record_type": "deadline", "title": title, "detail": title,
            "date_value": "2026-03-03", "date_raw": "March 3rd", "assignee": None,
            "status": "open", "statement_kind": "explicit", "key_phrase": key_phrase,
            "evidence": [{"evidence_id": eid, "role": "supports", "excerpt": text}
                         for eid, text in excerpts]}
    base.update(over)
    return base


def test_eligible_record_answers_when_units_path_cannot(tmp_path):
    store = Store(tmp_path / "a.sqlite3")
    store.add_file("a.txt", [("ev_a", "The draft is due March 3rd")])
    store.add_file("b.txt", [("ev_b", "Draft due March 3rd, all teams")])
    store.add_records("run-1", [candidate("Draft due March 3rd", "draft due",
                                          [("ev_a", "draft is due March 3rd"),
                                           ("ev_b", "Draft due March 3rd")])])
    result = store.ask("when is the draft due?")
    assert result["status"] == "answered"
    assert result["memory"]["used"] is True
    assert result["values"] and result["values"][0]["value"] == "March 3rd"
    assert {card["file"] for card in result["evidence"]} == {"/src/a.txt", "/src/b.txt"}


def test_multi_source_record_injects_unretrieved_corroboration(tmp_path):
    store = Store(tmp_path / "m.sqlite3")
    store.add_file("a.txt", [("ev_a", "The draft is due March 3rd")])
    store.add_file("b.txt", [("ev_b", "Milestone alpha scheduled")])
    store.add_file("c.txt", [("ev_c", "Prototype target set")])
    store.add_records("run-1", [candidate(
        "Draft due March 3rd", "draft due milestone",
        [("ev_a", "draft is due March 3rd"), ("ev_b", "Milestone alpha scheduled"),
         ("ev_c", "Prototype target set")])])
    result = store.ask("when is the draft due?")
    assert result["status"] == "answered"
    assert {card["file"] for card in result["evidence"]} == {"/src/a.txt", "/src/b.txt", "/src/c.txt"}


def test_stale_and_superseded_records_are_excluded(tmp_path):
    store = Store(tmp_path / "s.sqlite3")
    store.add_file("a.txt", [("ev_old", "Old hall booked March 1st"), ("ev_new", "New hall booked March 2nd")])
    store.add_records("run-1", [
        candidate("Old hall March 1st", "hall booked", [("ev_old", "Old hall booked March 1st")],
                  detail="Old hall booked March 1st",
                  record_type="decision", status="decided", date_value="2026-03-01", date_raw="March 1st"),
        candidate("New hall March 2nd", "hall booked new", [("ev_new", "New hall booked March 2nd")],
                  detail="New hall booked March 2nd",
                  record_type="decision", status="decided", date_value="2026-03-02", date_raw="March 2nd"),
    ])
    with store.db.transaction() as con:
        con.execute("DELETE FROM units WHERE evidence_id='ev_old'")
        assert mark_stale(con, store.folder_id) != []
    result = store.ask("which hall is booked?")
    assert result["memory"]["used"] is True
    assert len(result["memory"]["record_ids"]) == 1
    assert "New hall" in result["answer"]


def test_superseded_record_yields_to_successor(tmp_path):
    store = Store(tmp_path / "s2.sqlite3")
    store.add_file("a.txt", [("ev_old", "Venue is the old hall"), ("ev_new", "Venue is the new auditorium")])
    store.add_records("run-1", [
        candidate("Venue old hall", "venue hall", [("ev_old", "Venue is the old hall")],
                  record_type="decision", status="decided", date_value=None, date_raw=None),
        candidate("Venue auditorium", "venue auditorium",
                  [("ev_new", "Venue is the new auditorium"),
                   ("ev_old", "Venue is the old hall")],
                  detail="Venue auditorium chosen for the fest",
                  record_type="decision", status="decided", date_value=None, date_raw=None,
                  evidence=[{"evidence_id": "ev_new", "role": "supports",
                             "excerpt": "Venue is the new auditorium"},
                            {"evidence_id": "ev_old", "role": "supersedes",
                             "excerpt": "Venue is the old hall"}]),
    ])
    with store.db.transaction() as con:
        assert apply_supersession_links(con, store.folder_id) != []
    result = store.ask("which venue was chosen?")
    assert result["memory"]["used"] is True
    assert result["evidence"] and all(card["id"] == "ev_new" for card in result["evidence"])


def test_conflicted_pair_is_represented_explicitly(tmp_path):
    store = Store(tmp_path / "c.sqlite3")
    store.add_file("a.txt", [("ev_a", "The draft is due March 3rd")])
    store.add_file("b.txt", [("ev_b", "The draft is due April 4th")])
    store.add_records("run-1", [
        candidate("Draft due March 3rd", "draft due",
                  [("ev_a", "draft is due March 3rd")]),
        candidate("Draft due April 4th", "draft due",
                  [("ev_b", "draft is due April 4th")],
                  date_value="2026-04-04", date_raw="April 4th"),
    ])
    with store.db.transaction() as con:
        assert mark_conflicts(con, store.folder_id) != []
    dated = store.ask("when is the draft due?")
    assert dated["status"] == "conflict"
    assert dated["memory"]["used"] is True
    assert {card["id"] for card in dated["evidence"]} == {"ev_a", "ev_b"}
    undated = store.ask("tell me about the draft due dates")
    assert undated["status"] == "conflict"
    assert undated["memory"]["used"] is True
    assert {card["id"] for card in undated["evidence"]} == {"ev_a", "ev_b"}


def test_proposal_is_labeled_and_unresolved_is_gated(tmp_path):
    store = Store(tmp_path / "p.sqlite3")
    store.add_file("a.txt", [("ev_a", "Maya might review the draft"),
                             ("ev_b", "Workshop topics still need to be decided")])
    store.add_records("run-1", [
        candidate("Maya may review", "maya review", [("ev_a", "Maya might review the draft")],
                  detail="Maya may review the draft",
                  record_type="commitment", status="open", statement_kind="proposal",
                  date_value=None, date_raw=None),
        candidate("Workshop topics undecided", "workshop topics",
                  [("ev_b", "Workshop topics still need to be decided")],
                  record_type="open_question", status="open", statement_kind="unresolved",
                  date_value=None, date_raw=None),
    ])
    proposal = store.ask("who will review the draft?")
    assert proposal["status"] == "answered"
    assert proposal["answer"].startswith("Proposed:")
    undecided = store.ask("what is still undecided?")
    assert undecided["status"] == "answered"
    assert "Workshop topics" in undecided["answer"]
    factual = store.ask("who will review?")
    with store.db.connect() as con:
        open_q = con.execute("SELECT id FROM memory_records WHERE record_type='open_question'").fetchone()["id"]
    assert open_q not in factual["memory"]["record_ids"]


def test_inferred_records_never_become_premises(tmp_path):
    store = Store(tmp_path / "i.sqlite3")
    store.add_file("a.txt", [("ev_a", "The draft is due March 3rd")])
    store.add_records("run-1", [candidate(
        "Draft due March 3rd", "draft due", [("ev_a", "draft is due March 3rd")],
        record_type="decision", status="decided", statement_kind="inferred")])
    result = store.ask("when is the draft due?")
    assert result["memory"]["used"] is False
    assert result["status"] == "answered"


def test_memory_value_joins_established_conflict_rules(tmp_path):
    store = Store(tmp_path / "v.sqlite3")
    store.add_file("a.txt", [("ev_a", "The draft is due March 3rd")])
    store.add_file("b.txt", [("ev_b", "Leadership moved the draft to April 4th")])
    store.add_records("run-1", [candidate("Draft due March 3rd", "draft due",
                                          [("ev_a", "draft is due March 3rd")])])
    result = store.ask("when is the draft due?")
    assert result["status"] == "conflict"
    assert result["memory"]["used"] is True
    assert {card["file"] for card in result["evidence"]} == {"/src/a.txt", "/src/b.txt"}


def test_invalid_evidence_after_delete_or_rewrite_falls_back(tmp_path):
    for name, mutate in (("del", "delete"), ("rewrite", "rewrite")):
        store = Store(tmp_path / f"{name}.sqlite3")
        store.add_file("a.txt", [("ev_a", "The draft is due March 3rd")])
        store.add_file("b.txt", [("ev_b", "Draft due March 3rd, all teams")])
        store.add_records("run-1", [candidate(
            "Draft due March 3rd", "draft due",
            [("ev_a", "draft is due March 3rd"), ("ev_b", "Draft due March 3rd")])])
        with store.db.transaction() as con:
            if mutate == "delete":
                con.execute("DELETE FROM units WHERE evidence_id IN ('ev_a','ev_b')")
            else:
                con.execute("UPDATE units SET text='Rewritten without the claim' WHERE evidence_id IN ('ev_a','ev_b')")
        result = store.ask("when is the draft due?")
        assert result["memory"]["used"] is False, name


def test_consent_revocation_removes_records_from_premises(tmp_path):
    store = Store(tmp_path / "r.sqlite3")
    store.add_file("a.txt", [("ev_a", "The draft is due March 3rd")])
    store.add_file("b.txt", [("ev_b", "Draft due March 3rd, all teams")])
    store.add_records("run-1", [candidate(
        "Draft due March 3rd", "draft due",
        [("ev_a", "draft is due March 3rd"), ("ev_b", "Draft due March 3rd")])])
    assert store.ask("when is the draft due?")["memory"]["used"] is True
    with store.db.transaction() as con:
        con.execute("UPDATE files SET cloud_consent=0")
    revoked = store.ask("when is the draft due?")
    assert revoked["memory"]["used"] is False
    assert revoked["status"] == "cloud_unavailable"


def test_paraphrase_dupes_yield_one_claim(tmp_path):
    store = Store(tmp_path / "d.sqlite3")
    store.add_file("a.txt", [("ev_a", "Venue confirmation due November 10. Venue confirmation deadline November 10.")])
    store.add_records("run-1", [
        candidate("Venue confirmation due", "venue confirmation due",
                  [("ev_a", "Venue confirmation due November 10")],
                  date_value="2026-11-10", date_raw="November 10"),
        candidate("Venue confirmation deadline", "venue confirmation deadline",
                  [("ev_a", "Venue confirmation deadline November 10")],
                  date_value="2026-11-10", date_raw="November 10"),
    ])
    result = store.ask("when is venue confirmation due?")
    assert result["status"] == "answered"
    assert len(result["memory"]["record_ids"]) == 2
    assert len(result["values"]) == 1
    assert len(result["evidence"]) == 1


def test_repeated_extraction_union_yields_one_claim(tmp_path):
    from yaad.memory.extract import run_extraction

    store = Store(tmp_path / "u.sqlite3")
    store.settings = Settings(db_path=tmp_path / "u.sqlite3", cloud_enabled=True,
                              model_api_key="stub-key-for-tests")
    store.add_file("a.txt", [("ev_a", "Please deliver the draft by March 3rd")])
    store.add_file("b.txt", [("ev_b", "Please deliver the draft by March 3rd, all teams")])

    class StubClient:
        last_call = {"provider": "Meta Model API", "model": "muse-spark-1.3", "status_code": 200,
                     "success": True, "duration_ms": 5, "input_tokens": 900,
                     "output_tokens": 300, "cached_tokens": 0}

        def __init__(self, records):
            self.records = records

        def reason_json(self, messages, schema, prompt_id):
            return {"records": self.records}

    def extracted(title, phrase, evidence):
        item = candidate(title, phrase, evidence)
        item["date_value"] = item["date_value"] or ""
        item["date_raw"] = item["date_raw"] or ""
        item["assignee"] = ""
        return item

    run_extraction(store.db, store.folder_id, "run-1", store.settings, StubClient(
        [extracted("Draft due", "deliver draft", [("ev_a", "deliver the draft by March 3rd")])]))
    run_extraction(store.db, store.folder_id, "run-2", store.settings, StubClient(
        [extracted("Draft due revised", "deliver draft", [("ev_b", "deliver the draft by March 3rd")])]))
    store.settings = Settings(db_path=tmp_path / "u.sqlite3", cloud_enabled=False)
    result = store.ask("when is the draft due?")
    assert result["status"] == "answered"
    assert len(result["values"]) == 1
    assert {card["id"] for card in result["evidence"]} == {"ev_a", "ev_b"}


def test_evidence_overlap_alone_does_not_match(tmp_path):
    store = Store(tmp_path / "o.sqlite3")
    store.add_file("a.txt", [("ev_a", "The draft is due March 3rd")])
    store.add_records("run-1", [candidate(
        "Zebra budget alpha", "draft due", [("ev_a", "draft is due March 3rd")],
        detail="Zebra budget alpha")])
    result = store.ask("when is the draft due?")
    assert result["memory"]["used"] is False
    assert result["status"] == "answered"


def test_no_match_preserves_units_only_behavior(tmp_path):
    left = Store(tmp_path / "left.sqlite3")
    left.add_file("a.txt", [("ev_a", "The draft is due March 3rd")])
    left.add_records("run-1", [candidate("Draft due March 3rd", "draft due",
                                         [("ev_a", "draft is due March 3rd")])])
    right = Store(tmp_path / "right.sqlite3")
    right.add_file("a.txt", [("ev_a", "The draft is due March 3rd")])
    with_memory = left.ask("when is the draft due?")
    without_memory = right.ask("when is the draft due?")
    assert with_memory["memory"]["used"] is True
    unrelated = left.ask("what color is the bicycle?")
    assert unrelated["memory"]["used"] is False
    twin = right.ask("what color is the bicycle?")
    for key in ("status", "answer", "values", "evidence", "candidates"):
        assert unrelated[key] == twin[key]


def test_injected_rows_reach_semantic_review(tmp_path, monkeypatch):
    from yaad.llm import meta_client

    store = Store(tmp_path / "sem.sqlite3")
    store.settings = Settings(db_path=tmp_path / "sem.sqlite3", cloud_enabled=True,
                              model_api_key="stub-key-for-tests")
    store.add_file("a.txt", [("ev_a", "The draft is due March 3rd")])
    store.add_file("b.txt", [("ev_b", "Confirmed milestone for all teams")])
    store.add_records("run-1", [candidate(
        "Draft due March 3rd", "draft due milestone",
        [("ev_a", "draft is due March 3rd"), ("ev_b", "Confirmed milestone for all teams")])])
    seen: list = []

    class RecordingClient:
        last_call = {"provider": "Meta Model API", "model": "muse-spark-1.3", "status_code": 200,
                     "success": True, "duration_ms": 4, "input_tokens": 40, "output_tokens": 8}

        def __init__(self, _settings):
            pass

        def reason_json(self, messages, schema, prompt_id):
            seen.append(json.loads(messages[0]["content"]))
            return {"status": "answered", "answer": "The draft is due March 3rd.",
                    "claims": [{"claim": "The draft is due March 3rd.", "evidence_ids": ["ev_a"]}]}

    monkeypatch.setattr(meta_client, "MetaClient", RecordingClient)
    result = store.ask("when is the draft due?")
    assert result["status"] == "answered"
    assert {source["evidence_id"] for source in seen[0]["sources"]} == {"ev_a", "ev_b"}
    assert result["memory"]["used"] is True

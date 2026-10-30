"""Stage 1: amount-aware identity and same-matter amount conflicts.

Rival amounts (Rs 150 vs Rs 200) stay separate records and conflict; alias
spellings of one amount merge; different budget categories never conflict;
multi-amount records never auto-conflict; old 5-part keys migrate safely.
"""
from test_memory_answers import Store, candidate

from yaad.db import Database
from yaad.memory.records import (
    amount_class_of,
    backfill_amount_keys,
    conflict_partners,
    mark_conflicts,
    normalize_amount,
    upsert_records,
)


def test_normalize_amount_collapses_aliases_and_spellings():
    assert normalize_amount("Rs 150") == "INR150"
    assert normalize_amount("₹150") == "INR150"
    assert normalize_amount("150 rupees") == "INR150"
    assert normalize_amount("INR 150.00") == "INR150"
    assert normalize_amount("Rs. 1,500.50") == "INR1500.5"
    assert normalize_amount("$5") == "USD5"
    assert normalize_amount("$5") != normalize_amount("Rs 5")
    assert normalize_amount("") is None
    assert normalize_amount("no money here") is None


def test_amount_class_needs_exactly_one_distinct_amount():
    assert amount_class_of(["fee Rs 150"]) == ("INR150", "Rs 150")
    assert amount_class_of(["fee Rs 150", "cost ₹150"]) == ("INR150", "Rs 150")
    assert amount_class_of(["fee Rs 150", "fee Rs 200"]) == ("multi", None)
    assert amount_class_of(["no money"]) == ("noamount", None)
    assert amount_class_of([]) == ("noamount", None)


def test_rival_amounts_stay_separate_and_conflict(tmp_path):
    database = Database(tmp_path / "amt.sqlite3")
    with database.transaction() as con:
        con.execute("INSERT INTO folders(path,cloud_consent) VALUES('/src',1)")
        folder_id = con.execute("SELECT id FROM folders").fetchone()["id"]
        con.execute("INSERT INTO files(folder_id,path,sha256,kind,size,mtime,status,cloud_consent)"
                    " VALUES(?,?,?,?,?,?,?,?)",
                    (folder_id, "/src/n.txt", "0" * 64, "text", 10, 0.0, "indexed", 1))
        file_id = con.execute("SELECT id FROM files").fetchone()["id"]
        texts = {"ev_a": "registration fee is Rs 150", "ev_b": "registration fee is Rs 200"}
        for eid, text in texts.items():
            con.execute("INSERT INTO units(file_id,evidence_id,kind,locator,text) VALUES(?,?,?,?,?)",
                        (file_id, eid, "text_span", '{"type":"text_span"}', text))
        first = {"record_type": "decision", "title": "Fee Rs 150", "detail": "Fee Rs 150",
                 "date_value": None, "date_raw": None, "assignee": None, "status": "decided",
                 "statement_kind": "explicit", "key_phrase": "registration fee",
                 "evidence": [{"evidence_id": "ev_a", "role": "supports",
                               "excerpt": "registration fee is Rs 150"}]}
        second = {"record_type": "decision", "title": "Fee Rs 200", "detail": "Fee Rs 200",
                  "date_value": None, "date_raw": None, "assignee": None, "status": "decided",
                  "statement_kind": "explicit", "key_phrase": "registration fee",
                  "evidence": [{"evidence_id": "ev_b", "role": "supports",
                               "excerpt": "registration fee is Rs 200"}]}
        upsert_records(con, folder_id, "run-1", [first, second], dict(texts))
        rows = con.execute("SELECT id,title,record_key,amount_value,status FROM memory_records ORDER BY id").fetchall()
        assert [(r["title"], r["amount_value"]) for r in rows] == [
            ("Fee Rs 150", "INR150"), ("Fee Rs 200", "INR200")]
        assert rows[0]["record_key"] != rows[1]["record_key"]
        assert mark_conflicts(con, folder_id) == [(rows[0]["id"], rows[1]["id"])]
        assert {r["status"] for r in con.execute("SELECT status FROM memory_records")} == {"conflicted"}
        partners = conflict_partners(con, folder_id, rows[0]["id"])
        assert [p["id"] for p in partners] == [rows[1]["id"]]


def test_alias_spellings_of_one_amount_merge(tmp_path):
    database = Database(tmp_path / "alias.sqlite3")
    with database.transaction() as con:
        con.execute("INSERT INTO folders(path,cloud_consent) VALUES('/src',1)")
        folder_id = con.execute("SELECT id FROM folders").fetchone()["id"]
        con.execute("INSERT INTO files(folder_id,path,sha256,kind,size,mtime,status,cloud_consent)"
                    " VALUES(?,?,?,?,?,?,?,?)",
                    (folder_id, "/src/n.txt", "0" * 64, "text", 10, 0.0, "indexed", 1))
        file_id = con.execute("SELECT id FROM files").fetchone()["id"]
        texts = {"ev_a": "fee Rs 150 due", "ev_b": "fee ₹150 due"}
        for eid, text in texts.items():
            con.execute("INSERT INTO units(file_id,evidence_id,kind,locator,text) VALUES(?,?,?,?,?)",
                        (file_id, eid, "text_span", '{"type":"text_span"}', text))

        def fee(title, eid):
            return {"record_type": "decision", "title": title, "detail": title, "date_value": None,
                    "date_raw": None, "assignee": None, "status": "decided",
                    "statement_kind": "explicit", "key_phrase": "fee due",
                    "evidence": [{"evidence_id": eid, "role": "supports", "excerpt": texts[eid]}]}

        upsert_records(con, folder_id, "run-1", [fee("Fee Rs 150", "ev_a"), fee("Fee rupee 150", "ev_b")],
                       dict(texts))
        assert con.execute("SELECT COUNT(*) n FROM memory_records").fetchone()["n"] == 1
        assert mark_conflicts(con, folder_id) == []
        assert con.execute("SELECT COUNT(*) n FROM memory_record_evidence").fetchone()["n"] == 2


def test_different_budget_categories_never_conflict(tmp_path):
    database = Database(tmp_path / "cats.sqlite3")
    with database.transaction() as con:
        con.execute("INSERT INTO folders(path,cloud_consent) VALUES('/src',1)")
        folder_id = con.execute("SELECT id FROM folders").fetchone()["id"]
        con.execute("INSERT INTO files(folder_id,path,sha256,kind,size,mtime,status,cloud_consent)"
                    " VALUES(?,?,?,?,?,?,?,?)",
                    (folder_id, "/src/n.txt", "0" * 64, "text", 10, 0.0, "indexed", 1))
        file_id = con.execute("SELECT id FROM files").fetchone()["id"]
        texts = {"ev_a": "Venue setup Rs 8000", "ev_b": "Prizes Rs 5000"}
        for eid, text in texts.items():
            con.execute("INSERT INTO units(file_id,evidence_id,kind,locator,text) VALUES(?,?,?,?,?)",
                        (file_id, eid, "text_span", '{"type":"text_span"}', text))

        def line(title, phrase, eid):
            return {"record_type": "decision", "title": title, "detail": title, "date_value": None,
                    "date_raw": None, "assignee": None, "status": "decided",
                    "statement_kind": "explicit", "key_phrase": phrase,
                    "evidence": [{"evidence_id": eid, "role": "supports", "excerpt": texts[eid]}]}

        upsert_records(con, folder_id, "run-1",
                       [line("Venue setup 8000", "venue setup", "ev_a"),
                        line("Prizes 5000", "prizes", "ev_b")], dict(texts))
        assert mark_conflicts(con, folder_id) == []
        assert {r["status"] for r in con.execute("SELECT status FROM memory_records")} == {"decided"}


def test_multi_amount_record_never_auto_conflicts(tmp_path):
    database = Database(tmp_path / "multi.sqlite3")
    with database.transaction() as con:
        con.execute("INSERT INTO folders(path,cloud_consent) VALUES('/src',1)")
        folder_id = con.execute("SELECT id FROM folders").fetchone()["id"]
        con.execute("INSERT INTO files(folder_id,path,sha256,kind,size,mtime,status,cloud_consent)"
                    " VALUES(?,?,?,?,?,?,?,?)",
                    (folder_id, "/src/n.txt", "0" * 64, "text", 10, 0.0, "indexed", 1))
        file_id = con.execute("SELECT id FROM files").fetchone()["id"]
        texts = {"ev_a": "Budget amount Rs 2500, total Rs 25000",
                 "ev_b": "Budget amount Rs 150 confirmed"}
        for eid, text in texts.items():
            con.execute("INSERT INTO units(file_id,evidence_id,kind,locator,text) VALUES(?,?,?,?,?)",
                        (file_id, eid, "text_span", '{"type":"text_span"}', text))
        multi = {"record_type": "decision", "title": "Contingency", "detail": "Contingency",
                 "date_value": None, "date_raw": None, "assignee": None, "status": "decided",
                 "statement_kind": "explicit", "key_phrase": "budget amount",
                 "evidence": [{"evidence_id": "ev_a", "role": "supports", "excerpt": texts["ev_a"]}]}
        single = {"record_type": "decision", "title": "Fee", "detail": "Fee",
                  "date_value": None, "date_raw": None, "assignee": None, "status": "decided",
                  "statement_kind": "explicit", "key_phrase": "budget amount",
                  "evidence": [{"evidence_id": "ev_b", "role": "supports", "excerpt": texts["ev_b"]}]}
        upsert_records(con, folder_id, "run-1", [multi, single], dict(texts))
        assert mark_conflicts(con, folder_id) == []
        assert {r["status"] for r in con.execute("SELECT status FROM memory_records")} == {"decided"}


def test_fee_rivals_cannot_settle_end_to_end(tmp_path):
    store = Store(tmp_path / "fee.sqlite3")
    store.add_file("a.txt", [("ev_a", "The registration fee is Rs 150 per person")])
    store.add_file("b.txt", [("ev_b", "Updated notice: the registration fee is Rs 200 per person")])
    store.add_records("run-1", [
        candidate("Registration fee Rs 150", "registration fee",
                  [("ev_a", "registration fee is Rs 150")],
                  record_type="decision", status="decided", date_value=None, date_raw=None),
        candidate("Registration fee Rs 200", "registration fee",
                  [("ev_b", "registration fee is Rs 200")],
                  record_type="decision", status="decided", date_value=None, date_raw=None),
    ])
    with store.db.transaction() as con:
        from yaad.memory.records import mark_conflicts as sweep
        assert sweep(con, store.folder_id) != []
    result = store.ask("what is the registration fee?")
    assert result["status"] == "conflict"
    assert len(result["memory"]["record_ids"]) == 2
    assert {card["id"] for card in result["evidence"]} == {"ev_a", "ev_b"}
    assert {value["value"] for value in result["values"]} >= {"Rs 150", "Rs 200"}
    # Re-running extraction in either order never overwrites a rival's title.
    store.add_records("run-2", [
        candidate("Registration fee Rs 200", "registration fee",
                  [("ev_b", "registration fee is Rs 200")],
                  record_type="decision", status="decided", date_value=None, date_raw=None),
    ])
    with store.db.connect() as con:
        titles = {r["title"] for r in con.execute("SELECT title FROM memory_records")}
    assert titles == {"Registration fee Rs 150", "Registration fee Rs 200"}


def test_amount_key_backfill_migrates_old_databases(tmp_path):
    path = tmp_path / "old.sqlite3"
    database = Database(path)
    with database.transaction() as con:
        con.execute("INSERT INTO folders(path,cloud_consent) VALUES('/src',1)")
        folder_id = con.execute("SELECT id FROM folders").fetchone()["id"]
        con.execute("INSERT INTO files(folder_id,path,sha256,kind,size,mtime,status,cloud_consent)"
                    " VALUES(?,?,?,?,?,?,?,?)",
                    (folder_id, "/src/n.txt", "0" * 64, "text", 10, 0.0, "indexed", 1))
        file_id = con.execute("SELECT id FROM files").fetchone()["id"]
        for eid, text in (("ev_a", "fee Rs 150 due"), ("ev_b", "plain note here")):
            con.execute("INSERT INTO units(file_id,evidence_id,kind,locator,text) VALUES(?,?,?,?,?)",
                        (file_id, eid, "text_span", '{"type":"text_span"}', text))
        con.execute("""INSERT INTO memory_records(folder_id,record_type,title,detail,assignee,status,
                       statement_kind,record_key,extraction_run)
                       VALUES(?,'decision','Fee','Fee',NULL,'decided','explicit',
                       'decision|asserted|nodate|noassignee|due+fee','old-run')""", (folder_id,))
        first = con.execute("SELECT id FROM memory_records").fetchone()["id"]
        con.execute("INSERT INTO memory_record_evidence(record_id,evidence_id,role,excerpt)"
                    " VALUES(?, 'ev_a', 'supports', 'fee Rs 150 due')", (first,))
        con.execute("""INSERT INTO memory_records(folder_id,record_type,title,detail,assignee,status,
                       statement_kind,record_key,extraction_run)
                       VALUES(?,'decision','Note','Note',NULL,'decided','explicit',
                       'decision|asserted|nodate|noassignee|note+plain','old-run')""", (folder_id,))
        second = con.execute("SELECT id FROM memory_records WHERE title='Note'").fetchone()["id"]
        con.execute("INSERT INTO memory_record_evidence(record_id,evidence_id,role,excerpt)"
                    " VALUES(?, 'ev_b', 'supports', 'plain note here')", (second,))
    reopened = Database(path)
    with reopened.connect() as con:
        rows = {r["title"]: dict(r) for r in con.execute("SELECT title,record_key,amount_value FROM memory_records")}
    assert rows["Fee"]["record_key"] == "decision|asserted|nodate|noassignee|due+fee|INR150"
    assert rows["Fee"]["amount_value"] == "INR150"
    assert rows["Note"]["record_key"] == "decision|asserted|nodate|noassignee|note+plain|noamount"
    assert rows["Note"]["amount_value"] is None
    # Idempotent: reopening changes nothing.
    again = Database(path)
    with again.connect() as con:
        assert con.execute("SELECT COUNT(*) n FROM memory_records").fetchone()["n"] == 2
        assert backfill_amount_keys(con) == 0

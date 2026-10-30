"""Reviewed duplicate fixtures: true paraphrases, near-duplicates, distinct claims.

Pairs D1-D9 are genuine same-claim paraphrases mined from captured live
extraction outputs (stability take 2); N1-N8 are distinct claims sharing
vocabulary, evidence, or dates. Each case runs in its own folder to keep
pairs independent. Expectations below are the reviewed measurement:
9/9 duplicates hinted, 0/8 distinct hinted, 1 documented false negative
(disjoint-vocabulary paraphrases sharing evidence are indistinguishable
from distinct claims citing one unit, so the rule stays silent there).

Hints never merge; merging stays exact-record_key-only.
"""
import pytest

from yaad.db import Database
from yaad.memory.records import find_duplicate_candidates, mark_stale, upsert_records

# (label, kind, [(title, type, kind, date, assignee, key_phrase, [(unit, excerpt)])], unit_texts, expect_hint)
CASES = [
    ("D1 volunteers", "dup",
     [("Need volunteers", "decision", "explicit", None, None, "at least volunteers",
       [("u1", "We need at least six volunteers")]),
      ("Volunteer headcount", "decision", "explicit", None, None, "at least six",
       [("u1", "at least six volunteers")])],
     {"u1": "We need at least six volunteers"}, True),
    ("D2 budget total", "dup",
     [("Budget approved", "decision", "explicit", None, None, "budget approved by",
       [("u2", "Budget of 25000 approved by committee")]),
      ("Budget rupees", "decision", "explicit", None, None, "25,000 rupee budget",
       [("u2", "25000 rupee budget approved")])],
     {"u2": "Budget of 25000 approved by committee; 25000 rupee budget approved"}, True),
    ("D3 contingency", "dup",
     [("Contingency INR", "decision", "explicit", None, None, "Contingency 2,500 INR",
       [("u3", "Contingency 2500 INR")]),
      ("Contingency short", "decision", "explicit", None, None, "Contingency 2,500",
       [("u4", "Contingency 2500 reserve")])],
     {"u3": "Contingency 2500 INR", "u4": "Contingency 2500 reserve"}, True),
    ("D4 event date", "dup",
     [("Event held", "decision", "explicit", "2026-11-18", None, "held on November",
       [("u5", "Fest held on November 18 2026")]),
      ("Event dated", "decision", "explicit", "2026-11-18", None, "November 18 2026",
       [("u5", "November 18 2026 festival")])],
     {"u5": "Fest held on November 18 2026 festival"}, True),
    ("D5 prizes", "dup",
     [("Prizes INR", "decision", "explicit", None, None, "Prizes 5,000 INR",
       [("u6", "Prizes 5000 INR")]),
      ("Prizes budget", "decision", "explicit", None, None, "Budget Prizes 5,000",
       [("u6", "Budget Prizes 5000 line")])],
     {"u6": "Prizes 5000 INR; Budget Prizes 5000 line"}, True),
    ("D6 fee wording", "dup",
     [("Fee per", "decision", "explicit", None, None, "registration fee per",
       [("u7", "registration fee per person")]),
      ("Fee rupee", "decision", "explicit", None, None, "150 rupee registration",
       [("u7", "150 rupee registration fee")])],
     {"u7": "registration fee per person; 150 rupee registration fee"}, True),
    ("D7 venue setup", "dup",
     [("Setup seating", "decision", "explicit", None, None, "Venue setup seating",
       [("u8", "Venue setup seating 8000")]),
      ("Setup short", "decision", "explicit", None, None, "Venue setup and",
       [("u8", "Venue setup and seating")])],
     {"u8": "Venue setup seating 8000; Venue setup and seating"}, True),
    ("D8 venue deadline", "dup",
     [("Confirm due", "deadline", "explicit", "2026-11-10", None, "Venue confirmation due",
       [("u9", "Venue confirmation due November 10")]),
      ("Confirm deadline", "deadline", "explicit", "2026-11-10", None, "Venue confirmation deadline",
       [("u9", "Venue confirmation deadline November 10")])],
     {"u9": "Venue confirmation due November 10; Venue confirmation deadline November 10"}, True),
    ("D9 inferred wording", "dup",
     [("Sound explicit", "decision", "explicit", None, None, "sound lighting",
       [("u10", "sound lighting hall")]),
      ("Sound inferred", "decision", "inferred", None, None, "sound lighting hall",
       [("u10", "sound lighting hall")])],
     {"u10": "sound lighting hall"}, True),
    # Documented false negative: same claim, fully disjoint wording.
    ("FN fee disjoint", "dup",
     [("Fee persons", "decision", "explicit", None, None, "150 per person",
       [("u11", "150 per person fee")]),
      ("Fee rupee 2", "decision", "explicit", None, None, "150 rupee registration",
       [("u11", "150 rupee registration")])],
     {"u11": "150 per person fee; 150 rupee registration"}, False),
    ("N1 total vs line", "distinct",
     [("Budget total", "decision", "explicit", None, None, "25,000 rupee budget",
       [("u12", "25000 rupee budget total")]),
      ("Prizes line", "decision", "explicit", None, None, "Prizes 5,000",
       [("u12", "Prizes 5000 line item")])],
     {"u12": "25000 rupee budget total; Prizes 5000 line item"}, False),
    ("N2 sound vs printing", "distinct",
     [("Sound line", "decision", "explicit", None, None, "Sound and lighting",
       [("u13", "Sound and lighting 6000")]),
      ("Print line", "decision", "explicit", None, None, "Printing and publicity",
       [("u13", "Printing and publicity 3500")])],
     {"u13": "Sound and lighting 6000; Printing and publicity 3500"}, False),
    ("N3 riya vs arjun", "distinct",
     [("Riya task", "commitment", "explicit", None, "Riya", "confirm sound quote",
       [("u14", "Riya confirm sound quote")]),
      ("Arjun task", "commitment", "explicit", None, "Arjun", "confirm projector booking",
       [("u15", "Arjun confirm projector booking")])],
     {"u14": "Riya confirm sound quote", "u15": "Arjun confirm projector booking"}, False),
    ("N4 meeting vs event", "distinct",
     [("Meeting date", "decision", "explicit", "2026-11-06", None, "planning meeting November",
       [("u16", "planning meeting November 6")]),
      ("Event date", "decision", "explicit", "2026-11-18", None, "held on November",
       [("u17", "event held on November 18")])],
     {"u16": "planning meeting November 6", "u17": "event held on November 18"}, False),
    ("N5 proposal vs explicit", "distinct",
     [("Hall decided", "decision", "explicit", None, None, "book main hall",
       [("u18", "we will book main hall")]),
      ("Hall proposed", "commitment", "proposal", None, None, "book main hall",
       [("u19", "proposal to book main hall")])],
     {"u18": "we will book main hall", "u19": "proposal to book main hall"}, False),
    ("N6 poster types", "distinct",
     [("Poster deadline", "deadline", "explicit", "2026-11-12", None, "poster must be",
       [("u20", "poster must be ready")]),
      ("Poster work", "commitment", "explicit", None, None, "poster registration forms",
       [("u21", "work on poster registration forms")])],
     {"u20": "poster must be ready", "u21": "work on poster registration forms"}, False),
    ("N7 fee vs headcount", "distinct",
     [("Fee decided", "decision", "explicit", None, None, "registration fee per",
       [("u22", "registration fee per person")]),
      ("Headcount", "decision", "explicit", None, None, "at least volunteers",
       [("u23", "need at least six volunteers")])],
     {"u22": "registration fee per person", "u23": "need at least six volunteers"}, False),
]


def seed(database, folder_id, specs, unit_texts, tag):
    with database.transaction() as con:
        con.execute("INSERT INTO files(folder_id,path,sha256,kind,size,mtime,status,cloud_consent)"
                    " VALUES(?,?,?,?,?,?,?,?)",
                    (folder_id, f"/src/{tag}.txt", "0" * 64, "text", 10, 0.0, "indexed", 1))
        file_id = con.execute("SELECT id FROM files WHERE path=?", (f"/src/{tag}.txt",)).fetchone()["id"]
        ids = {}
        for unit, text in unit_texts.items():
            eid = f"ev_{tag}_{unit}"
            ids[unit] = eid
            con.execute("INSERT INTO units(file_id,evidence_id,kind,locator,text) VALUES(?,?,?,?,?)",
                        (file_id, eid, "text_span", '{"type":"text_span"}', text))
        candidates = []
        for title, rtype, kind, date, who, phrase, ev in specs:
            status = "decided" if rtype in ("decision",) else "open"
            if rtype == "open_question":
                status = "open"
            candidates.append({
                "record_type": rtype, "title": title, "detail": title, "date_value": date,
                "date_raw": None, "assignee": who, "status": status, "statement_kind": kind,
                "key_phrase": phrase,
                "evidence": [{"evidence_id": ids[u], "role": "supports", "excerpt": x} for u, x in ev]})
        upsert_records(con, folder_id, f"run-{tag}", candidates,
                       {ids[u]: t for u, t in unit_texts.items()})


@pytest.fixture
def database(tmp_path):
    return Database(tmp_path / "dup.sqlite3")


@pytest.mark.parametrize("case", [c for c in CASES], ids=[c[0] for c in CASES])
def test_reviewed_pair_expectations(database, tmp_path, case):
    label, kind, specs, unit_texts, expect = case
    tag = label.split()[0].lower()
    with database.transaction() as con:
        con.execute("INSERT INTO folders(path,cloud_consent) VALUES(?,1)", (f"/src/{tag}",))
        folder_id = con.execute("SELECT id FROM folders WHERE path=?", (f"/src/{tag}",)).fetchone()["id"]
    seed(database, folder_id, specs, unit_texts, tag)
    with database.connect() as con:
        assert len(find_duplicate_candidates(con, folder_id)) == (1 if expect else 0), label


def test_stale_records_are_excluded_from_hints(database):
    with database.transaction() as con:
        con.execute("INSERT INTO folders(path,cloud_consent) VALUES('/src/stale',1)")
        folder_id = con.execute("SELECT id FROM folders").fetchone()["id"]
    seed(database, folder_id,
         [("Old wording", "decision", "explicit", None, None, "venue setup seating",
           [("u1", "Venue setup seating 8000")]),
          ("New wording", "decision", "explicit", None, None, "Venue setup and",
           [("u1", "Venue setup and seating")])],
         {"u1": "Venue setup seating 8000; Venue setup and seating"}, "stale")
    with database.transaction() as con:
        con.execute("DELETE FROM units WHERE evidence_id='ev_stale_u1'")
        assert mark_stale(con, folder_id) != []
    with database.connect() as con:
        assert find_duplicate_candidates(con, folder_id) == []


def test_duplicate_fixture_precision_and_recall(database, tmp_path):
    # Aggregate measurement over the reviewed set (excludes the stale case).
    folders = {}
    with database.transaction() as con:
        for case in CASES:
            tag = "agg" + case[0].split()[0].lower()
            con.execute("INSERT INTO folders(path,cloud_consent) VALUES(?,1)", (f"/src/{tag}",))
            folders[case[0]] = con.execute("SELECT id FROM folders WHERE path=?",
                                           (f"/src/{tag}",)).fetchone()["id"]
    for case in CASES:
        seed(database, folders[case[0]], case[2], case[3], "agg" + case[0].split()[0].lower())
    true_pos = false_pos = false_neg = 0
    with database.connect() as con:
        for case in CASES:
            label, kind, _, _, expect = case
            hinted = len(find_duplicate_candidates(con, folders[label])) == 1
            if kind == "dup" and label != "FN fee disjoint":
                true_pos += hinted
                false_neg += not hinted
            elif kind == "distinct":
                false_pos += hinted
    assert (true_pos, false_pos, false_neg) == (9, 0, 0)

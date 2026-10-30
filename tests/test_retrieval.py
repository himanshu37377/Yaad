"""Retrieval returns what the question asks, without topic-expansion pollution.

Temporary databases only; no model calls.
"""
from yaad.db import Database
from yaad.query.retrieve import search


def store(tmp_path):
    database = Database(tmp_path / "ret.sqlite3")
    with database.transaction() as con:
        con.execute("INSERT INTO folders(path,cloud_consent) VALUES('/src',1)")
        folder_id = con.execute("SELECT id FROM folders").fetchone()["id"]
        for name, eid, text in (
            ("shop.txt", "ev-shop", "the corner shop is open late every evening"),
            ("settings.txt", "ev-settings", "offline mode settings and toggles"),
        ):
            con.execute("INSERT INTO files(folder_id,path,sha256,kind,size,mtime,status,cloud_consent)"
                        " VALUES(?,?,?,?,?,?,?,?)",
                        (folder_id, f"/src/{name}", "0" * 64, "text", 10, 0.0, "indexed", 1))
            file_id = con.execute("SELECT id FROM files WHERE path=?",
                                  (f"/src/{name}",)).fetchone()["id"]
            con.execute("INSERT INTO units(file_id,evidence_id,kind,locator,text) VALUES(?,?,?,?,?)",
                        (file_id, eid, "text_span", '{"type":"text_span"}', text))
    return database


def test_unrelated_expansion_terms_do_not_retrieve(tmp_path):
    hits = [row["evidence_id"] for row in search(store(tmp_path), "is the shop open", None, 10)]
    assert hits == ["ev-shop"]


def test_direct_terms_still_match(tmp_path):
    hits = [row["evidence_id"] for row in search(store(tmp_path), "offline mode settings", None, 10)]
    assert hits == ["ev-settings"]

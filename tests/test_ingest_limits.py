"""Ingest guardrails: oversized text is truncated loudly, huge folders capped.

Local indexing only (cloud off); temporary databases and files.
"""
from yaad.config import Settings
from yaad.db import Database
from yaad.extract.text import MAX_TEXT_BYTES, extract_text
from yaad.scan import worker as worker_mod
from yaad.scan.discover import discover
from yaad.scan.worker import Indexer


def test_large_text_is_truncated_loudly_at_unit_level(tmp_path):
    big = tmp_path / "big.txt"
    big.write_bytes(b"lorem ipsum dolor sit amet. " * 40_000)
    units, truncated = extract_text(big)
    assert truncated is True
    assert sum(len(text) for text, _ in units) <= MAX_TEXT_BYTES * 1.2
    small = tmp_path / "small.txt"
    small.write_text("short note", encoding="utf-8")
    assert extract_text(small)[1] is False


def test_large_docx_is_truncated_loudly_at_unit_level(tmp_path):
    from docx import Document

    path = tmp_path / "big.docx"
    doc = Document()
    for n in range(4000):
        doc.add_paragraph(f"Paragraph {n} with enough words to matter. " * 10)
    doc.save(path)
    units, truncated = extract_text(path)
    assert truncated is True
    assert sum(len(text) for text, _ in units) <= MAX_TEXT_BYTES + 3500


def test_oversized_file_indexes_partial_with_clear_error(tmp_path):
    folder = tmp_path / "src"
    folder.mkdir()
    (folder / "big.txt").write_bytes(b"searchable padding line here. " * 40_000)
    database = Database(tmp_path / "lim.sqlite3")
    settings = Settings(db_path=tmp_path / "lim.sqlite3", cloud_enabled=False)
    indexer = Indexer(database, settings)
    folder_id = indexer.add_folder(str(folder), False)
    counts = indexer.index_folder(folder_id)
    assert counts["partial"] == 1
    with database.connect() as con:
        file = con.execute("SELECT status,error FROM files").fetchone()
        assert file["status"] == "partial"
        assert "1,000,000 bytes" in file["error"]
        assert con.execute("SELECT COUNT(*) n FROM units").fetchone()["n"] < 400


def test_folder_file_count_is_capped_and_reported(tmp_path, monkeypatch):
    monkeypatch.setattr(worker_mod, "MAX_INDEX_FILES", 3)
    folder = tmp_path / "src"
    folder.mkdir()
    for name in ("a.txt", "b.txt", "c.txt", "d.txt", "e.txt"):
        (folder / name).write_text(f"contents of {name}", encoding="utf-8")
    database = Database(tmp_path / "cap.sqlite3")
    settings = Settings(db_path=tmp_path / "cap.sqlite3", cloud_enabled=False)
    indexer = Indexer(database, settings)
    folder_id = indexer.add_folder(str(folder), False)
    counts = indexer.index_folder(folder_id)
    assert counts["capped"] == 2
    assert counts["indexed"] == 3
    with database.connect() as con:
        names = sorted(row["path"].rsplit("/", 1)[-1]
                       for row in con.execute("SELECT path FROM files"))
        assert names == ["a.txt", "b.txt", "c.txt"]


def test_discover_keeps_sorted_order_under_cap(tmp_path):
    folder = tmp_path / "src"
    folder.mkdir()
    for name in ("b.txt", "a.txt"):
        (folder / name).write_text("x", encoding="utf-8")
    entries, overflow = discover(folder, 100)
    assert [path.name for path, _ in entries] == ["a.txt", "b.txt"]
    assert overflow == 0


def test_discover_indexes_folder_under_hidden_parent(tmp_path):
    hidden = tmp_path / ".hidden-parent"
    folder = hidden / "notes"
    folder.mkdir(parents=True)
    (folder / "visible.txt").write_text("hello", encoding="utf-8")
    (folder / ".secret.txt").write_text("hidden", encoding="utf-8")
    entries, overflow = discover(folder, 100)
    assert [path.name for path, _ in entries] == ["visible.txt"]
    assert overflow == 0

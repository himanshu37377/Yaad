"""Photo-answer path: listings, value routing, sensitivity, disclosure.

The photo branch answers gallery/browse questions from image evidence
alone, but substantive questions that mention photos must still travel
the shared pipeline (memory, validation, conflicts, abstention) with
the gallery attached -- never silently discarded into a listing.

Deterministic fixtures on disposable databases; no model calls, no
production database contact.
"""
import pytest

from test_memory_answers import Store, candidate
from yaad.memory.records import apply_supersession_links, mark_conflicts
from yaad.query.photos import photo_intent, photo_value_intent


def add_image(store, name, caption, consent=1):
    """Index-shape image rows without a model call."""
    with store.db.transaction() as con:
        con.execute("INSERT INTO files(folder_id,path,sha256,kind,size,mtime,status,cloud_consent)"
                    " VALUES(?,?,?,?,?,?,?,?)",
                    (store.folder_id, f"/src/{name}", "ab" * 32, "image", 10, 0.0, "indexed", consent))
        file_id = con.execute("SELECT id FROM files WHERE path=?", (f"/src/{name}",)).fetchone()["id"]
        evidence_id = f"ev_{name.replace('.', '_')}"
        text = f"Description: {caption}"
        con.execute("INSERT INTO units(file_id,evidence_id,kind,locator,text) VALUES(?,?,?,?,?)",
                    (file_id, evidence_id, "image_understanding", '{"type":"image"}', text))
        con.execute("INSERT INTO photo_descriptions(file_id,schema_version,caption) VALUES(?,?,?)",
                    (file_id, 3, caption))
        store.unit_texts[evidence_id] = text
        return file_id, evidence_id


def venue_conflict_store(path):
    store = Store(path)
    add_image(store, "stage.jpg", "Spring Fest venue stage")
    store.add_file("a.txt", [("ev_a", "Council booked Riverside Hall for Spring Fest")])
    store.add_file("b.txt", [("ev_b", "Council booked Grand Arena for Spring Fest on March 3rd")])
    store.add_records("run-1", [
        candidate("Council Spring Fest venue Riverside Hall", "spring fest",
                  [("ev_a", "Council booked Riverside Hall for Spring Fest")],
                  record_type="decision", status="decided", date_value=None, date_raw=None,
                  detail="Council booked Riverside Hall for Spring Fest"),
        candidate("Council Spring Fest venue Grand Arena", "spring fest",
                  [("ev_b", "Council booked Grand Arena for Spring Fest on March 3rd")],
                  record_type="decision", status="decided",
                  date_value="2026-03-03", date_raw="March 3rd",
                  detail="Council booked Grand Arena for Spring Fest"),
    ])
    with store.db.transaction() as con:
        mark_conflicts(con, store.folder_id)
    return store


def test_p1_photo_listing_and_attribution(tmp_path):
    store = Store(tmp_path / "p1.sqlite3")
    add_image(store, "bike.jpg", "A red bicycle leaning on a fence")
    result = store.ask("show photos of the bicycle")
    assert result["status"] == "answered"
    assert len(result["photos"]) == 1
    assert result["memory"]["used"] is False
    (card,) = result["evidence"]
    assert card["file"] == "/src/bike.jpg"
    assert "red bicycle" in card["quote"]
    assert card["preview_url"].endswith("/preview")
    assert card["record_ids"] == []


def test_p2_photography_does_not_hijack(tmp_path):
    assert photo_intent("who won the photography prize?") is False
    store = Store(tmp_path / "p2.sqlite3")
    add_image(store, "bike.jpg", "A red bicycle leaning on a fence")
    store.add_file("news.txt", [("ev_n", "Arjun won the photography prize on March 3rd")])
    result = store.ask("who won the photography prize?")
    assert result.get("photos", []) == []
    assert any(card["file"] == "/src/news.txt" for card in result["evidence"])


def test_p3_photosynthesis_does_not_hijack(tmp_path):
    assert photo_intent("tell me about photosynthesis") is False
    assert photo_intent("picturesque places?") is False
    store = Store(tmp_path / "p3.sqlite3")
    store.add_file("bio.txt", [("ev_b", "Photosynthesis converts light in chloroplasts")])
    result = store.ask("tell me about photosynthesis")
    assert result.get("photos", []) == []
    assert any(card["file"] == "/src/bio.txt" for card in result["evidence"])


def test_p4_document_answers_accessible(tmp_path):
    assert photo_value_intent("What is the Spring Fest venue? Show me the photos.") is True
    assert photo_value_intent("show photos of the bicycle") is False
    assert photo_value_intent("please show me all photos") is False
    assert photo_value_intent("where was this photo taken?") is False
    store = Store(tmp_path / "p4.sqlite3")
    store.add_file("a.txt", [("ev_a", "The draft is due March 3rd")])
    result = store.ask("when is the draft due?")
    assert result["status"] == "answered"
    assert result.get("photos", []) == []


def test_p5_value_question_uses_shared_path(tmp_path):
    store = venue_conflict_store(tmp_path / "p5.sqlite3")
    result = store.ask("What is the Spring Fest venue? Show me the photos.")
    assert result["status"] == "conflict"
    assert result["memory"]["used"] is True
    assert result["memory"]["conflicts"] != []
    assert len(result["photos"]) == 1


def test_p6_conflicts_visible_with_photos(tmp_path):
    store = venue_conflict_store(tmp_path / "p6.sqlite3")
    result = store.ask("What is the Spring Fest venue? Show me the photos.")
    assert len(result["memory"]["conflict_validations"]) == 1
    assert "Riverside Hall" in result["answer"] and "Grand Arena" in result["answer"]
    assert {photo["file"] for photo in result["photos"]} == {"/src/stage.jpg"}


def test_p7_photos_alongside_answer(tmp_path):
    store = Store(tmp_path / "p7.sqlite3")
    add_image(store, "stage.jpg", "Spring Fest venue stage")
    store.add_file("a.txt", [("ev_a", "Council booked Riverside Hall for Spring Fest")])
    store.add_records("run-1", [candidate(
        "Council Spring Fest venue Riverside Hall", "spring fest",
        [("ev_a", "Council booked Riverside Hall for Spring Fest")],
        record_type="decision", status="decided", date_value=None, date_raw=None,
        detail="Council booked Riverside Hall for Spring Fest")])
    result = store.ask("What is the Spring Fest venue? Show me the photos.")
    assert result["status"] == "answered"
    assert "Riverside Hall" in result["answer"]
    assert result["memory"]["used"] is True
    assert {photo["file"] for photo in result["photos"]} == {"/src/stage.jpg"}


def test_p8_pure_browsing_stays_listing_only(tmp_path):
    store = Store(tmp_path / "p8.sqlite3")
    add_image(store, "bike.jpg", "A red bicycle leaning on a fence")
    store.add_file("t.txt", [("ev_t", "Council approved the library grant of Rs 50000")])
    store.add_records("run-1", [candidate(
        "Council library grant Rs 50000", "library grant",
        [("ev_t", "Council approved the library grant of Rs 50000")],
        record_type="decision", status="decided", date_value=None, date_raw=None,
        detail="Council approved the library grant of Rs 50000")])
    result = store.ask("show photos of the bicycle")
    assert result["status"] == "answered"
    assert "photo(s)" in result["answer"]
    assert result["memory"] == {"used": False, "record_ids": [], "claims": [],
                                "context_claims": [], "conflicts": [], "conflict_validations": []}


def test_p9_contradictory_captions_stay_separate(tmp_path):
    store = Store(tmp_path / "p9.sqlite3")
    add_image(store, "one.jpg", "A red bicycle leaning on a fence")
    add_image(store, "two.jpg", "A blue bicycle parked by a wall")
    result = store.ask("show photos of the bicycle")
    assert result["status"] == "answered"
    assert len(result["photos"]) == 2
    quotes = " / ".join(card["quote"] for card in result["evidence"])
    assert "red bicycle" in quotes and "blue bicycle" in quotes
    assert "red" not in result["answer"] and "blue" not in result["answer"]


def test_p10_duplicate_photo_units_deduplicated(tmp_path):
    store = Store(tmp_path / "p10.sqlite3")
    file_id, _ = add_image(store, "one.jpg", "A red bicycle leaning on a fence")
    with store.db.transaction() as con:
        con.execute("INSERT INTO units(file_id,evidence_id,kind,locator,text) VALUES(?,?,?,?,?)",
                    (file_id, "ev_dup", "image_understanding", '{"type":"image"}',
                     "Description: A red bicycle leaning on a fence"))
    result = store.ask("show photos of the bicycle")
    assert [card["file"] for card in result["evidence"]] == ["/src/one.jpg"]


def test_p11_irrelevant_memory_ignores_listing(tmp_path):
    def build(path, with_memory):
        store = Store(path)
        add_image(store, "bike.jpg", "A red bicycle leaning on a fence")
        store.add_file("t.txt", [("ev_t", "Council approved the library grant of Rs 50000")])
        if with_memory:
            store.add_records("run-1", [candidate(
                "Council library grant Rs 50000", "library grant",
                [("ev_t", "Council approved the library grant of Rs 50000")],
                record_type="decision", status="decided", date_value=None, date_raw=None,
                detail="Council approved the library grant of Rs 50000")])
        return store
    result = build(tmp_path / "p11.sqlite3", True).ask("show photos of the bicycle")
    reference = build(tmp_path / "p11t.sqlite3", False).ask("show photos of the bicycle")
    assert result == reference


def test_p12_superseded_excluded_from_assisted_answer(tmp_path):
    store = Store(tmp_path / "p12.sqlite3")
    add_image(store, "stage.jpg", "Spring Fest venue stage")
    store.add_file("a.txt", [("ev_o", "Council booked Riverside Hall for Spring Fest"),
                             ("ev_n", "Council booked Grand Arena for Spring Fest on March 3rd")])
    store.add_records("run-1", [
        candidate("Council Spring Fest Riverside Hall", "spring fest",
                  [("ev_o", "Council booked Riverside Hall for Spring Fest")],
                  record_type="decision", status="decided", date_value=None, date_raw=None,
                  detail="Council booked Riverside Hall for Spring Fest"),
        candidate("Council Spring Fest Grand Arena", "spring fest",
                  [("ev_n", "Council booked Grand Arena for Spring Fest on March 3rd")],
                  record_type="decision", status="decided",
                  date_value="2026-03-03", date_raw="March 3rd",
                  detail="Council booked Grand Arena for Spring Fest"),
    ])
    with store.db.transaction() as con:
        con.execute("INSERT INTO memory_record_evidence(record_id,evidence_id,role,excerpt)"
                    " VALUES(2,'ev_o','supersedes','Council booked Riverside Hall for Spring Fest')")
        assert apply_supersession_links(con, store.folder_id) == [1]
    result = store.ask("What is the Spring Fest venue? Show me the photos.")
    assert result["status"] == "answered"
    assert "Grand Arena" in result["answer"]
    assert "Riverside Hall" not in result["answer"]
    assert len(result["photos"]) == 1


def test_p13_no_consent_serving_adds_no_egress(tmp_path):
    store = Store(tmp_path / "p13.sqlite3")
    file_id, _ = add_image(store, "bike.jpg", "A red bicycle leaning on a fence")
    with store.db.transaction() as con:
        con.execute("UPDATE folders SET cloud_consent=0")
        before = con.execute("SELECT COUNT(*) FROM egress_log").fetchone()[0]
    assert store.ask("show photos of the bicycle")["status"] == "answered"
    assert store.ask("What is shown? Show me the photos.")["status"] != "error"
    with store.db.transaction() as con:
        after = con.execute("SELECT COUNT(*) FROM egress_log").fetchone()[0]
    assert before == after == 0
    from yaad.scan.worker import Indexer
    with pytest.raises(PermissionError):
        Indexer(store.db, store.settings).redescribe_image(file_id)


def test_p14_sensitive_caption_flagged(tmp_path):
    store = Store(tmp_path / "p14.sqlite3")
    add_image(store, "doc.jpg", "Passport photo page, holder identity visible")
    result = store.ask("show photos of the passport")
    assert result["status"] == "answered"
    assert "answer_sensitive" in result
    assert [card["sensitive"] for card in result["evidence"]] == [True]


def test_p15_caption_egress_disclosed_once(tmp_path):
    store = Store(tmp_path / "p15.sqlite3")
    file_id, _ = add_image(store, "bike.jpg", "A red bicycle leaning on a fence")
    with store.db.transaction() as con:
        con.execute("INSERT INTO egress_log(file_id,purpose,provider,model,bytes,content_kind,status_code,success)"
                    " VALUES(?,'image understanding','Meta Model API','m',10,'image',200,1)", (file_id,))
    result = store.ask("show photos of the bicycle")
    assert len(result["disclosure"]) == 1
    (entry,) = result["disclosure"]
    assert entry["why"] == "image understanding" and entry["to"] == "Meta Model API"
    assert "bike.jpg" in entry["what"]

    clean = Store(tmp_path / "p15b.sqlite3")
    add_image(clean, "plain.jpg", "A park bench at noon")
    assert clean.ask("show photos of the bench")["disclosure"] == []

"""Regressions for the post-V3 reliability cycle: image fields schema,
person-slot gating (U1), history serving (M2), winner naming (H5),
and chain-link prompt coverage. Deterministic; no live calls."""
from test_memory_answers import Store, candidate
from yaad.scan.worker import VISION_SCHEMA, _searchable_image_text


def test_vision_schema_requests_structured_fields():
    assert "fields" in VISION_SCHEMA["properties"]
    assert "fields" in VISION_SCHEMA["required"]
    text = _searchable_image_text({
        "caption": "Poster", "people": [], "objects": [], "actions": [],
        "scene": {}, "visible_text": "Venue: Grand Arena",
        "visual_attributes": [], "uncertainties": [],
        "fields": [{"name": "venue", "value": "Grand Arena"}]})
    assert "Fields: venue: Grand Arena" in text


def test_chief_guest_question_admits_no_wrong_slot_claims(tmp_path):
    store = Store(tmp_path / "u1.sqlite3")
    store.add_file("a.txt", [("ev_a", "Harbor Fest venue is Marina Hall")])
    store.add_file("b.txt", [("ev_b", "Harbor Fest budget is Rs 40000")])
    store.add_records("run-1", [
        candidate("Harbor Fest venue Marina Hall", "harbor fest venue",
                  [("ev_a", "Harbor Fest venue is Marina Hall")],
                  record_type="decision", status="decided", date_value=None, date_raw=None,
                  detail="Harbor Fest venue is Marina Hall"),
        candidate("Harbor Fest budget Rs 40000", "harbor fest budget",
                  [("ev_b", "Harbor Fest budget is Rs 40000")],
                  record_type="decision", status="decided", date_value=None, date_raw=None,
                  detail="Harbor Fest budget is Rs 40000")])
    result = store.ask("Who is the Harbor Fest chief guest?")
    assert result["memory"]["claims"] == []
    assert result["status"] != "answered" or "chief guest" in result["answer"].casefold()


def test_original_amount_served_from_superseded_record(tmp_path):
    store = Store(tmp_path / "m2.sqlite3")
    store.add_file("a.txt", [("ev_a", "Hall hire cost for Spring Fest is 12000 INR, quoted by City Halls.")])
    store.add_file("b.txt", [("ev_b", "Hall hire cost for Spring Fest is 18000 INR, confirmed with City Halls.")])
    store.add_records("run-1", [
        candidate("Hall hire 12000", "hall hire cost",
                  [("ev_a", "Hall hire cost for Spring Fest is 12000 INR")],
                  record_type="decision", status="decided", date_value=None, date_raw=None,
                  detail="Hall hire cost for Spring Fest is 12000 INR, quoted by City Halls."),
        candidate("Hall hire 18000", "hall hire cost",
                  [("ev_b", "Hall hire cost for Spring Fest is 18000 INR")],
                  record_type="decision", status="decided", date_value=None, date_raw=None,
                  detail="Hall hire cost for Spring Fest is 18000 INR, confirmed with City Halls.")])
    with store.db.transaction() as con:
        con.execute("INSERT INTO memory_record_evidence(record_id,evidence_id,role,excerpt)"
                    " VALUES(2,'ev_a','supersedes','Hall hire cost for Spring Fest is 12000 INR')")
        from yaad.memory.records import apply_supersession_links
        assert apply_supersession_links(con, store.folder_id) == [1]
    result = store.ask("What was the original hall hire cost?")
    assert result["status"] == "answered"
    assert "12000" in result["answer"]
    assert result["answer"].startswith("Originally")
    current = store.ask("What is the hall hire cost?")
    assert "18000" in current["answer"]


def test_what_changed_names_winner_titles(tmp_path):
    from test_v2_failure_fixes import _deadline_chain
    store, _ = _deadline_chain(tmp_path / "h5.sqlite3")
    result = store.ask("What changed?")
    assert result["status"] == "answered"
    assert "Deadline April 9" in result["answer"]
    assert "record 3" not in result["answer"]


def test_extraction_prompt_links_final_to_every_earlier_statement():
    from yaad.memory.extract import RULES
    assert "EVERY" in RULES and "earlier statement it replaces" in RULES

"""Regressions for the three real-model evaluation failures (L3, budget
linking, L5). Deterministic fixtures on disposable databases; no model
calls, no production database contact, frozen eval fixtures untouched.
"""
from test_memory_answers import Store, candidate
from test_photo_answers import add_image
from yaad.memory.records import apply_supersession_links, mark_conflicts


def deadline_store(path, supersede):
    store = Store(path)
    store.add_file("a.txt", [("ev_a", "Registration deadline is March 1, 2027 for Spring Fest")])
    store.add_file("b.txt", [("ev_b", "Registration deadline is April 2, 2027 for Spring Fest")])
    store.add_file("c.txt", [("ev_c", "Registration deadline is March 1, 2027")])
    store.add_records("run-1", [
        candidate("Registration deadline March 2027", "registration deadline",
                  [("ev_a", "Registration deadline is March 1, 2027")],
                  date_value="2027-03-01", date_raw="March 1, 2027"),
        candidate("Registration deadline April 2027", "registration deadline",
                  [("ev_b", "Registration deadline is April 2, 2027")],
                  date_value="2027-04-02", date_raw="April 2, 2027"),
    ])
    if supersede:
        with store.db.transaction() as con:
            con.execute("INSERT INTO memory_record_evidence(record_id,evidence_id,role,excerpt)"
                        " VALUES(2,'ev_a','supersedes','Registration deadline is March 1, 2027')")
            assert apply_supersession_links(con, store.folder_id) == [1]
    return store


def test_l3_validated_supersession_rescues_conflict(tmp_path):
    result = deadline_store(tmp_path / "l3.sqlite3", True).ask(
        "What is the registration deadline?")
    assert result["status"] == "answered"
    assert "April" in result["answer"]
    assert {v["value"] for v in result["values"]} == {"April 2, 2027"}
    assert result["memory"]["used"] is True
    assert result["memory"]["conflicts"] == []


def test_l3_current_rival_without_supersession_stays_conflict(tmp_path):
    result = deadline_store(tmp_path / "l3b.sqlite3", False).ask(
        "What is the registration deadline?")
    assert result["status"] == "conflict"
    assert result["status"] != "answered"


def test_genuine_validated_amount_conflict_remains_conflict(tmp_path):
    store = Store(tmp_path / "gc.sqlite3")
    store.add_file("a.txt", [("ev_a", "Council approved the library grant of Rs 50000")])
    store.add_file("b.txt", [("ev_b", "Council approved the library grant of Rs 75000")])
    store.add_records("run-1", [
        candidate("Council library grant Rs 50000", "library grant",
                  [("ev_a", "Council approved the library grant of Rs 50000")],
                  record_type="decision", status="decided", date_value=None, date_raw=None,
                  detail="Council approved the library grant of Rs 50000"),
        candidate("Council library grant Rs 75000", "library grant",
                  [("ev_b", "Council approved the library grant of Rs 75000")],
                  record_type="decision", status="decided", date_value=None, date_raw=None,
                  detail="Council approved the library grant of Rs 75000"),
    ])
    with store.db.transaction() as con:
        assert [tuple(sorted(p)) for p in mark_conflicts(con, store.folder_id)] == [(1, 2)]
    result = store.ask("what is the library grant amount?")
    assert result["status"] == "conflict"
    assert {v["value"] for v in result["values"]} == {"Rs 50000", "Rs 75000"}


def test_unvalidated_amount_rivalry_is_unverified_never_settled(tmp_path):
    store = Store(tmp_path / "amt.sqlite3")
    store.add_file("a.txt", [("ev_a", "Spring Fest budget is Rs 20000"),
                             ("ev_b", "Budget amount payment is Rs 25000 for the fest")])
    store.add_records("run-1", [
        candidate("Spring Fest budget Rs 20000", "spring fest budget",
                  [("ev_a", "Spring Fest budget is Rs 20000")],
                  record_type="decision", status="decided",
                  date_value=None, date_raw=None,
                  detail="Spring Fest budget is Rs 20000"),
        candidate("Fest budget Rs 25000", "budget amount payment",
                  [("ev_b", "Budget amount payment is Rs 25000")],
                  record_type="decision", status="decided",
                  date_value=None, date_raw=None,
                  detail="Fest budget amount is Rs 25000"),
    ])
    result = store.ask("What is the Spring Fest budget?")
    assert result["status"] == "unverified"
    assert "Rs 20000" in result["answer"] and "Rs 25000" in result["answer"]
    assert result["memory"]["conflicts"] == []


def test_l5_exact_poster_question_returns_image_evidence(tmp_path):
    store = Store(tmp_path / "l5.sqlite3")
    add_image(store, "poster.jpg",
              "SPRING FEST 2027 REVISED New venue Grand Arena Revised budget 25000 INR")
    result = store.ask("What does the poster photo show?")
    assert result["status"] == "answered"
    assert "Grand Arena" in result["answer"]
    assert len(result["photos"]) == 1
    assert result["evidence"][0]["file"] == "/src/poster.jpg"
    assert result["memory"]["used"] is False


def test_photo_content_terms_still_rank_matching_image_first(tmp_path):
    store = Store(tmp_path / "rank.sqlite3")
    add_image(store, "bike.jpg", "A red bicycle leaning on a fence")
    add_image(store, "stage.jpg", "Spring Fest venue stage at Grand Arena")
    result = store.ask("show photos of the bicycle")
    assert result["status"] == "answered"
    assert result["photos"][0]["file"] == "/src/bike.jpg"


def test_l5_fallback_excludes_unrelated_memory(tmp_path):
    store = Store(tmp_path / "l5b.sqlite3")
    add_image(store, "poster.jpg", "SPRING FEST 2027 REVISED New venue Grand Arena")
    store.add_file("t.txt", [("ev_t", "Council approved the library grant of Rs 50000")])
    store.add_records("run-1", [candidate(
        "Council library grant Rs 50000", "library grant",
        [("ev_t", "Council approved the library grant of Rs 50000")],
        record_type="decision", status="decided", date_value=None, date_raw=None,
        detail="Council approved the library grant of Rs 50000")])
    result = store.ask("What does the poster photo show?")
    assert result["status"] == "answered"
    assert result["memory"]["used"] is False
    assert result["memory"]["record_ids"] == []
    assert "library grant" not in result["answer"].casefold()
    assert all(card["record_ids"] == [] for card in result["evidence"])

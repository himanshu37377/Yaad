"""Slot synonyms for memory overlap (shared seam) + A5 paraphrase serving.

SLOT_SYNONYMS/canonical_term in yaad.query.memory_answer project memory
overlap onto tiny reviewed equivalence classes, so a paraphrased
question ('cost to attend') meets its record ('registration fee').
Hard negatives ('free' vs 'fee') must never match. Deterministic
fixtures on disposable databases; no model calls, no network.
"""
from test_memory_answers import Store, candidate

from yaad.query.memory_answer import (
    SLOT_SYNONYMS,
    _matches_terms,
    canonical_term,
    content_terms,
)


def test_slot_synonym_classes_are_tiny_and_reviewed():
    assert len(set(SLOT_SYNONYMS.values())) <= 10
    assert {canonical_term(word) for word in ("cost", "price", "charge")} == {"fee"}
    assert {canonical_term(word) for word in ("attend", "admission")} == {"registration"}
    assert {canonical_term(word) for word in ("room", "hall")} == {"venue"}
    assert {canonical_term(word) for word in ("date", "due")} == {"deadline"}


def test_canonical_term_casefolds_and_keeps_identity():
    assert canonical_term("Cost") == "fee"
    assert canonical_term("DUE") == "deadline"
    assert canonical_term("fee") == "fee"
    assert canonical_term("registration") == "registration"
    assert canonical_term("banana") == "banana"


def test_free_is_a_hard_negative_against_fee():
    assert "free" not in SLOT_SYNONYMS
    assert canonical_term("free") == "free"
    record = {"title": "Spring Fest entry fee approved",
              "detail": "Spring Fest entry fee approved"}
    assert _matches_terms(record, content_terms("what is the entry cost?")) is True
    assert _matches_terms(record, content_terms("is entry free?")) is False


def test_matches_terms_bridges_cost_to_attend_paraphrase():
    record = {"title": "Spring Fest registration fee Rs 150",
              "detail": "Spring Fest registration fee is Rs 150"}
    assert _matches_terms(record, content_terms("What is the cost to attend?")) is True
    assert _matches_terms(record, content_terms("What is the cost-to-attend?")) is True
    assert _matches_terms(record, content_terms("what color is the bicycle?")) is False


def test_a5_cost_to_attend_answers_citing_fees(tmp_path):
    store = Store(tmp_path / "a5.sqlite3")
    store.add_file("fees.txt", [("ev_fee", "Spring Fest registration fee is Rs 150")])
    store.add_records("run-1", [
        candidate("Spring Fest registration fee Rs 150", "spring fest registration fee",
                  [("ev_fee", "Spring Fest registration fee is Rs 150")],
                  record_type="decision", status="decided",
                  date_value=None, date_raw=None,
                  detail="Spring Fest registration fee is Rs 150"),
    ])
    for question in ("What is the cost to attend?", "What is the cost-to-attend?"):
        result = store.ask(question)
        assert result["status"] == "answered", question
        assert result["memory"]["used"] is True, question
        assert "Rs 150" in result["answer"], question
        assert "/src/fees.txt" in {card["file"] for card in result["evidence"]}, question
        assert "ev_fee" in {card["id"] for card in result["evidence"]}, question

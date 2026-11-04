"""P0-1 retrieval: multi-part subquery union and the quote/caterer fold class.

The hybrid keyword channel splits multi-part questions on and/who clause
boundaries, retrieves per clause with a capped depth, and unions the top
rows before RRF fusion -- so one clause cannot drown the other clause's
gold. A minimal verb/noun fold map (quote/quoted, caterer/catering) is
applied at match time in the folded query. search() stays frozen: no
splits, no fold class.

Deterministic fixtures on disposable databases; no model calls.
"""
import pytest

from test_memory_answers import Store
from yaad.query.hybrid import (
    SUBQUERY_DEPTH,
    _folded_query,
    hybrid_search,
    retrieve_channel,
)
from yaad.query.retrieve import phrase_hits, question_phrases, search, split_subqueries, word_folds


def ids(rows):
    return [row["evidence_id"] for row in rows]


# --- subquery splitting ---

def test_split_single_part_unchanged():
    question = "What was the partnership decision?"
    assert split_subqueries(question) == [question]
    assert split_subqueries("") == []


def test_split_on_and():
    assert split_subqueries("Where is the meeting and who is catering?") == [
        "Where is the meeting", "is catering?"]
    # "and" inside a word is not a boundary.
    assert split_subqueries("What are the catering standards?") == [
        "What are the catering standards?"]


def test_split_on_who_clause():
    assert split_subqueries("Which caterer was hired who quoted the lowest invoice?") == [
        "Which caterer was hired", "quoted the lowest invoice?"]


def test_split_drops_empty_and_termless_clauses():
    assert split_subqueries("Who catered the gala and when was the invoice paid?") == [
        "catered the gala", "when was the invoice paid?"]
    # "where was it" has no content terms, so it retrieves nothing alone.
    assert split_subqueries("Who catered the gala and where was it?") == ["catered the gala"]


# --- fold map: two closed classes, both directions ---

@pytest.mark.parametrize("term,expected", [
    ("quote", ["quoted"]), ("quoted", ["quote"]),
    ("caterer", ["catering"]), ("catering", ["caterer"]),
    ("close", ["closes"]), ("closes", ["close"]),
    ("quotes", ["quoted"]), ("caterers", ["catering"]),
    ("Quoted", ["quote"]),
])
def test_word_folds_table(term, expected):
    assert word_folds(term) == expected


@pytest.mark.parametrize("term", ["invoice", "partnership", "fee", "quotation", "cater", "closed", "closing"])
def test_word_folds_closed_class(term):
    assert word_folds(term) == []


def test_word_folds_no_cross_pair_leak():
    assert "caterer" not in word_folds("quote")
    assert "catering" not in word_folds("quoted")
    assert "closes" not in word_folds("quote")
    assert "quoted" not in word_folds("close")
    assert "quote" not in word_folds("caterer")


def test_folded_query_adds_class_only():
    assert "quote" in _folded_query("who quoted the invoice?").split()
    assert "caterer" in _folded_query("who is catering?").split()
    folded = _folded_query("what is the invoice total?").split()
    assert "quote" not in folded and "quoted" not in folded
    assert "caterer" not in folded and "catering" not in folded


# --- frozen search() vs folding keyword channel ---

def test_search_frozen_but_keyword_channel_folds(tmp_path):
    store = Store(tmp_path / "fold.sqlite3")
    store.add_file("a.txt", [("ev_a", "The caterer quote is attached")])
    assert ids(search(store.db, "quoted", store.folder_id, 15)) == []
    assert ids(search(store.db, "catering", store.folder_id, 15)) == []
    assert retrieve_channel(store.db, "who quoted the invoice?", "keyword",
                            folder_id=store.folder_id) == ["ev_a"]
    assert retrieve_channel(store.db, "who is catering?", "keyword",
                            folder_id=store.folder_id) == ["ev_a"]


def test_multipart_union_rescues_drowned_gold(tmp_path):
    """One clause's long 4-term noise drowns the short gold in a combined
    query; per-clause retrieval ranks each gold first and the union keeps
    both through phrase bonus and trim."""
    store = Store(tmp_path / "multi.sqlite3")
    store.add_file("partnership.txt", [("ev_part", "Partnership decision signed")])
    store.add_file("invoice.txt", [("ev_inv", "Caterer quote attached to the catering invoice")])
    noise = [(f"ev_n{i:02d}",
              f"Partnership memo number {i}: invoice quote attached pending final decision "
              f"with additional review commentary from the committee")
             for i in range(12)]
    store.add_file("noise.txt", noise)
    question = "What was the partnership decision and who quoted the catering invoice?"
    assert len(split_subqueries(question)) == 2
    combined = ids(search(store.db, _folded_query(question), store.folder_id, 10))
    assert "ev_part" not in combined  # drowned without splitting
    channel = retrieve_channel(store.db, question, "keyword", depth=10,
                               folder_id=store.folder_id)
    assert {"ev_part", "ev_inv"} <= set(channel)
    assert len(channel) == len(set(channel))  # union dedupes
    fused = ids(hybrid_search(store.db, question, folder_id=store.folder_id))
    assert {"ev_part", "ev_inv"} <= set(fused)


def test_per_clause_depth_cap_bounds_noise(tmp_path):
    store = Store(tmp_path / "cap.sqlite3")
    store.add_file("a.txt", [(f"ev_a{i:02d}", f"Partnership memo number {i}") for i in range(25)])
    store.add_file("b.txt", [("ev_gold", "Caterer quote attached to the catering invoice")])
    question = "Partnership memo archive and who quoted the catering invoice?"
    assert len(split_subqueries(question)) == 2
    channel = retrieve_channel(store.db, question, "keyword",
                               folder_id=store.folder_id)
    clause_a_only = [eid for eid in channel if eid.startswith("ev_a")]
    assert len(clause_a_only) <= SUBQUERY_DEPTH
    assert len(channel) <= 50
    assert "ev_gold" in channel


def test_single_clause_channel_matches_plain_search(tmp_path):
    store = Store(tmp_path / "parity.sqlite3")
    store.add_file("a.txt", [("ev_a", "The caterer quote is attached"),
                             ("ev_b", "Spring Fest was great fun for everyone involved")])
    question = "quoted invoice total?"
    assert split_subqueries(question) == [question]
    rows = search(store.db, _folded_query(question), store.folder_id, 50)
    phrases = question_phrases(question)
    if phrases:
        rows = sorted(rows, key=lambda row: -phrase_hits(row["text"], phrases))
    assert retrieve_channel(store.db, question, "keyword",
                            folder_id=store.folder_id) == ids(rows[:50])

"""Phase 1 hybrid retrieval: deterministic fusion of FTS5 keyword and
Tier A memory channels through Reciprocal Rank Fusion.

Keyword-only search() is preserved byte-for-behavior; hybrid_search adds
query-time plural folding, exact-phrase ranking bonus, per-channel depth
50, fused top-30, README demotion as a deterministic tiebreak, and
per-channel ablation. Tier C units never enter the memory channel;
fused candidates still pass every existing gate (consent, taint,
validation, relevance tiers).

Pure paraphrase with no shared terms and no memory bridge stays out of
reach by design (no synonyms, no embeddings in Phase 1): those tests pin
the boundary, not a fix.

Deterministic fixtures on disposable databases; no model calls, no
production database contact.
"""
import pytest

from test_memory_answers import Store, candidate
from yaad.query.hybrid import (
    fold_term,
    hybrid_search,
    phrase_hits,
    question_phrases,
    retrieve_channel,
    rrf_fuse,
    rrf_scores,
)
from yaad.query.retrieve import search


def ids(rows):
    return [row["evidence_id"] for row in rows]


# --- folding: plurals normalize, singulars and oddballs survive ---

@pytest.mark.parametrize("term,expected", [
    ("drafts", "draft"), ("fees", "fee"), ("budgets", "budget"),
    ("stories", "story"), ("boxes", "box"), ("churches", "church"),
    ("wishes", "wish"), ("venues", "venue"), ("festivals", "festival"),
    ("fee", "fee"), ("march", "march"), ("class", "class"),
    ("glass", "glass"), ("status", "status"), ("news", "news"),
    ("gas", "gas"), ("rs", "rs"), ("150", "150"),
])
def test_fold_term_table(term, expected):
    assert fold_term(term) == expected


def test_search_ignores_folding_but_hybrid_folds(tmp_path):
    store = Store(tmp_path / "fold.sqlite3")
    store.add_file("a.txt", [("ev_a", "The draft is due March 3rd")])
    assert ids(search(store.db, "drafts status", store.folder_id, 15)) == []
    assert retrieve_channel(store.db, "drafts status", "keyword",
                            folder_id=store.folder_id) == ["ev_a"]


# --- phrases: adjacent content words boost ranking, never filter ---

def test_question_phrases_and_hits():
    assert question_phrases("where is the Spring Fest venue?") == ["spring fest venue"]
    assert question_phrases("what is the registration fee?") == ["registration fee"]
    assert question_phrases("fee?") == []
    assert question_phrases("spring fest events and venues?") == ["spring fest events"]
    gold = "Spring Fest venue is the riverside park"
    noise = "Spring Fest was great fun for everyone involved"
    assert phrase_hits(gold, ["spring fest venue"]) == 1
    assert phrase_hits(noise, ["spring fest venue"]) == 0
    assert phrase_hits(gold, ["riverside park"]) == 1


def test_phrase_bonus_orders_gold_first(tmp_path):
    store = Store(tmp_path / "phr.sqlite3")
    store.add_file("a.txt", [("ev_noise", "Spring Fest was great fun for everyone involved")])
    store.add_file("b.txt", [("ev_gold", "Spring Fest venue is the riverside park")])
    rows = retrieve_channel(store.db, "where is the Spring Fest venue?", "keyword",
                            folder_id=store.folder_id)
    assert rows[0] == "ev_gold"


# --- RRF: exact fusion math + deterministic ties ---

def test_rrf_fuse_exact_order():
    assert rrf_fuse({"a": ["x", "y", "z"], "b": ["y"]}) == ["y", "x", "z"]
    scores = rrf_scores({"a": ["x", "y"]})
    assert scores["x"] == pytest.approx(1 / 61)
    assert scores["y"] == pytest.approx(1 / 62)
    # Score ties break by evidence_id, deterministically.
    assert rrf_fuse({"a": ["m", "n"], "b": ["n", "m"]}) == ["m", "n"]


def test_readme_demotion_is_deterministic_tiebreak(tmp_path):
    store = Store(tmp_path / "rdm.sqlite3")
    store.add_file("notes.txt", [("ev_notes", "Fest update duplicate text")])
    store.add_file("README.md", [("ev_readme", "Fest update duplicate text")])
    assert ids(search(store.db, "fest update", store.folder_id, 15)) == ["ev_notes", "ev_readme"]
    assert ids(hybrid_search(store.db, "fest update?", folder_id=store.folder_id)) == [
        "ev_notes", "ev_readme"]


# --- P1: pure paraphrase without a bridge stays out (boundary pinned) ---

def test_p1_pure_paraphrase_beyond_phase1(tmp_path):
    store = Store(tmp_path / "p1.sqlite3")
    store.add_file("a.txt", [("ev_a", "The registration fee is Rs 150")])
    assert ids(search(store.db, "what is the entry cost?", store.folder_id, 15)) == []
    assert ids(hybrid_search(store.db, "what is the entry cost?",
                             folder_id=store.folder_id)) == []
    assert store.ask("what is the entry cost?")["status"] == "not_found"


# --- P2: plural inflection retrieved through folding ---

def test_p2_plural_inflection_retrieved(tmp_path):
    store = Store(tmp_path / "p2.sqlite3")
    store.add_file("a.txt", [("ev_a", "The draft is due March 3rd")])
    assert ids(search(store.db, "drafts status", store.folder_id, 15)) == []
    assert ids(hybrid_search(store.db, "drafts status", folder_id=store.folder_id)) == ["ev_a"]
    # Folding lives in the keyword channel, so ablation agrees with hybrid
    # here (no records); legacy search() stays frozen and misses.
    assert store.ask("drafts due date?", channels=("keyword",))["status"] == "answered"
    answered = store.ask("drafts due date?")
    assert answered["status"] == "answered"
    assert answered["values"] and answered["values"][0]["value"] == "March 3rd"
    assert [card["id"] for card in answered["evidence"]] == ["ev_a"]


# --- P3: depth 50 finds gold that limit 15 truncates ---

def test_p3_depth_finds_truncated_gold(tmp_path):
    store = Store(tmp_path / "p3.sqlite3")
    units = [(f"ev_{i:02d}", f"Fest update number {i} with general news") for i in range(20)]
    units.append(("ev_gold", "Fest update: the closing date is June 9"))
    store.add_file("a.txt", units)
    assert "ev_gold" not in ids(search(store.db, "fest update?", store.folder_id, 15))
    assert "ev_gold" in ids(hybrid_search(store.db, "fest update?", folder_id=store.folder_id))


# --- P4: exact names, dates, amounts keep rank 1 (keyword parity) ---

def test_p4_exact_values_keep_rank_with_keyword_parity(tmp_path):
    store = Store(tmp_path / "p4.sqlite3")
    store.add_file("a.txt", [("ev_maya", "Maya attended the review"),
                             ("ev_patel", "Patel abstained from the vote")])
    store.add_file("b.txt", [("ev_exact", "Maya Patel approved the budget")])
    store.add_file("c.txt", [("ev_mar3", "Draft due March 3rd"),
                             ("ev_mar10", "Draft due March 10th")])
    store.add_file("d.txt", [("ev_150", "Registration fee Rs 150"),
                             ("ev_200", "Registration fee Rs 200")])
    cases = [("did Maya Patel approve?", "ev_exact"),
             ("draft due march 3rd", "ev_mar3"),
             ("fee rs 150", "ev_150")]
    for question, gold in cases:
        assert ids(hybrid_search(store.db, question, folder_id=store.folder_id))[0] == gold
        assert ids(search(store.db, question, store.folder_id, 15))[0] == gold


# --- P5: memory channel rescues unretrieved paraphrase ---

def memory_bridge_store(path):
    store = Store(path)
    store.add_file("a.txt", [("ev_seen", "Registration desk opens soon"),
                             ("ev_hidden", "The entry cost is Rs 150 per person")])
    store.add_records("run-1", [candidate(
        "Registration fee Rs 150", "registration entry",
        [("ev_seen", "Registration desk opens"), ("ev_hidden", "entry cost is Rs 150")],
        detail="Registration fee Rs 150",
        record_type="decision", status="decided", date_value=None, date_raw=None)])
    return store


def test_p5_memory_channel_rescues_unretrieved_paraphrase(tmp_path):
    store = memory_bridge_store(tmp_path / "p5.sqlite3")
    question = "what is the registration fee?"
    assert ids(search(store.db, question, store.folder_id, 15)) == ["ev_seen"]
    assert retrieve_channel(store.db, question, "memory",
                            folder_id=store.folder_id) == ["ev_hidden", "ev_seen"]
    assert "ev_hidden" in ids(hybrid_search(store.db, question, folder_id=store.folder_id))
    result = store.ask(question)
    assert result["status"] == "answered"
    # Pseudo-values attach the statement value to the first claim evidence
    # in best (pre-existing policy, unchanged by fusion).
    assert result["values"] == [{"field": "amount", "value": "Rs 150",
                                 "verification": "verified_text",
                                 "evidence_ids": ["ev_seen"]}]


# --- ablation: channels compose and validate ---

def test_channel_ablation_and_validation(tmp_path):
    store = memory_bridge_store(tmp_path / "abl.sqlite3")
    question = "what is the registration fee?"
    both = ids(hybrid_search(store.db, question, folder_id=store.folder_id))
    keyword = ids(hybrid_search(store.db, question, folder_id=store.folder_id,
                                channels=("keyword",)))
    memory = ids(hybrid_search(store.db, question, folder_id=store.folder_id,
                               channels=("memory",)))
    assert keyword == ["ev_seen"]
    assert memory == ["ev_hidden", "ev_seen"]
    assert set(both) == {"ev_seen", "ev_hidden"}
    with pytest.raises(ValueError):
        hybrid_search(store.db, question, channels=("semantic",))
    with pytest.raises(ValueError):
        retrieve_channel(store.db, question, "semantic")


# --- verdict neutrality: Tier C never influences answers ---

def test_neutrality_background_match_preserves_twin(tmp_path):
    store = Store(tmp_path / "n1.sqlite3")
    store.add_file("autumn.txt", [("ev_afee", "Autumn Fest registration fee is Rs 200 per person")])
    store.add_file("spring.txt", [("ev_sfee", "Spring Fest registration fee is Rs 100 per person"),
                                  ("ev_pub", "Spring Fest publicity handled by volunteers")])
    store.add_records("run-1", [
        candidate("Autumn Fest registration fee", "registration fee",
                  [("ev_afee", "Autumn Fest registration fee is Rs 200")],
                  record_type="decision", status="decided", date_value=None, date_raw=None),
        candidate("Spring Fest registration fee", "registration fee",
                  [("ev_sfee", "Spring Fest registration fee is Rs 100")],
                  record_type="decision", status="decided", date_value=None, date_raw=None),
    ])
    twin = Store(tmp_path / "n1t.sqlite3")
    twin.add_file("autumn.txt", [("ev_afee", "Autumn Fest registration fee is Rs 200 per person")])
    twin.add_file("spring.txt", [("ev_sfee", "Spring Fest registration fee is Rs 100 per person"),
                                 ("ev_pub", "Spring Fest publicity handled by volunteers")])
    question = "What was the publicity budget for Spring Fest?"
    assert ids(hybrid_search(store.db, question, folder_id=store.folder_id)) == \
        ids(search(store.db, question, store.folder_id, 15))
    result = store.ask(question)
    reference = twin.ask(question)
    assert result["status"] == "cloud_unavailable" == reference["status"]
    assert result["answer"] == reference["answer"]
    assert result["memory"]["claims"] == []
    assert retrieve_channel(store.db, question, "memory", folder_id=store.folder_id) == []


def test_neutrality_phantom_value_and_invented_title(tmp_path):
    shop = Store(tmp_path / "n2.sqlite3")
    shop.add_file("a.txt", [("ev_a", "The registration fee is Rs 150")])
    shop.add_file("b.txt", [("ev_b", "The old note is archived")])
    shop.add_records("run-1", [candidate(
        "Registration fee history", "note archived", [("ev_b", "old note is archived")],
        detail="Registration fee history: Rs 999 in 2020",
        record_type="decision", status="decided", date_value=None, date_raw=None)])
    twin = Store(tmp_path / "n2t.sqlite3")
    twin.add_file("a.txt", [("ev_a", "The registration fee is Rs 150")])
    twin.add_file("b.txt", [("ev_b", "The old note is archived")])
    result = shop.ask("what is the registration fee?")
    reference = twin.ask("what is the registration fee?")
    assert result["status"] == "answered"
    assert result["values"] == [{"field": "amount", "value": "Rs 150",
                                 "verification": "verified_text", "evidence_ids": ["ev_a"]}]
    assert {k: v for k, v in result.items() if k != "memory"} == \
        {k: v for k, v in reference.items() if k != "memory"}
    assert result["memory"]["claims"] == []
    assert result["memory"]["context_claims"][0]["relevance"]["tier"] == "contextual"

    zebra = Store(tmp_path / "n3.sqlite3")
    zebra.add_file("a.txt", [("ev_a", "The draft is due March 3rd")])
    zebra.add_records("run-1", [candidate(
        "Zebra budget alpha", "draft due", [("ev_a", "draft is due March 3rd")],
        detail="Zebra budget alpha")])
    assert zebra.ask("zebra budget alpha?")["status"] == "not_found"


# --- fused shape, limits, and genuine-conflict preservation ---

def test_hybrid_row_shape_and_limits(tmp_path):
    store = Store(tmp_path / "lim.sqlite3")
    store.add_file("a.txt", [(f"ev_{i:02d}", f"Fest update number {i}") for i in range(40)])
    rows = hybrid_search(store.db, "fest update?", folder_id=store.folder_id)
    assert 0 < len(rows) <= 30
    assert list(rows[0].keys()) == list(search(store.db, "fest update?", store.folder_id, 1)[0].keys())


def test_genuine_conflict_preserved_through_hybrid(tmp_path):
    store = Store(tmp_path / "gconf.sqlite3")
    store.add_file("a.txt", [("ev_a", "Autumn Fest registration fee is Rs 150 per person")])
    store.add_file("b.txt", [("ev_b", "Autumn Fest registration fee is Rs 200 per person")])
    store.add_records("run-1", [
        candidate("Autumn Fest registration fee", "registration fee",
                  [("ev_a", "Autumn Fest registration fee is Rs 150")],
                  record_type="decision", status="decided", date_value=None, date_raw=None),
        candidate("Autumn Fest registration fee", "registration fee",
                  [("ev_b", "Autumn Fest registration fee is Rs 200")],
                  record_type="decision", status="decided", date_value=None, date_raw=None),
    ])
    from yaad.memory.records import mark_conflicts
    with store.db.transaction() as con:
        assert len(mark_conflicts(con, store.folder_id)) == 1
    result = store.ask("tell me about the Autumn Fest registration")
    assert result["status"] == "conflict"
    assert "disagree" in result["answer"]
    assert result["memory"]["conflict_validations"][0]["verdict"] == "supported"


def test_hybrid_ignores_candidate_that_no_longer_resolves(tmp_path, monkeypatch):
    import yaad.query.hybrid as hybrid_mod

    store = Store(tmp_path / "gone.sqlite3")
    store.add_file("a.txt", [("ev_a", "The gate code is 4821")])
    monkeypatch.setattr(
        hybrid_mod, "retrieve_channel",
        lambda db, q, ch, depth=50, folder_id=None, file_kinds=None, local=False:
        ["ev_phantom", "ev_a"] if ch == "keyword" else [])
    assert ids(hybrid_search(store.db, "gate code?", folder_id=store.folder_id)) == ["ev_a"]

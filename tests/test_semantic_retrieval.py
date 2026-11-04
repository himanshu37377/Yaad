"""Semantic retrieval channel: opt-in plumbing with stubbed vectors.

Real-weight quality is measured by eval/paraphrase_recall/run_probe.py,
not here: these tests pin the wiring (default-off, explicit opt-in,
filters, rank stability) without downloading any model.
"""
import numpy as np
import pytest

from yaad.config import Settings
from yaad.db import Database
from yaad.query import embeddings
from yaad.query.answer import answer_question
from yaad.query.hybrid import CHANNELS, hybrid_search, retrieve_channel


@pytest.fixture
def store(tmp_path):
    db = Database(tmp_path / "sem.sqlite3")
    with db.transaction() as con:
        folder_id = con.execute(
            "INSERT INTO folders(path,cloud_consent) VALUES(?,0)",
            (str(tmp_path / "docs"),)).lastrowid
        file_id = con.execute(
            """INSERT INTO files(folder_id,path,sha256,kind,size,mtime,status,cloud_consent)
               VALUES(?,?,?,?,?,?,?,?)""",
            (folder_id, "n.txt", "b" * 64, "text", 10, 1.0, "indexed", 0)).lastrowid
        for eid, text in [("e1", "The keynote address begins at nine."),
                          ("e2", "Unrelated filler about parking.")]:
            con.execute(
                "INSERT INTO units(file_id,evidence_id,kind,locator,text) VALUES(?,?,?,?,?)",
                (file_id, eid, "text_span", "{}", text))
    settings = Settings(db_path=tmp_path / "sem.sqlite3", cloud_enabled=False)
    return db, settings, folder_id


@pytest.fixture(autouse=True)
def fake_vectors(monkeypatch):
    def fake_embed(texts):
        return np.array([[1.0, 0.0] if "keynote" in text or "speech" in text
                         else [0.0, 1.0] for text in texts], dtype=np.float32)
    monkeypatch.setattr(embeddings, "embed_texts", fake_embed)
    embeddings.reset_model_cache()


def test_unknown_channel_still_rejected(store):
    db, _, folder_id = store
    with pytest.raises(ValueError, match="unknown retrieval channel"):
        retrieve_channel(db, "q", "bogus", folder_id=folder_id)


def test_semantic_channel_ranks_by_similarity(store):
    db, _, folder_id = store
    # Ordering is by similarity; the floor decides membership (next test).
    assert embeddings.semantic_rank(db, "opening speech", 5, folder_id,
                                    min_similarity=-1.0) == ["e1", "e2"]
    assert embeddings.semantic_rank(db, "   ", 5, folder_id) == []


def test_similarity_floor_drops_noise_tail(store):
    db, _, folder_id = store
    # Default floor: the unrelated unit (cosine 0) never enters fusion.
    assert retrieve_channel(db, "opening speech", "semantic",
                            folder_id=folder_id) == ["e1"]
    assert embeddings.semantic_rank(db, "opening speech", 5, folder_id,
                                    min_similarity=0.99) == ["e1"]
    assert embeddings.semantic_rank(db, "opening speech", 5, folder_id,
                                    min_similarity=1.01) == []


def test_semantic_scores_report_above_floor_candidates(store):
    db, _, folder_id = store
    assert embeddings.semantic_scores(db, "opening speech", folder_id) == {"e1": 1.0}
    assert embeddings.semantic_scores(db, "opening speech", folder_id,
                                      min_similarity=-1.0) == {"e1": 1.0, "e2": 0.0}


def test_default_answer_path_never_touches_semantic(store, monkeypatch):
    db, settings, folder_id = store

    def explode(*args, **kwargs):
        raise AssertionError("semantic channel must stay off by default")

    monkeypatch.setattr("yaad.query.hybrid._semantic_channel", explode)
    result = answer_question(db, "When does the keynote begin?", folder_id, settings)
    assert result["status"] in ("answered", "unverified", "cloud_unavailable", "not_found")


def test_opt_in_fuses_semantic_channel(store):
    db, settings, folder_id = store
    settings.semantic_retrieval = True
    result = answer_question(db, "opening speech", folder_id, settings)
    assert "e1" in [card["id"] for card in result["evidence"]]


def test_semantic_channel_respects_folder_scope(store):
    db, settings, folder_id = store
    assert embeddings.semantic_rank(db, "speech", 5, folder_id + 999) == []
    assert CHANNELS == ("keyword", "memory")


def test_min_similarity_default_is_configured(monkeypatch):
    monkeypatch.delenv("YAAD_SEMANTIC_MIN_SIMILARITY", raising=False)
    assert Settings().semantic_min_similarity == 0.30
    monkeypatch.setenv("YAAD_SEMANTIC_MIN_SIMILARITY", "0.55")
    assert Settings().semantic_min_similarity == 0.55


def test_vectors_are_cached_and_invalidated_by_content(store, monkeypatch):
    db, _, folder_id = store
    calls: list[list[str]] = []
    real_fake = embeddings.embed_texts

    def recording(texts):
        calls.append(list(texts))
        return real_fake(texts)

    monkeypatch.setattr(embeddings, "embed_texts", recording)
    embeddings.semantic_rank(db, "opening speech", 5, folder_id, min_similarity=-1.0)
    embeddings.semantic_rank(db, "opening speech", 5, folder_id, min_similarity=-1.0)
    unit_calls = [text for call in calls for text in call
                  if "keynote" in text or "filler" in text]
    # Each unit embedded exactly once across both queries; the repeat
    # query embeds only its own text and reports full cache hits.
    assert len(unit_calls) == 2
    assert embeddings.last_stats["cache_hits"] == 2
    assert embeddings.last_stats["embedded"] == 0
    with db.connect() as con:
        assert con.execute("SELECT COUNT(*) c FROM unit_embeddings").fetchone()["c"] == 2
    # Editing a unit's text invalidates exactly that unit.
    with db.transaction() as con:
        con.execute("UPDATE units SET text='A keynote moved to noon.' WHERE evidence_id='e2'")
    embeddings.semantic_rank(db, "opening speech", 5, folder_id, min_similarity=-1.0)
    assert embeddings.last_stats["embedded"] == 1
    assert embeddings.last_stats["cache_hits"] == 1


def test_candidate_universe_is_never_truncated(tmp_path):
    """520 units: the best match sits past the old silent 500 cap and
    must still rank first; stats report the true universe size."""
    db = Database(tmp_path / "wide.sqlite3")
    with db.transaction() as con:
        folder_id = con.execute(
            "INSERT INTO folders(path,cloud_consent) VALUES(?,0)",
            (str(tmp_path / "docs"),)).lastrowid
        file_id = con.execute(
            """INSERT INTO files(folder_id,path,sha256,kind,size,mtime,status,cloud_consent)
               VALUES(?,?,?,?,?,?,?,?)""",
            (folder_id, "n.txt", "c" * 64, "text", 10, 1.0, "indexed", 0)).lastrowid
        for index in range(520):
            text = ("keynote finale at noon" if index == 519
                    else f"parking note number {index}")
            con.execute(
                "INSERT INTO units(file_id,evidence_id,kind,locator,text) VALUES(?,?,?,?,?)",
                (file_id, f"u{index:03d}", "text_span", "{}", text))
    ranked = embeddings.semantic_rank(db, "opening speech", 50, folder_id,
                                      min_similarity=-1.0)
    assert ranked[0] == "u519"
    assert embeddings.last_stats["units"] == 520
    assert embeddings.last_stats["ms"] >= 0

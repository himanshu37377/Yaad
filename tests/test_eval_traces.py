"""Evaluation retrieval traces (Phase 7): transcripts must carry the
provenance needed to stage-attribute failures -- channel rankings,
fused order with RRF scores, selected evidence -- not just answers.

Offline: the V3 runner module is imported and its trace builder runs
against a seeded database. No model calls (semantic channel off).
"""
import importlib.util
from pathlib import Path

from test_memory_answers import Store, candidate

ROOT = Path(__file__).parents[1]
_spec = importlib.util.spec_from_file_location(
    "v3_runner", ROOT / "eval" / "real_eval_v3" / "run_real_eval_v3.py")
runner = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(runner)


def test_retrieval_trace_shape(tmp_path):
    store = Store(tmp_path / "trace.sqlite3")
    store.add_file("fee.txt", [("ev_fee", "The workshop fee is Rs 150 per person.")])
    store.add_file("venue.txt", [("ev_venue", "The workshop venue is Riverside Hall.")])
    store.add_records("run-1", [
        candidate("Workshop fee", "workshop fee",
                  [("ev_fee", "The workshop fee is Rs 150 per person")],
                  record_type="decision", status="decided",
                  date_value=None, date_raw=None),
    ])
    trace = runner._retrieval_trace(store.db, "What is the workshop fee?",
                                    store.folder_id, store.settings, ["ev_fee"])
    assert trace["channels"] == ["keyword", "memory"]
    assert set(trace["per_channel"]) == {"keyword", "memory"}
    assert trace["similarities"] == {}
    assert trace["fused"], "fused ranking must not be empty for a matching question"
    assert trace["fused"][0]["rank"] == 1 and trace["fused"][0]["rrf"] > 0
    assert {entry["evidence_id"] for entry in trace["fused"]} >= {"ev_fee"}
    assert trace["selected"] == ["ev_fee"]


def test_retrieval_trace_records_semantic_when_enabled(tmp_path, monkeypatch):
    """With the semantic channel on, similarities are recorded too
    (fake vectors; the point is the trace plumbing, not the model)."""
    import numpy as np

    from yaad.config import Settings
    from yaad.query import embeddings

    def fake_embed(texts):
        return np.array([[1.0, 0.0] if "keynote" in text or "speech" in text
                          else [0.0, 1.0] for text in texts], dtype=np.float32)

    monkeypatch.setattr(embeddings, "embed_texts", fake_embed)
    store = Store(tmp_path / "trace2.sqlite3")
    store.add_file("talk.txt", [("ev_talk", "The keynote address opens the fest.")])
    settings = Settings(db_path=tmp_path / "trace2.sqlite3", semantic_retrieval=True)
    trace = runner._retrieval_trace(store.db, "opening speech", store.folder_id,
                                    settings, [])
    assert trace["channels"] == ["keyword", "memory", "semantic"]
    assert trace["similarities"].get("ev_talk") == 1.0
    assert trace["per_channel"]["semantic"] == ["ev_talk"]

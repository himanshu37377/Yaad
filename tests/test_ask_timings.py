"""Stage 3: ask timing diagnostics and timeout behavior.

Durations and counts only — timings must never carry source content.
Slow-model and failure paths use stubbed clients (no network, no spend).
"""
import os
import tempfile
import time
from pathlib import Path

os.environ.setdefault("YAAD_DB_PATH", str(Path(tempfile.gettempdir()) / "yaad-ask-timing-tests.sqlite3"))

from test_memory_answers import Store

from yaad.api import main
from yaad.config import Settings
from yaad.schemas import AskRequest


def test_fast_offline_ask_reports_stage_timings(tmp_path, monkeypatch):
    store = Store(tmp_path / "fast.sqlite3")
    store.add_file("a.txt", [("ev_a", "The draft is due March 3rd")])
    monkeypatch.setattr(main, "db", store.db)
    monkeypatch.setattr(main, "settings", store.settings)
    result = main.ask(AskRequest(question="when is the draft due?", folder_id=store.folder_id))
    timings = result["timings"]
    assert set(timings) >= {"retrieval_ms", "memory_ms", "retrieved_rows",
                            "memory_records", "total_ms"}
    assert "semantic_ms" not in timings
    assert all(isinstance(value, (int, float)) for value in timings.values())
    assert timings["total_ms"] >= timings["retrieval_ms"] + timings["memory_ms"] - 5
    assert timings["retrieved_rows"] == 1 and timings["memory_records"] == 0


def test_slow_semantic_stage_is_measured(tmp_path, monkeypatch):
    from yaad.llm import meta_client

    store = Store(tmp_path / "slow.sqlite3")
    store.settings = Settings(db_path=tmp_path / "slow.sqlite3", cloud_enabled=True,
                              model_api_key="stub-key-for-tests")
    store.add_file("a.txt", [("ev_a", "The draft is due March 3rd")])
    store.add_file("b.txt", [("ev_b", "Draft due March 3rd, all teams")])

    class SlowClient:
        last_call = {"provider": "Meta Model API", "model": "muse-spark-1.3", "status_code": 200,
                     "success": True, "duration_ms": 1500, "input_tokens": 40,
                     "output_tokens": 8, "cached_tokens": 0}

        def __init__(self, _settings):
            pass

        def reason_json(self, messages, schema, prompt_id):
            time.sleep(1.5)
            return {"status": "answered", "answer": "The draft is due March 3rd.",
                    "claims": [{"claim": "The draft is due March 3rd.",
                                "evidence_ids": ["ev_a", "ev_b"]}]}

    monkeypatch.setattr(meta_client, "MetaClient", SlowClient)
    monkeypatch.setattr(main, "db", store.db)
    monkeypatch.setattr(main, "settings", store.settings)
    result = main.ask(AskRequest(question="when is the draft due?", folder_id=store.folder_id))
    assert result["status"] == "answered"
    assert result["timings"]["semantic_ms"] >= 1500
    assert result["timings"]["total_ms"] >= result["timings"]["semantic_ms"]


def test_model_failure_propagates_with_partial_diagnostics_and_egress(tmp_path, monkeypatch):
    from yaad.llm import meta_client

    store = Store(tmp_path / "err.sqlite3")
    store.settings = Settings(db_path=tmp_path / "err.sqlite3", cloud_enabled=True,
                              model_api_key="stub-key-for-tests")
    store.add_file("a.txt", [("ev_a", "The draft is due March 3rd")])
    store.add_file("b.txt", [("ev_b", "Draft due March 3rd, all teams")])

    class FailingClient:
        last_call = {"provider": "Meta Model API", "model": "muse-spark-1.3", "status_code": None,
                     "success": False, "duration_ms": 3, "input_tokens": 0,
                     "output_tokens": 0, "cached_tokens": 0, "error_code": "boom"}

        def __init__(self, _settings):
            pass

        def reason_json(self, messages, schema, prompt_id):
            raise RuntimeError("provider down")

    monkeypatch.setattr(meta_client, "MetaClient", FailingClient)
    monkeypatch.setattr(main, "db", store.db)
    monkeypatch.setattr(main, "settings", store.settings)
    result = main.ask(AskRequest(question="when is the draft due?", folder_id=store.folder_id))
    assert result["status"] == "cloud_unavailable"
    assert "Meta review is unavailable" in result["answer"]
    assert len(result["evidence"]) == 2
    assert [item["why"] for item in result["disclosure"]] == ["semantic answer review"]
    assert set(result["timings"]) >= {"retrieval_ms", "memory_ms", "semantic_ms", "total_ms"}
    with store.db.connect() as con:
        row = con.execute("SELECT purpose,error_code FROM egress_log").fetchone()
        assert (row["purpose"], row["error_code"]) == ("semantic answer review", "boom")


def test_status_listing_ask_carries_timings(tmp_path, monkeypatch):
    from test_memory_answers import candidate

    store = Store(tmp_path / "st.sqlite3")
    store.add_file("a.txt", [("ev_a", "Maybe we could book Room C")])
    store.add_records("run-1", [candidate(
        "Book Room C maybe", "book room", [("ev_a", "book Room C")],
        detail="Book Room C maybe", record_type="commitment", status="open",
        statement_kind="proposal", date_value=None, date_raw=None)])
    monkeypatch.setattr(main, "db", store.db)
    monkeypatch.setattr(main, "settings", store.settings)
    result = main.ask(AskRequest(question="which memories are proposals?",
                                 folder_id=store.folder_id))
    assert result["status"] == "answered"
    assert result["timings"]["memory_records"] == 1
    assert result["timings"]["total_ms"] >= 0

"""Recall-direction link proposal (Phase 4 regression suite).

The first extraction pass can miss a replacement link entirely (it
never cites the older statement). The second look lets the model
PROPOSE such links over a bounded, deterministically nominated pair
set; each proposal is written as a proposed supersession evidence
row and decided by the same deterministic sweep as extraction's own
links. The model never writes a relationship.

Offline: all model access is stubbed; receipts and spend accounting
run against the real pipeline.
"""
from test_memory_answers import Store, candidate

from yaad.config import Settings
from yaad.memory.extract import run_extraction

OLD_UNIT = "The committee confirmed the Spring Fest venue is Riverside Hall"
NEW_UNIT = "Spring Fest update: the venue Grand Arena is confirmed."


class ChainClient:
    last_call = {"provider": "Meta Model API", "model": "muse-spark-1.3",
                 "status_code": 200, "success": True, "duration_ms": 5,
                 "input_tokens": 900, "output_tokens": 300, "cached_tokens": 0}

    def __init__(self, records, second_look=None, verify=None):
        self.records = records
        self.second_look = second_look
        self.verify = verify
        self.calls = []

    def reason_json(self, messages, schema, prompt_id):
        self.calls.append(prompt_id)
        if prompt_id == "link-second-look-v1":
            return {"links": self.second_look or []}
        if prompt_id == "link-verification-v1":
            return {"links": self.verify or []}
        return {"records": self.records}


def venue_store(path):
    store = Store(path)
    store.settings = Settings(db_path=path, cloud_enabled=True,
                              model_api_key="stub-key-for-tests")
    store.add_file("initial.txt", [("ev_old", OLD_UNIT)])
    store.add_file("update.txt", [("ev_new", NEW_UNIT)])
    return store


def unlinked_pair():
    old = candidate("Venue Riverside Hall confirmed", "riverside venue",
                    [("ev_old", "Spring Fest venue is Riverside Hall")],
                    detail="Spring Fest venue is Riverside Hall",
                    record_type="decision", status="decided",
                    date_value=None, date_raw=None)
    new = candidate("Venue Grand Arena confirmed", "grand arena venue",
                    [("ev_new", "venue Grand Arena is confirmed")],
                    detail="Revised venue is Grand Arena and confirmed",
                    record_type="decision", status="decided",
                    date_value=None, date_raw=None)
    return [old, new]


def linked_pair():
    records = unlinked_pair()
    records[1]["evidence"].append(
        {"evidence_id": "ev_old", "role": "supersedes",
         "excerpt": "Spring Fest venue is Riverside Hall"})
    return records


def statuses(db):
    with db.connect() as con:
        return {row["title"]: row["status"] for row in
                con.execute("SELECT title,status FROM memory_records")}


def test_second_look_recovers_missed_link_through_validation(tmp_path):
    store = venue_store(tmp_path / "recover.sqlite3")
    client = ChainClient(unlinked_pair(),
                         second_look=[{"index": 0, "verdict": "supersedes",
                                       "reason": "revised venue replaces initial"}])
    summary = run_extraction(store.db, store.folder_id, "run-1",
                             store.settings, client)
    assert client.calls == ["memory-extraction-v1", "link-second-look-v1"]
    assert summary["link_second_look"] == {"checked": 1, "proposed": 1, "skipped": None}
    assert summary["link_verification"]["skipped"] == "no proposed links"
    assert statuses(store.db) == {"Venue Riverside Hall confirmed": "superseded",
                                  "Venue Grand Arena confirmed": "decided"}
    with store.db.connect() as con:
        purposes = [row["purpose"] for row in
                    con.execute("SELECT purpose FROM egress_log ORDER BY rowid")]
    assert purposes == ["link second look", "memory extraction"]


def test_second_look_none_verdict_proposes_nothing(tmp_path):
    store = venue_store(tmp_path / "none.sqlite3")
    client = ChainClient(unlinked_pair(),
                         second_look=[{"index": 0, "verdict": "none",
                                       "reason": "different matters"}])
    summary = run_extraction(store.db, store.folder_id, "run-1",
                             store.settings, client)
    assert summary["link_second_look"] == {"checked": 1, "proposed": 0, "skipped": None}
    assert set(statuses(store.db).values()) == {"decided"}
    with store.db.connect() as con:
        assert con.execute(
            "SELECT COUNT(*) c FROM memory_record_evidence WHERE role='supersedes'"
        ).fetchone()["c"] == 0


def test_bogus_proposal_is_rejected_by_deterministic_validation(tmp_path):
    """The model proposes a cross-event link; validation -- not the
    model -- has the final word, and the rejection is reported."""
    store = venue_store(tmp_path / "bogus.sqlite3")
    store.add_file("harbor.txt", [("ev_hb", "Harbor Fest venue moved to Grand Arena")])
    records = unlinked_pair()[:1] + [
        candidate("Harbor Fest Grand Arena", "grand arena venue",
                  [("ev_hb", "Harbor Fest venue moved to Grand Arena")],
                  detail="Harbor Fest venue moved to Grand Arena",
                  record_type="decision", status="decided",
                  date_value=None, date_raw=None)]
    client = ChainClient(records,
                         second_look=[{"index": 0, "verdict": "supersedes",
                                       "reason": "model error"}])
    summary = run_extraction(store.db, store.folder_id, "run-1",
                             store.settings, client)
    assert summary["link_second_look"]["proposed"] == 1
    assert statuses(store.db)["Venue Riverside Hall confirmed"] == "decided"
    assert any("contradicted" in item["reason"]
               for item in summary["rejected_supersession"])


def test_verify_rejected_pair_is_not_reproposed(tmp_path):
    store = venue_store(tmp_path / "veto.sqlite3")
    client = ChainClient(
        linked_pair(),
        verify=[{"index": 0, "verdict": "reject", "reason": "not a replacement"}],
        second_look=[{"index": 0, "verdict": "supersedes"}])
    summary = run_extraction(store.db, store.folder_id, "run-1",
                             store.settings, client)
    assert client.calls == ["memory-extraction-v1", "link-verification-v1"]
    assert summary["link_second_look"]["skipped"] == "no unlinked rival pairs"
    assert summary["link_second_look"]["proposed"] == 0
    assert statuses(store.db)["Venue Riverside Hall confirmed"] == "decided"


def test_no_pairs_means_no_second_look_call(tmp_path):
    store = venue_store(tmp_path / "quiet.sqlite3")
    solo = candidate("Venue Riverside Hall confirmed", "riverside venue",
                     [("ev_old", "Spring Fest venue is Riverside Hall")],
                     detail="Spring Fest venue is Riverside Hall",
                     record_type="decision", status="decided",
                     date_value=None, date_raw=None)
    client = ChainClient([solo])
    summary = run_extraction(store.db, store.folder_id, "run-1",
                             store.settings, client)
    assert client.calls == ["memory-extraction-v1"]
    assert summary["link_second_look"]["skipped"] == "no unlinked rival pairs"


def _marked_venue_store(path):
    from test_memory_answers import Store
    from yaad.config import Settings
    store = Store(path)
    store.settings = Settings(db_path=path, cloud_enabled=True,
                              model_api_key="stub-key-for-tests")
    store.add_file("initial.txt",
                   [("ev_old", "Original plan: Spring Fest venue is Riverside Hall")])
    store.add_file("update.txt",
                   [("ev_new", "Final notice: Spring Fest venue is Grand Arena")])
    return store


def _marked_pair():
    old = candidate("Venue Riverside Hall confirmed", "riverside venue",
                    [("ev_old", "Original plan: Spring Fest venue is Riverside Hall")],
                    detail="Original plan: Spring Fest venue is Riverside Hall",
                    record_type="decision", status="decided",
                    date_value=None, date_raw=None)
    new = candidate("Venue Grand Arena confirmed", "grand arena venue",
                    [("ev_new", "Final notice: Spring Fest venue is Grand Arena")],
                    detail="Final notice: Spring Fest venue is Grand Arena",
                    record_type="decision", status="decided",
                    date_value=None, date_raw=None)
    return old, new


def test_second_look_skips_inverted_pair_without_model_call(tmp_path):
    # Only the older statement arrives this run; the newer record it
    # rivals predates it. No nomination is possible (the only
    # direction is inverted), so the model is never consulted.
    store = _marked_venue_store(tmp_path / "skip.sqlite3")
    old, new = _marked_pair()
    store.add_records("run-0", [new])
    client = ChainClient([old], second_look=[{"index": 0, "verdict": "supersedes"}])
    summary = run_extraction(store.db, store.folder_id, "run-1",
                             store.settings, client)
    assert "link-second-look-v1" not in client.calls
    assert summary["link_second_look"]["skipped"] == "no unlinked rival pairs"
    assert set(statuses(store.db).values()) == {"decided"}


def test_second_look_nominates_newer_claimant_despite_id_order(tmp_path):
    # Out-of-order ingestion: the newer-fact record is inserted FIRST
    # (lower id). Nomination follows temporal order, not row order.
    store = _marked_venue_store(tmp_path / "ooo.sqlite3")
    old, new = _marked_pair()
    client = ChainClient([new, old],
                         second_look=[{"index": 0, "verdict": "supersedes",
                                       "reason": "final replaces original"}])
    summary = run_extraction(store.db, store.folder_id, "run-1",
                             store.settings, client)
    assert summary["link_second_look"] == {"checked": 1, "proposed": 1, "skipped": None}
    assert statuses(store.db) == {"Venue Riverside Hall confirmed": "superseded",
                                  "Venue Grand Arena confirmed": "decided"}


def test_second_look_prompt_carries_marker_facts(tmp_path):
    prompts = {}
    store = venue_store(tmp_path / "marks.sqlite3")

    class _Capture(ChainClient):
        def reason_json(self, messages, schema, prompt_id):
            prompts[prompt_id] = messages[0]["content"] if messages else ""
            return super().reason_json(messages, schema, prompt_id)

    client = _Capture(unlinked_pair(),
                      second_look=[{"index": 0, "verdict": "supersedes"}])
    run_extraction(store.db, store.folder_id, "run-1", store.settings, client)
    prompt = prompts["link-second-look-v1"]
    assert "Sequence markers (deterministic)" in prompt
    assert "revised" in prompt


def test_second_look_respects_spend_cap(tmp_path):
    from yaad.llm.meta_config import estimate_text_cost_usd
    store = venue_store(tmp_path / "cap.sqlite3")
    extraction_spend = estimate_text_cost_usd(
        store.settings.model, {"input_tokens": 900, "output_tokens": 300,
                               "input_tokens_details": {"cached_tokens": 0}})
    client = ChainClient(unlinked_pair(),
                         second_look=[{"index": 0, "verdict": "supersedes"}])
    summary = run_extraction(store.db, store.folder_id, "run-1", store.settings,
                             client, max_spend_usd=extraction_spend + 0.000001)
    assert summary["link_second_look"]["skipped"] == "spend cap"
    assert "link-second-look-v1" not in client.calls
    assert set(statuses(store.db).values()) == {"decided"}

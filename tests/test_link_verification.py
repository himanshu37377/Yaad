"""Model-verified extraction linking: propose -> model verify -> validate -> store.

The model re-checks each proposed supersession link against both
excerpts; a reject verdict removes the link, while deterministic
validation still makes every final decision. Zero proposed links
means zero extra model calls. All model access is stubbed.
"""
from test_memory_answers import Store, candidate

from yaad.config import Settings
from yaad.llm.meta_config import estimate_text_cost_usd
from yaad.memory.extract import run_extraction


class ScriptedClient:
    last_call = {"provider": "Meta Model API", "model": "muse-spark-1.3", "status_code": 200,
                 "success": True, "duration_ms": 5, "input_tokens": 900,
                 "output_tokens": 300, "cached_tokens": 0}

    def __init__(self, records, links=None, fail_verification=False):
        self.records = records
        self.links = links
        self.fail_verification = fail_verification
        self.calls = []

    def reason_json(self, messages, schema, prompt_id):
        self.calls.append(prompt_id)
        if prompt_id == "link-verification-v1":
            if self.fail_verification:
                raise RuntimeError("provider exploded")
            return {"links": self.links} if self.links is not None else {"records": []}
        return {"records": self.records}


def room_store(path):
    store = Store(path)
    store.settings = Settings(db_path=path, cloud_enabled=True, model_api_key="stub-key-for-tests")
    store.add_file("a.txt", [("ev_old", "The workshop is in Room A")])
    store.add_file("b.txt", [("ev_new", "Correction: the workshop moved to Room B")])
    return store


def linked_pair():
    old = candidate("Workshop Room A", "workshop room",
                    [("ev_old", "workshop is in Room A")],
                    record_type="decision", status="decided",
                    date_value=None, date_raw=None)
    new = candidate("Workshop Room B", "workshop room moved",
                    [("ev_new", "workshop moved to Room B")],
                    detail="Workshop Room B",
                    record_type="decision", status="decided",
                    date_value=None, date_raw=None)
    new["evidence"].append({"evidence_id": "ev_old", "role": "supersedes",
                            "excerpt": "workshop is in Room A"})
    return [old, new]


def statuses(db):
    with db.connect() as con:
        return {r["title"]: r["status"] for r in
                con.execute("SELECT title,status FROM memory_records")}


class CaptureClient(ScriptedClient):
    def __init__(self, records, links=None):
        super().__init__(records, links)
        self.prompts = {}

    def reason_json(self, messages, schema, prompt_id):
        self.prompts[prompt_id] = messages[0]["content"] if messages else ""
        return super().reason_json(messages, schema, prompt_id)


def test_verification_prompt_carries_temporal_facts(tmp_path):
    store = room_store(tmp_path / "facts.sqlite3")
    client = CaptureClient(linked_pair(), links=[{"index": 0, "verdict": "confirm"}])
    run_extraction(store.db, store.folder_id, "run-1", store.settings, client)
    prompt = client.prompts["link-verification-v1"]
    assert "Temporal evidence (deterministic)" in prompt
    assert "Computed order: claimant-newer" in prompt
    assert "older statement" not in prompt


def test_inverted_link_prompt_marks_claimant_older(tmp_path):
    store = Store(tmp_path / "inv.sqlite3")
    store.settings = Settings(db_path=tmp_path / "inv.sqlite3", cloud_enabled=True,
                              model_api_key="stub-key-for-tests")
    store.add_file("a.txt", [("ev_old", "Final notice: the workshop is in Room A")])
    store.add_file("b.txt", [("ev_new", "Original plan: the workshop is in Room B")])
    old = candidate("Workshop Room A", "workshop room final",
                    [("ev_old", "Final notice: the workshop is in Room A")],
                    record_type="decision", status="decided",
                    date_value=None, date_raw=None)
    new = candidate("Workshop Room B", "workshop room original",
                    [("ev_new", "Original plan: the workshop is in Room B")],
                    record_type="decision", status="decided",
                    date_value=None, date_raw=None)
    new["evidence"].append({"evidence_id": "ev_old", "role": "supersedes",
                            "excerpt": "Final notice: the workshop is in Room A"})
    client = CaptureClient([old, new], links=[{"index": 0, "verdict": "reject",
                                               "reason": "inverted by order"}])
    summary = run_extraction(store.db, store.folder_id, "run-1", store.settings, client)
    assert "Computed order: claimant-older" in client.prompts["link-verification-v1"]
    assert summary["rejected_supersession"] != []
    assert statuses(store.db) == {"Workshop Room A": "decided",
                                  "Workshop Room B": "decided"}


def test_no_links_means_no_extra_call(tmp_path):
    store = room_store(tmp_path / "nolink.sqlite3")
    plain = candidate("Workshop Room A", "workshop room",
                      [("ev_old", "workshop is in Room A")],
                      record_type="decision", status="decided",
                      date_value=None, date_raw=None)
    client = ScriptedClient([plain])
    summary = run_extraction(store.db, store.folder_id, "run-1", store.settings, client)
    assert client.calls == ["memory-extraction-v1"]
    assert summary["link_verification"] == {"checked": 0, "rejected": 0,
                                            "skipped": "no proposed links"}


def test_reject_verdict_removes_link_and_blocks_supersession(tmp_path):
    store = room_store(tmp_path / "reject.sqlite3")
    client = ScriptedClient(linked_pair(), links=[
        {"index": 0, "verdict": "reject", "reason": "different buildings"}])
    summary = run_extraction(store.db, store.folder_id, "run-1", store.settings, client)
    assert summary["link_verification"]["rejected"] == 1
    assert summary["superseded"] == []
    assert any("model-verification" in link["reason"]
               for link in summary["rejected_supersession"])
    assert statuses(store.db)["Workshop Room A"] == "decided"
    with store.db.connect() as con:
        roles = [row["role"] for row in con.execute(
            "SELECT role FROM memory_record_evidence")]
    assert "supersedes" not in roles


def test_confirm_verdict_keeps_deterministic_supersession(tmp_path):
    store = room_store(tmp_path / "confirm.sqlite3")
    client = ScriptedClient(linked_pair(), links=[
        {"index": 0, "verdict": "confirm", "reason": "same workshop, explicit move"}])
    summary = run_extraction(store.db, store.folder_id, "run-1", store.settings, client)
    assert summary["link_verification"] == {"checked": 1, "rejected": 0, "skipped": None}
    assert summary["superseded"] != []
    assert statuses(store.db) == {"Workshop Room A": "superseded",
                                  "Workshop Room B": "decided"}
    with store.db.connect() as con:
        purposes = [row["purpose"] for row in
                    con.execute("SELECT purpose FROM egress_log ORDER BY rowid")]
    assert purposes == ["link verification", "memory extraction"]


def test_unparseable_verdicts_fall_back_to_deterministic_validation(tmp_path):
    store = room_store(tmp_path / "fallback.sqlite3")
    client = ScriptedClient(linked_pair(), links=None)
    summary = run_extraction(store.db, store.folder_id, "run-1", store.settings, client)
    assert summary["link_verification"]["skipped"] == "unparseable verification response"
    assert summary["superseded"] != []
    assert statuses(store.db)["Workshop Room A"] == "superseded"


def test_failed_verification_call_never_breaks_extraction(tmp_path):
    store = room_store(tmp_path / "fail.sqlite3")
    client = ScriptedClient(linked_pair(), fail_verification=True)
    summary = run_extraction(store.db, store.folder_id, "run-1", store.settings, client)
    assert summary["link_verification"]["skipped"].startswith("verification call failed")
    assert summary["upserted"] == 2
    assert statuses(store.db)["Workshop Room A"] == "superseded"


def test_verification_skipped_when_spend_cap_exceeded(tmp_path):
    store = room_store(tmp_path / "cap.sqlite3")
    extraction_spend = estimate_text_cost_usd(
        store.settings.model, {"input_tokens": 900, "output_tokens": 300,
                               "input_tokens_details": {"cached_tokens": 0}})
    client = ScriptedClient(linked_pair(), links=[{"index": 0, "verdict": "reject"}])
    summary = run_extraction(store.db, store.folder_id, "run-1", store.settings, client,
                             max_spend_usd=extraction_spend + 0.000001)
    assert summary["link_verification"]["skipped"] == "spend cap"
    assert client.calls == ["memory-extraction-v1"]
    assert summary["superseded"] != []

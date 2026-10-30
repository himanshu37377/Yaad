"""CP3A: extraction pipeline tests with a stubbed model client.

No network, no model spend, temporary databases only.
"""
import pytest

from yaad.config import Settings
from yaad.db import Database
from yaad.memory.extract import (
    EXTRACTION_SCHEMA,
    RULES,
    build_extraction_prompt,
    run_extraction,
    select_units,
)
from yaad.memory.records import MEMORY_SCHEMA


class StubClient:
    def __init__(self, records):
        self.records = records
        self.seen_prompt = None
        self.last_call = {"provider": "Meta Model API", "model": "muse-spark-1.3",
                          "status_code": 200, "success": True, "duration_ms": 5,
                          "input_tokens": 900, "output_tokens": 300, "cached_tokens": 0}

    def reason_json(self, messages, schema, prompt_id):
        self.seen_prompt = messages[0]["content"]
        assert schema == EXTRACTION_SCHEMA
        return {"records": self.records}


@pytest.fixture
def store(tmp_path):
    database = Database(tmp_path / "ext.sqlite3")
    with database.transaction() as con:
        con.executescript(MEMORY_SCHEMA)
        con.execute("INSERT INTO folders(path, cloud_consent) VALUES(?, 1)", (str(tmp_path / "src"),))
        con.execute("INSERT INTO folders(path, cloud_consent) VALUES(?, 0)", (str(tmp_path / "vault"),))
        folder_id = con.execute("SELECT id FROM folders WHERE path=?",
                                (str(tmp_path / "src"),)).fetchone()["id"]
        vault_id = con.execute("SELECT id FROM folders WHERE path=?",
                               (str(tmp_path / "vault"),)).fetchone()["id"]
        for name, fid, consent, units in (
            ("a.txt", folder_id, 1, [("ev_a1", "Deliver the draft by March 3rd."),
                                     ("ev_a2", "Maya to book the seminar room."),
                                     ("ev_a3", "Update: draft moved to March 10th instead.")]),
            ("b.txt", vault_id, 0, [("ev_b1", "Secret draft due April 9th.")]),
        ):
            base = tmp_path / ("src" if fid == folder_id else "vault")
            con.execute("INSERT INTO files(folder_id,path,sha256,kind,size,mtime,status,cloud_consent)"
                        " VALUES(?,?,?,?,?,?,?,?)",
                        (fid, str(base / name), "0" * 64, "text", 10, 0.0,
                         "indexed", consent))
            file_id = con.execute("SELECT id FROM files WHERE path=?",
                                  (str(base / name),)).fetchone()["id"]
            for eid, text in units:
                con.execute("INSERT INTO units(file_id,evidence_id,kind,locator,text) VALUES(?,?,?,?,?)",
                            (file_id, eid, "text_span", '{"type":"text_span"}', text))
    settings = Settings(db_path=tmp_path / "ext.sqlite3", cloud_enabled=True,
                        model_api_key="stub-key-for-tests")
    return database, folder_id, settings


def candidate(**over):
    base = {"record_type": "deadline", "title": "Draft due", "detail": "Draft due March 3rd.",
            "date_value": "2026-03-03", "date_raw": "March 3rd", "assignee": "",
            "status": "open", "statement_kind": "explicit", "key_phrase": "deliver draft",
            "evidence": [{"evidence_id": "ev_a1", "role": "supports",
                          "excerpt": "Deliver the draft by March 3rd"}]}
    base.update(over)
    return base


def test_schema_has_strict_safeguards_at_every_level():
    assert EXTRACTION_SCHEMA["additionalProperties"] is False
    item = EXTRACTION_SCHEMA["properties"]["records"]["items"]
    assert item["additionalProperties"] is False
    leaf = item["properties"]["evidence"]["items"]
    assert leaf["additionalProperties"] is False
    assert set(item["required"]) == set(item["properties"])
    assert set(leaf["required"]) == set(leaf["properties"])


def test_prompt_carries_all_units_and_dateless_guidance(store):
    database, folder_id, _ = store
    with database.connect() as con:
        units = select_units(con, folder_id)
    assert {u["evidence_id"] for u in units} == {"ev_a1", "ev_a2", "ev_a3"}
    prompt = build_extraction_prompt(units)
    assert "ev_a1" in prompt and "ev_a2" in prompt and "ev_b1" not in prompt
    assert "dateless" in prompt.casefold()
    assert "do not\nskip dateless items" in prompt or "do not skip dateless items" in prompt.replace("\n", " ")


def test_run_extraction_stores_valid_and_reports_rejected(store):
    database, folder_id, settings = store
    bad = candidate(title="Bad date", date_value="March someday")
    client = StubClient([candidate(), bad])
    summary = run_extraction(database, folder_id, "run-1", settings, client)
    assert summary["returned"] == 2
    assert summary["accepted"] == 1
    assert len(summary["rejected"]) == 1
    assert summary["rejected"][0]["title"] == "Bad date"
    assert summary["rejected"][0]["errors"]
    with database.connect() as con:
        assert con.execute("SELECT COUNT(*) n FROM memory_records").fetchone()["n"] == 1
        assert con.execute("SELECT purpose FROM egress_log").fetchone()["purpose"] == "memory extraction"
    assert 0 < summary["spend_usd"] < 0.25


def test_run_extraction_refuses_without_consent_or_cloud(store, tmp_path):
    database, folder_id, settings = store
    client = StubClient([])
    off = Settings(db_path=tmp_path / "ext.sqlite3", cloud_enabled=False)
    with pytest.raises(RuntimeError, match="cloud gate"):
        run_extraction(database, folder_id, "run-x", off, client)
    with database.transaction() as con:
        con.execute("UPDATE folders SET cloud_consent=0 WHERE id=?", (folder_id,))
    with pytest.raises(RuntimeError, match="no consented evidence"):
        run_extraction(database, folder_id, "run-x", settings, client)
    assert client.seen_prompt is None


def test_run_extraction_ignores_stale_file_consent_after_folder_revocation(store):
    database, folder_id, settings = store
    with database.transaction() as con:
        # Revocation updates the folder only; files.* rows keep their
        # index-time consent flags, which must not authorize egress.
        con.execute("UPDATE folders SET cloud_consent=0 WHERE id=?", (folder_id,))
        assert con.execute("SELECT cloud_consent FROM files WHERE path LIKE '%a.txt'").fetchone()[0] == 1
    client = StubClient([])
    with pytest.raises(RuntimeError, match="no consented evidence"):
        run_extraction(database, folder_id, "run-x", settings, client)
    assert client.seen_prompt is None
    with database.connect() as con:
        assert con.execute("SELECT COUNT(*) n FROM egress_log").fetchone()["n"] == 0


def test_run_extraction_marks_conflicts_and_empty_results(store):
    database, folder_id, settings = store
    first = candidate(key_phrase="march draft")
    moved = candidate(title="Draft due moved", detail="Draft moved to March 10th.",
                      date_value="2026-03-10", date_raw="March 10th", key_phrase="march draft",
                      evidence=[{"evidence_id": "ev_a3", "role": "supports",
                                 "excerpt": "draft moved to March 10th"}])
    # Same action signature, different date: separate candidates, then conflicted.
    assert run_extraction(database, folder_id, "run-1", settings,
                          StubClient([first, moved]))["accepted"] == 2
    with database.connect() as con:
        assert {r["status"] for r in con.execute("SELECT status FROM memory_records")} == {"conflicted"}
    empty = run_extraction(database, folder_id, "run-2", settings, StubClient([]))
    assert empty["returned"] == 0 and empty["accepted"] == 0
    with database.connect() as con:
        # Earlier records are never deleted by a later empty run.
        assert con.execute("SELECT COUNT(*) n FROM memory_records").fetchone()["n"] == 2


def test_run_extraction_enforces_spend_cap(store):
    database, folder_id, settings = store
    client = StubClient([candidate()])
    client.last_call["output_tokens"] = 10_000_000
    with pytest.raises(RuntimeError, match="cap"):
        run_extraction(database, folder_id, "run-1", settings, client, max_spend_usd=0.01)
    with database.connect() as con:
        row = con.execute("SELECT success,error_code FROM egress_log").fetchone()
        assert (row["success"], row["error_code"]) == (0, "spend_cap_exceeded")


def test_blank_detail_rejected_with_clear_reason_before_persistence(store):
    from yaad.memory.records import validate_candidate
    database, folder_id, _ = store
    with database.connect() as con:
        unit_texts = {row["evidence_id"]: row["text"]
                      for row in con.execute("SELECT evidence_id,text FROM units")}
    errors = validate_candidate(candidate(detail=""), unit_texts)
    assert any("detail" in error for error in errors)


def test_mixed_batch_persists_valid_alongside_rejected(store):
    database, folder_id, settings = store
    client = StubClient([candidate(), candidate(detail="", title="Blank detail")])
    summary = run_extraction(database, folder_id, "run-mix", settings, client)
    assert (summary["accepted"], len(summary["rejected"])) == (1, 1)
    assert any("detail" in error for error in summary["rejected"][0]["errors"])
    with database.connect() as con:
        assert con.execute("SELECT COUNT(*) n FROM memory_records").fetchone()["n"] == 1
        row = con.execute("SELECT success,error_code FROM egress_log").fetchone()
        assert (row["success"], row["error_code"]) == (1, None)


def test_persistence_failure_still_records_receipt(store, monkeypatch):
    import yaad.memory.extract as extract_mod
    database, folder_id, settings = store
    client = StubClient([candidate()])

    def boom(*args, **kwargs):
        raise RuntimeError("simulated persistence failure")

    monkeypatch.setattr(extract_mod, "upsert_records", boom)
    with pytest.raises(RuntimeError, match="simulated persistence failure"):
        run_extraction(database, folder_id, "run-boom", settings, client)
    with database.connect() as con:
        assert con.execute("SELECT COUNT(*) n FROM memory_records").fetchone()["n"] == 0
        row = con.execute("SELECT success,error_code,estimated_cost_usd FROM egress_log").fetchone()
        assert (row["success"], row["error_code"]) == (0, "RuntimeError")
        assert row["estimated_cost_usd"] > 0


def test_prompt_teaches_replacement_links(store):
    database, folder_id, _ = store
    with database.connect() as con:
        units = select_units(con, folder_id)
    prompt = build_extraction_prompt(units)
    assert "supersedes" in prompt
    assert "Never link a proposal" in RULES


def test_extraction_supersession_link_supersedes_older_record(store):
    database, folder_id, settings = store
    older = candidate()
    newer = candidate(
        title="Draft due moved", detail="Draft moved to March 10th.",
        date_value="2026-03-10", date_raw="March 10th",
        evidence=[{"evidence_id": "ev_a3", "role": "supports",
                   "excerpt": "draft moved to March 10th"},
                  {"evidence_id": "ev_a1", "role": "supersedes",
                   "excerpt": "Deliver the draft by March 3rd"}])
    summary = run_extraction(database, folder_id, "run-1", settings,
                             StubClient([older, newer]))
    assert summary["accepted"] == 2
    assert len(summary["superseded"]) == 1
    with database.connect() as con:
        rows = {row["date_value"]: row["status"] for row in
                con.execute("SELECT date_value,status FROM memory_records")}
    assert rows["2026-03-03"] == "superseded"
    assert rows["2026-03-10"] == "open"


def test_run_extraction_refuses_oversized_prompt_before_model_call(store):
    database, folder_id, settings = store
    with database.transaction() as con:
        file_id = con.execute("SELECT id FROM files WHERE path LIKE '%a.txt'").fetchone()["id"]
        for n in range(1200):
            con.execute("INSERT INTO units(file_id,evidence_id,kind,locator,text) VALUES(?,?,?,?,?)",
                        (file_id, f"ev_pad{n}", "text_span", '{"type":"text_span"}',
                         f"Padding sentence number {n}. " * 40))
    client = StubClient([])
    with pytest.raises(RuntimeError, match="exceeds cap"):
        run_extraction(database, folder_id, "run-x", settings, client)
    assert client.seen_prompt is None
    with database.connect() as con:
        assert con.execute("SELECT COUNT(*) n FROM egress_log").fetchone()["n"] == 0
        assert con.execute("SELECT COUNT(*) n FROM memory_records").fetchone()["n"] == 0

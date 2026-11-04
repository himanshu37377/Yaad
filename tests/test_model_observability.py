"""Model-failure observability (offline, stubbed clients).

Every model stage must report whether the model ran: ``model_verified``
(reran and validated), ``deterministic_only`` (never attempted), or
``model_error`` (attempted and failed, deterministic fallback standing
in explicitly). No network; stub clients simulate transport, HTTP,
and validation failures.
"""
from pydantic import ValidationError

from yaad.config import Settings
from yaad.db import Database
from yaad.llm.meta_api import MetaAPIError
from yaad.llm.model_status import (
    DETERMINISTIC_ONLY,
    MODEL_ERROR,
    MODEL_VERIFIED,
    call_snapshot,
    error_category,
)
from yaad.query.verify import verify_claims


def _db(tmp_path):
    db = Database(tmp_path / "obs.sqlite3")
    with db.transaction() as con:
        con.execute("INSERT INTO folders(id,path,cloud_consent) VALUES(1,'/d',1)")
    return db


def _settings():
    return Settings(cloud_enabled=True, model_api_key="stub",
                    model_provider="local", model="muse-spark-1.3")


class _FailingClient:
    def __init__(self, exc):
        self._exc = exc
        self.last_call = {"model": "muse-spark-1.3", "status_code": 400,
                          "success": False, "error_code": "http_400"}

    def reason_json(self, messages, schema, prompt_id):
        raise self._exc


class _GarbageClient:
    last_call = {"model": "muse-spark-1.3", "status_code": 200, "success": True,
                 "input_tokens": 10, "output_tokens": 5}

    def reason_json(self, messages, schema, prompt_id):
        return {"items": [{"index": 0, "verdict": "maybe"}]}


class _OkClient:
    last_call = {"model": "muse-spark-1.3", "status_code": 200, "success": True,
                 "input_tokens": 100, "output_tokens": 20}

    def reason_json(self, messages, schema, prompt_id):
        return {"items": [{"index": 0, "verdict": "supported", "reason": "ok"}]}


def test_verify_transport_failure_is_model_error(tmp_path):
    db = _db(tmp_path)
    rep = verify_claims([("The celebration takes place at Riverside Hall.", ["e1"])],
                        {"e1": "Autumn Fest will be held at Riverside Hall."},
                        settings=_settings(), db=db,
                        client=_FailingClient(MetaAPIError(400, {}, 5, {})),
                        use_model=True)
    assert rep.status == MODEL_ERROR
    assert rep.model_error == "http_400"
    assert rep.model_calls == 0
    assert rep.claims[0].model_used is False
    assert rep.model_info["http_status"] == 400


def test_verify_bad_response_is_response_validation(tmp_path):
    db = _db(tmp_path)
    rep = verify_claims([("The celebration takes place at Riverside Hall.", ["e1"])],
                        {"e1": "Autumn Fest will be held at Riverside Hall."},
                        settings=_settings(), db=db, client=_GarbageClient(),
                        use_model=True)
    assert rep.status == MODEL_ERROR
    assert rep.model_error == "response_validation"


def test_verify_gate_refusal_is_deterministic_only(tmp_path):
    db = _db(tmp_path)
    settings = Settings(cloud_enabled=False, model_provider="cloud")
    rep = verify_claims([("The celebration takes place at Riverside Hall.", ["e1"])],
                        {"e1": "Autumn Fest will be held at Riverside Hall."},
                        settings=settings, db=db, client=_OkClient(), use_model=True)
    assert rep.status == DETERMINISTIC_ONLY
    assert rep.model_info["attempted"] is False
    assert rep.claims[0].model_used is False


def test_verify_success_is_model_verified(tmp_path):
    db = _db(tmp_path)
    rep = verify_claims([("The celebration takes place at Riverside Hall.", ["e1"])],
                        {"e1": "Autumn Fest will be held at Riverside Hall."},
                        settings=_settings(), db=db, client=_OkClient(), use_model=True)
    assert rep.status == MODEL_VERIFIED
    assert rep.model_calls == 1
    assert rep.claims[0].model_used is True
    assert rep.model_info["model"] == "muse-spark-1.3"


def test_pipeline_stage_failure_is_model_error(tmp_path):
    from yaad.query.pipeline import run_pipeline
    db = Database(tmp_path / "p.sqlite3")
    with db.transaction() as con:
        con.execute("INSERT INTO folders(id,path,cloud_consent) VALUES(1,'/d',0)")
        con.execute("""INSERT INTO files(id,folder_id,path,sha256,kind,size,mtime,status)
                       VALUES(1,1,'/d/a.txt','h','text',10,1.0,'indexed')""")
        con.execute("""INSERT INTO units(file_id,evidence_id,kind,locator,text) VALUES
                       (1,'e1','text','{}','Autumn Fest will be held at Riverside Hall.'),
                       (1,'e2','text','{}','Revised venue is Grand Arena for Autumn Fest.')""")
    res = run_pipeline(db, "Two files contradict the venue; what changed and which is current?",
                       1, _settings(),
                       client=_FailingClient(MetaAPIError(503, {}, 5, {})),
                       use_model=True)
    by_stage = {s.stage: s for s in res.stages}
    assert by_stage["evidence_classification"].status == MODEL_ERROR
    assert by_stage["evidence_classification"].error_category == "http_503"
    assert res.model_calls == 0
    assert res.status in ("answered", "conflict", "not_found")


def test_extraction_verify_failure_carries_error_code(tmp_path):
    from test_memory_answers import Store, candidate
    from yaad.memory.extract import run_extraction
    store = Store(tmp_path / "x.sqlite3")
    store.settings = Settings(db_path=tmp_path / "x.sqlite3", cloud_enabled=True,
                              model_api_key="stub-key-for-tests")
    store.add_file("a.txt", [("ev_old", "The workshop is in Room A")])
    store.add_file("b.txt", [("ev_new", "Correction: the workshop moved to Room B")])
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

    class _ExtractThenFail:
        last_call = {"provider": "Meta Model API", "model": "muse-spark-1.3",
                     "status_code": 200, "success": True, "duration_ms": 5,
                     "input_tokens": 50, "output_tokens": 10, "cached_tokens": 0}

        def reason_json(self, messages, schema, prompt_id):
            if prompt_id == "memory-extraction-v1":
                return {"records": [old, new]}
            type(self).last_call = {"provider": "Meta Model API",
                                    "model": "muse-spark-1.3", "status_code": 400,
                                    "success": False, "error_code": "http_400"}
            raise MetaAPIError(400, {}, 5, {})

    summary = run_extraction(store.db, store.folder_id, "t1", store.settings,
                             _ExtractThenFail())
    skipped = summary["link_verification"]["skipped"]
    assert skipped.startswith("verification call failed")
    assert "http_400" in skipped
    assert summary["upserted"] == 2


def test_error_category_mapping():
    assert error_category(MetaAPIError(429, {}, 1, {})) == "http_429"
    assert error_category(TimeoutError()) == "transport"
    try:
        raise ValueError("bad json")
    except ValueError as exc:
        assert error_category(exc) == "malformed_response"


def test_call_snapshot_has_no_secrets():
    class _C:
        last_call = {"model": "m", "status_code": 200, "input_tokens": 1,
                     "output_tokens": 2, "error_code": None,
                     "api_key": "SECRET", "prompt": "private"}

    snap = call_snapshot(_C())
    assert snap == {"model": "m", "http_status": 200, "input_tokens": 1,
                    "output_tokens": 2, "error_code": None}

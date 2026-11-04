"""API reliability and spend ceilings (Phase 6 regression suite).

Transport retries are tested at the urlopen boundary with an injected
sleep (no real waiting); spend ceilings at each operation's
pre-flight gate with stubbed clients. No network, no credits.
"""
import io
import json
from email.message import Message
from urllib.error import HTTPError

import pytest
from test_memory_answers import Store

import yaad.llm.meta_api as meta_api
from yaad.config import Settings
from yaad.llm import get_client
from yaad.llm.meta_api import MetaAPIError, request_json, request_multipart
from yaad.memory.extract import run_extraction


# ---------------------------------------------------------------------------
# Transport retries
# ---------------------------------------------------------------------------

class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = json.dumps(payload).encode()
        self.status = status
        self.headers = Message()

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def http_error(code, retry_after=None):
    headers = Message()
    if retry_after is not None:
        headers["Retry-After"] = str(retry_after)
    return HTTPError("https://api.meta.ai/v1/chat", code, "err", headers,
                     io.BytesIO(b'{"error": "boom"}'))


def run_sequence(monkeypatch, outcomes):
    counter = []

    def fake_urlopen(request, timeout=None):
        counter.append(1)
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(meta_api, "urlopen", fake_urlopen)
    return counter


def test_429_then_success_retries_once(monkeypatch):
    counter = run_sequence(monkeypatch, [http_error(429), FakeResponse({"ok": True})])
    sleeps = []
    body, meta = request_json("key", "/chat", {}, sleep=sleeps.append)
    assert body == {"ok": True} and meta["status_code"] == 200
    assert len(counter) == 2 and sleeps == [0.5]


def test_retry_after_header_is_honored(monkeypatch):
    run_sequence(monkeypatch, [http_error(429, retry_after=2), FakeResponse({"ok": True})])
    sleeps = []
    request_json("key", "/chat", {}, sleep=sleeps.append)
    assert sleeps == [2.0]


def test_500_then_success_retries(monkeypatch):
    counter = run_sequence(monkeypatch, [http_error(500), FakeResponse({"ok": 1})])
    body, _meta = request_json("key", "/chat", {}, sleep=lambda _s: None)
    assert body == {"ok": 1} and len(counter) == 2


def test_persistent_429_exhausts_attempts(monkeypatch):
    counter = run_sequence(monkeypatch, [http_error(429)] * 3)
    sleeps = []
    with pytest.raises(MetaAPIError) as excinfo:
        request_json("key", "/chat", {}, sleep=sleeps.append)
    assert excinfo.value.status_code == 429
    assert len(counter) == 3 and sleeps == [0.5, 1.0]


def test_permanent_4xx_is_not_retried(monkeypatch):
    counter = run_sequence(monkeypatch, [http_error(400)])
    sleeps = []
    with pytest.raises(MetaAPIError) as excinfo:
        request_json("key", "/chat", {}, sleep=sleeps.append)
    assert excinfo.value.status_code == 400
    assert len(counter) == 1 and sleeps == []


def test_timeout_then_success_retries(monkeypatch):
    counter = run_sequence(monkeypatch, [TimeoutError("slow"), FakeResponse({"ok": 2})])
    body, _meta = request_json("key", "/chat", {}, sleep=lambda _s: None)
    assert body == {"ok": 2} and len(counter) == 2


def test_persistent_timeout_reraises(monkeypatch):
    counter = run_sequence(monkeypatch, [TimeoutError("slow")] * 3)
    with pytest.raises(TimeoutError):
        request_json("key", "/chat", {}, sleep=lambda _s: None)
    assert len(counter) == 3


def test_multipart_shares_the_retry_policy(monkeypatch):
    counter = run_sequence(monkeypatch, [http_error(503), FakeResponse({"ok": 3})])
    body, _meta = request_multipart(
        "key", "/audio", {"file": ("a.wav", b"data", "audio/wav")},
        sleep=lambda _s: None)
    assert body == {"ok": 3} and len(counter) == 2


# ---------------------------------------------------------------------------
# Spend ceilings
# ---------------------------------------------------------------------------

class CountingClient:
    last_call = {"provider": "Meta Model API", "model": "muse-spark-1.3",
                 "status_code": 200, "success": True, "duration_ms": 1,
                 "input_tokens": 10, "output_tokens": 5, "cached_tokens": 0}

    def __init__(self):
        self.calls = 0

    def reason_json(self, messages, schema, prompt_id):
        self.calls += 1
        return {"records": []}


def test_global_cap_refuses_extraction_before_any_call(tmp_path):
    store = Store(tmp_path / "cap.sqlite3")
    store.add_file("a.txt", [("ev_a", "The draft is due March 3rd")])
    with store.db.transaction() as con:
        con.execute(
            """INSERT INTO egress_log(purpose,provider,model,bytes,content_kind,
                   success,estimated_cost_usd) VALUES('memory extraction',
                   'Meta Model API','muse-spark-1.3',10,'evidence units',1,5.0)""")
    settings = Settings(db_path=tmp_path / "cap.sqlite3", cloud_enabled=True,
                        model_api_key="stub-key-for-tests", cloud_spend_cap_usd=1.0)
    client = CountingClient()
    with pytest.raises(RuntimeError, match="global cloud spend cap"):
        run_extraction(store.db, store.folder_id, "run-1", settings, client)
    assert client.calls == 0


def test_review_ceiling_refuses_before_provider(tmp_path, monkeypatch):
    import test_claim_grounding as grounding
    import yaad.llm.meta_client as meta_client
    from yaad.query.answer import answer_question

    store = grounding.review_store(tmp_path)
    store.settings = Settings(db_path=tmp_path / "claims.sqlite3", cloud_enabled=True,
                              model_api_key="stub-key-for-tests",
                              review_max_spend_usd=0.0000001)
    calls = []
    grounding.StubReview.payload = {
        "status": "answered", "answer": "The workshop fee is Rs 150 per person.",
        "claims": [{"claim": "The workshop fee is Rs 150 per person.",
                    "evidence_ids": ["ev_fee"]}]}
    original = grounding.StubReview.reason_json

    def counting(self, messages, schema, prompt_id):
        calls.append(prompt_id)
        return original(self, messages, schema, prompt_id)

    monkeypatch.setattr(meta_client, "MetaClient", grounding.StubReview)
    monkeypatch.setattr(grounding.StubReview, "reason_json", counting)
    result = answer_question(store.db, "What is the workshop fee and where is it held?",
                             store.folder_id, store.settings)
    assert calls == []  # refused before the provider was constructed/called
    with store.db.connect() as con:
        assert con.execute(
            "SELECT COUNT(*) c FROM egress_log WHERE purpose='semantic answer review'"
        ).fetchone()["c"] == 0


def test_review_global_cap_counts_expected_output(tmp_path, monkeypatch):
    import test_claim_grounding as grounding
    import yaad.llm.meta_client as meta_client
    from yaad.query.answer import answer_question

    store = grounding.review_store(tmp_path)
    store.settings = Settings(db_path=tmp_path / "claims.sqlite3", cloud_enabled=True,
                              model_api_key="stub-key-for-tests",
                              cloud_spend_cap_usd=0.0001)
    calls = []
    grounding.StubReview.payload = {
        "status": "answered", "answer": "The workshop fee is Rs 150 per person.",
        "claims": [{"claim": "The workshop fee is Rs 150 per person.",
                    "evidence_ids": ["ev_fee"]}]}
    original = grounding.StubReview.reason_json

    def counting(self, messages, schema, prompt_id):
        calls.append(prompt_id)
        return original(self, messages, schema, prompt_id)

    monkeypatch.setattr(meta_client, "MetaClient", grounding.StubReview)
    monkeypatch.setattr(grounding.StubReview, "reason_json", counting)
    result = answer_question(store.db, "What is the workshop fee and where is it held?",
                             store.folder_id, store.settings)
    # An input-only estimate (~$0.00002 here) slips under the cap while the
    # real call costs output-dominated ~$0.0017 (live-measured). The gate
    # must budget expected output, so this cap refuses before any call.
    assert calls == []
    with store.db.connect() as con:
        assert con.execute(
            "SELECT COUNT(*) c FROM egress_log WHERE purpose='semantic answer review'"
        ).fetchone()["c"] == 0


def test_indexing_ceiling_stops_at_the_limit(tmp_path):
    from yaad.db import Database
    from yaad.scan.worker import Indexer

    db = Database(tmp_path / "idx.sqlite3")
    settings = Settings(db_path=tmp_path / "idx.sqlite3", index_max_spend_usd=0.04,
                        model_api_key="stub-key-for-tests")

    class StubClient:
        last_call = {"provider": "Meta Model API", "model": "muse-spark-1.3",
                     "status_code": 200, "success": True, "duration_ms": 1,
                     "input_tokens": 10, "output_tokens": 5, "cached_tokens": 0,
                     "estimated_cost_usd": 0.04}

    indexer = Indexer(db, settings)
    operations = []
    indexer._cloud_call(lambda: operations.append(1) or {"ok": 1},
                        "vision test", 10, "image", client=StubClient())
    assert operations == [1]  # 0.04 spent, ceiling not yet reached
    with pytest.raises(RuntimeError, match="indexing spend ceiling"):
        indexer._cloud_call(lambda: operations.append(2) or {"ok": 2},
                            "vision test", 10, "image", client=StubClient())
    assert operations == [1]  # second call never started


def test_unknown_provider_never_falls_back():
    with pytest.raises(ValueError, match="Unknown model provider"):
        get_client(Settings(model_provider="some-other-host"))


# ---------------------------------------------------------------------------
# Deterministic structured calls: temperature 0 is pinned on every
# strict-schema path (live-proven accepted 2026-11-03). Verification
# verdicts must not flip run to run.
# ---------------------------------------------------------------------------

def _capture_payload(monkeypatch, seen):
    import yaad.llm.meta_api as meta_api_mod

    def fake_urlopen(request, *args, **kwargs):
        seen.append(json.loads(request.data.decode()))
        body = {"output": [{"content": [{"type": "output_text",
                                         "text": json.dumps({"ok": True})}]}],
                "usage": {"input_tokens": 10, "output_tokens": 5}}
        return FakeResponse(body)

    monkeypatch.setattr(meta_api_mod, "urlopen", fake_urlopen)


def test_reason_json_pins_temperature_zero(monkeypatch):
    from yaad.llm.meta_client import MetaClient
    seen = []
    _capture_payload(monkeypatch, seen)
    client = MetaClient(Settings(model_api_key="stub"))
    client.reason_json([{"role": "user", "content": "hi"}], {"type": "object"}, "t")
    assert seen[0]["temperature"] == 0


def test_vision_and_compare_pin_temperature_zero(monkeypatch, tmp_path):
    from PIL import Image
    from yaad.llm.meta_client import MetaClient
    img = tmp_path / "t.png"
    Image.new("RGB", (8, 8), (9, 9, 9)).save(img)
    raw = img.read_bytes()
    seen = []
    _capture_payload(monkeypatch, seen)
    client = MetaClient(Settings(model_api_key="stub"))
    client.vision_extract(raw, {"type": "object"}, "v")
    client.compare_images(raw, raw, {"type": "object"}, "c")
    assert [p["temperature"] for p in seen] == [0, 0]

"""Local-model inference via an Ollama-compatible runtime.

A fake in-process Ollama server stands in for weights this repo never
downloads: contract tests pin the request/response shapes, and mode tests
prove local operation can never invoke the cloud client.
"""
import base64
import io
import json
import threading
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from yaad.config import Settings
from yaad.db import Database


class FakeOllamaHandler(BaseHTTPRequestHandler):
    requests = []
    responder = None

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        type(self).requests.append({"path": self.path, "body": body})
        status, payload = type(self).responder(body)
        if isinstance(payload, dict):
            data = json.dumps(payload).encode()
        else:
            data = payload
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


@pytest.fixture
def ollama():
    FakeOllamaHandler.requests = []
    try:
        server = ThreadingHTTPServer(("127.0.0.1", 0), FakeOllamaHandler)
    except PermissionError:
        pytest.skip("sandbox forbids binding a loopback test server")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server, FakeOllamaHandler
    server.shutdown()
    thread.join()


def chat_reply(content, model="muse-glimmer:30b-test", status=200):
    return status, {"model": model, "message": {"role": "assistant", "content": content},
                    "done": True, "prompt_eval_count": 11, "eval_count": 7}


def local_settings(tmp_path, endpoint, **over):
    kwargs = {"db_path": tmp_path / "local.sqlite3", "model_provider": "local",
              "local_endpoint": endpoint, "local_model": "muse-glimmer:30b-test",
              "cloud_enabled": False, "model_api_key": None}
    kwargs.update(over)
    return Settings(**kwargs)


def test_factory_defaults_to_cloud_and_rejects_unknown_provider(tmp_path, monkeypatch):
    from yaad.llm import get_client
    import yaad.llm.meta_client as meta_client

    assert Settings(db_path=tmp_path / "d.sqlite3").model_provider == "cloud"

    made = []
    monkeypatch.setattr(meta_client, "MetaClient", lambda settings: made.append(settings))
    get_client(Settings(db_path=tmp_path / "d.sqlite3"))
    assert len(made) == 1

    with pytest.raises(ValueError, match="Unknown model provider"):
        get_client(Settings(db_path=tmp_path / "d.sqlite3", model_provider="bogus"))


def test_factory_returns_local_client_without_key_or_cloud(tmp_path):
    from yaad.llm import get_client
    from yaad.llm.local_client import LocalClient

    client = get_client(local_settings(tmp_path, "http://127.0.0.1:9"))
    assert isinstance(client, LocalClient)
    assert client.model == "muse-glimmer:30b-test"


def test_reason_json_contract(ollama, tmp_path):
    from yaad.llm.local_client import LocalClient

    server, handler = ollama
    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}}
    handler.responder = lambda body: chat_reply(json.dumps({"ok": True}))
    client = LocalClient(local_settings(tmp_path, f"http://127.0.0.1:{server.server_port}"))

    assert client.reason_json([{"role": "user", "content": "hi"}], schema, "probe") == {"ok": True}

    assert len(handler.requests) == 1
    request = handler.requests[0]
    assert request["path"] == "/api/chat"
    assert request["body"]["model"] == "muse-glimmer:30b-test"
    assert request["body"]["stream"] is False
    assert request["body"]["format"] == schema
    assert request["body"]["messages"][0]["role"] == "system"
    assert "JSON" in request["body"]["messages"][0]["content"]
    assert request["body"]["messages"][1] == {"role": "user", "content": "hi"}

    receipt = client.last_call
    assert receipt["provider"] == "local"
    assert receipt["model"] == "muse-glimmer:30b-test"
    assert receipt["success"] is True
    assert receipt["estimated_cost_usd"] == 0.0
    assert (receipt["input_tokens"], receipt["output_tokens"]) == (11, 7)


def test_vision_extract_contract(ollama, tmp_path):
    from yaad.llm.local_client import LocalClient

    server, handler = ollama
    schema = {"type": "object", "properties": {"caption": {"type": "string"}}}
    handler.responder = lambda body: chat_reply(json.dumps({"caption": "a poster"}))
    client = LocalClient(local_settings(tmp_path, f"http://127.0.0.1:{server.server_port}"))

    assert client.vision_extract(b"\x89PNG", schema, "probe") == {"caption": "a poster"}

    body = handler.requests[0]["body"]
    assert body["format"] == schema and body["stream"] is False
    assert body["messages"][-1]["images"] == [base64.b64encode(b"\x89PNG").decode()]
    assert "JSON" in body["messages"][-1]["content"]


def test_unsupported_operations_fail_clearly(tmp_path):
    from yaad.llm.local_client import LocalClient, LocalUnsupportedError

    client = LocalClient(local_settings(tmp_path, "http://127.0.0.1:9"))
    with pytest.raises(LocalUnsupportedError, match="[Ll]ocal model"):
        client.transcribe("/tmp/nowhere.wav")
    with pytest.raises(LocalUnsupportedError, match="[Ll]ocal mode"):
        client.understand_pdf(b"%PDF", "f.pdf", [(1, "text")])
    with pytest.raises(LocalUnsupportedError):
        client.describe_photo(b"\x89PNG")
    assert client.last_call["error_code"] == "local_unsupported"
    assert client.last_call["provider"] == "local"


def test_unreachable_endpoint_maps_clearly(tmp_path):
    import socket
    from yaad.llm.local_client import LocalClient, LocalModelError

    probe = socket.socket()
    try:
        probe.bind(("127.0.0.1", 0))
    except PermissionError:
        probe.close()
        pytest.skip("sandbox forbids binding a loopback probe socket")
    dead = f"http://127.0.0.1:{probe.getsockname()[1]}"
    probe.close()
    client = LocalClient(local_settings(tmp_path, dead))
    with pytest.raises(LocalModelError, match="did not respond"):
        client.reason_json([{"role": "user", "content": "hi"}], {"type": "object"}, "probe")
    assert client.last_call["error_code"] == "local_unreachable"
    assert client.last_call["success"] is False


def test_missing_model_maps_to_pull_hint(ollama, tmp_path):
    from yaad.llm.local_client import LocalClient, LocalModelError

    server, handler = ollama
    handler.responder = lambda body: (404, {"error": "model 'muse-glimmer:30b-test' not found, try pulling it first"})
    client = LocalClient(local_settings(tmp_path, f"http://127.0.0.1:{server.server_port}"))
    with pytest.raises(LocalModelError, match="ollama pull muse-glimmer:30b-test"):
        client.reason_json([{"role": "user", "content": "hi"}], {"type": "object"}, "probe")
    assert client.last_call["error_code"] == "local_model_missing"


def test_malformed_content_maps_clearly(ollama, tmp_path):
    from yaad.llm.local_client import LocalClient, LocalModelError

    server, handler = ollama
    handler.responder = lambda body: chat_reply("not json {{{")
    client = LocalClient(local_settings(tmp_path, f"http://127.0.0.1:{server.server_port}"))
    with pytest.raises(LocalModelError):
        client.reason_json([{"role": "user", "content": "hi"}], {"type": "object"}, "probe")
    assert client.last_call["error_code"] == "malformed_structured_response"


def seed_store(tmp_path, consent):
    from yaad.memory.records import MEMORY_SCHEMA

    database = Database(tmp_path / "ext.sqlite3")
    with database.transaction() as con:
        con.executescript(MEMORY_SCHEMA)
        con.execute("INSERT INTO folders(path, cloud_consent) VALUES(?, ?)",
                    (str(tmp_path / "src"), consent))
        folder_id = con.execute("SELECT id FROM folders").fetchone()["id"]
        con.execute("INSERT INTO files(folder_id,path,sha256,kind,size,mtime,status,cloud_consent)"
                    " VALUES(?,?,?,?,?,?,?,?)",
                    (folder_id, str(tmp_path / "src" / "a.txt"), "0" * 64, "text",
                     10, 0.0, "indexed", consent))
        file_id = con.execute("SELECT id FROM files").fetchone()["id"]
        con.execute("INSERT INTO units(file_id,evidence_id,kind,locator,text) VALUES(?,?,?,?,?)",
                    (file_id, "ev_a1", "text_span", '{"type":"text_span"}',
                     "Deliver the draft by March 3rd."))
    return database, folder_id


def test_extraction_runs_local_without_cloud_key_or_consent(ollama, tmp_path, monkeypatch):
    import yaad.llm.meta_client as meta_client
    from yaad.memory.extract import run_extraction

    def bomb(settings):
        raise AssertionError("cloud client must never be constructed in local mode")

    monkeypatch.setattr(meta_client, "MetaClient", bomb)
    server, handler = ollama
    good = {"record_type": "deadline", "title": "Draft due", "detail": "Draft due March 3rd.",
            "status": "open", "statement_kind": "explicit", "key_phrase": "deliver draft",
            "date_value": "2026-03-03", "date_raw": "March 3rd",
            "evidence": [{"evidence_id": "ev_a1", "role": "supports",
                          "excerpt": "Deliver the draft by March 3rd"}]}
    bad = dict(good, evidence=[{"evidence_id": "ev_nope", "role": "supports",
                                "excerpt": "invented"}])
    handler.responder = lambda body: chat_reply(json.dumps({"records": [good, bad]}))
    database, folder_id = seed_store(tmp_path, consent=0)
    settings = local_settings(tmp_path, f"http://127.0.0.1:{server.server_port}")

    summary = run_extraction(database, folder_id, "run-local", settings,
                             __import__("yaad.llm", fromlist=["get_client"]).get_client(settings))

    assert (summary["accepted"], len(summary["rejected"])) == (1, 1)
    assert summary["spend_usd"] == 0.0
    with database.connect() as con:
        row = con.execute("SELECT provider,model,estimated_cost_usd,input_tokens,output_tokens"
                          " FROM egress_log WHERE purpose='memory extraction'").fetchone()
    assert row["provider"] == "local" and row["model"] == "muse-glimmer:30b-test"
    assert row["estimated_cost_usd"] == 0.0
    assert (row["input_tokens"], row["output_tokens"]) == (11, 7)


def test_extraction_local_refuses_empty_folder(ollama, tmp_path):
    from yaad.memory.extract import run_extraction
    from yaad.memory.records import MEMORY_SCHEMA

    server, handler = ollama
    handler.responder = lambda body: chat_reply(json.dumps({"records": []}))
    database = Database(tmp_path / "empty.sqlite3")
    with database.transaction() as con:
        con.executescript(MEMORY_SCHEMA)
        con.execute("INSERT INTO folders(path, cloud_consent) VALUES(?, 0)", (str(tmp_path),))
        folder_id = con.execute("SELECT id FROM folders").fetchone()["id"]
    settings = local_settings(tmp_path, f"http://127.0.0.1:{server.server_port}")
    with pytest.raises(RuntimeError, match="no evidence units"):
        run_extraction(database, folder_id, "run-empty", settings,
                       __import__("yaad.llm", fromlist=["get_client"]).get_client(settings))
    assert handler.requests == []


def seed_answers(tmp_path):
    database = Database(tmp_path / "ans.sqlite3")
    with database.transaction() as con:
        con.execute("INSERT INTO folders(path, cloud_consent) VALUES(?, 0)", (str(tmp_path),))
        folder_id = con.execute("SELECT id FROM folders").fetchone()["id"]
        for name, eid, text in (("one.txt", "ev_one", "The gate code is 4821."),
                                ("two.txt", "ev_two", "The gate code is 4821.")):
            con.execute("INSERT INTO files(folder_id,path,sha256,kind,size,mtime,status,cloud_consent)"
                        " VALUES(?,?,?,?,?,?,?,?)",
                        (folder_id, str(tmp_path / name), "1" * 64, "text", 10, 0.0,
                         "indexed", 0))
            file_id = con.execute("SELECT id FROM files WHERE path=?",
                                  (str(tmp_path / name),)).fetchone()["id"]
            con.execute("INSERT INTO units(file_id,evidence_id,kind,locator,text) VALUES(?,?,?,?,?)",
                        (file_id, eid, "text_span", '{"type":"text_span"}', text))
    return database, folder_id


def test_answer_review_local_success_uses_fake_server(ollama, tmp_path, monkeypatch):
    import yaad.llm.meta_client as meta_client
    from yaad.query.answer import answer_question

    def bomb(settings):
        raise AssertionError("cloud client must never be constructed in local mode")

    monkeypatch.setattr(meta_client, "MetaClient", bomb)
    server, handler = ollama
    handler.responder = lambda body: chat_reply(json.dumps(
        {"status": "answered", "answer": "The gate code is 4821.",
         "claims": [{"claim": "The gate code is 4821.", "evidence_ids": ["ev_one"]}]}))
    database, folder_id = seed_answers(tmp_path)
    settings = local_settings(tmp_path, f"http://127.0.0.1:{server.server_port}")

    result = answer_question(database, "What is the gate code?", folder_id, settings)

    assert result["status"] == "answered"
    assert result["evidence"][0]["id"] == "ev_one"
    with database.connect() as con:
        row = con.execute("SELECT provider,estimated_cost_usd FROM egress_log").fetchone()
    assert row["provider"] == "local" and row["estimated_cost_usd"] == 0.0


def test_answer_review_local_failure_is_local_unavailable_without_cloud(tmp_path, monkeypatch):
    import socket
    import yaad.llm.meta_client as meta_client
    from yaad.query.answer import answer_question

    def bomb(settings):
        raise AssertionError("cloud client must never be constructed in local mode")

    monkeypatch.setattr(meta_client, "MetaClient", bomb)
    probe = socket.socket()
    try:
        probe.bind(("127.0.0.1", 0))
    except PermissionError:
        probe.close()
        pytest.skip("sandbox forbids binding a loopback probe socket")
    dead = f"http://127.0.0.1:{probe.getsockname()[1]}"
    probe.close()
    database, folder_id = seed_answers(tmp_path)
    settings = local_settings(tmp_path, dead)

    result = answer_question(database, "What is the gate code?", folder_id, settings)

    assert result["status"] == "local_unavailable"
    assert "muse-glimmer:30b-test" in result["answer"]
    assert len(result["evidence"]) == 2


def test_worker_image_local_and_audio_clear_failure(ollama, tmp_path, monkeypatch):
    import yaad.llm.meta_client as meta_client
    from PIL import Image
    from yaad.scan.worker import Indexer

    def bomb(settings):
        raise AssertionError("cloud client must never be constructed in local mode")

    monkeypatch.setattr(meta_client, "MetaClient", bomb)
    server, handler = ollama
    handler.responder = lambda body: chat_reply(json.dumps(
        {"caption": "a test chart", "visible_text": "Q3 revenue up"}))
    folder = tmp_path / "media"
    folder.mkdir()
    Image.new("RGB", (8, 8)).save(folder / "chart.png")
    with wave.open(str(folder / "note.wav"), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\x00" * 32000)
    database = Database(tmp_path / "media.sqlite3")
    settings = local_settings(tmp_path, f"http://127.0.0.1:{server.server_port}")
    indexer = Indexer(database, settings)

    folder_id = indexer.add_folder(str(folder), False)
    indexer.index_folder(folder_id)

    with database.connect() as con:
        rows = {row["path"].split("/")[-1]: dict(row)
                for row in con.execute("SELECT path,status,error FROM files")}
        photo = con.execute("SELECT caption,model FROM photo_descriptions").fetchone()
        providers = {row["provider"] for row in con.execute("SELECT DISTINCT provider FROM egress_log")}
    assert rows["chart.png"]["status"] == "indexed"
    assert photo["caption"] == "a test chart" and photo["model"] == "muse-glimmer:30b-test"
    assert rows["note.wav"]["status"] == "partial"
    assert "local model" in rows["note.wav"]["error"]
    assert providers == {"local"}


def seed_record(database, folder_id, **over):
    from yaad.memory.records import upsert_records

    candidate = {"record_type": "deadline", "title": "Draft due",
                 "detail": "Draft due March 3rd.", "status": "open",
                 "statement_kind": "explicit", "key_phrase": "draft march",
                 "date_value": "2026-03-03", "date_raw": "March 3rd",
                 "evidence": [{"evidence_id": "ev_a1", "role": "supports",
                               "excerpt": "Deliver the draft by March 3rd"}]}
    candidate.update(over)
    with database.transaction() as con:
        upsert_records(con, folder_id, "run-seed", [candidate],
                       {"ev_a1": "Deliver the draft by March 3rd."})
    with database.connect() as con:
        return con.execute("SELECT id FROM memory_records").fetchone()["id"]


def bomb_cloud(monkeypatch):
    import yaad.llm.meta_client as meta_client

    def bomb(settings):
        raise AssertionError("cloud client must never be constructed")

    monkeypatch.setattr(meta_client, "MetaClient", bomb)


def test_local_extraction_then_serving_without_consent(ollama, tmp_path, monkeypatch):
    from yaad.memory.extract import run_extraction
    from yaad.query.answer import answer_question

    bomb_cloud(monkeypatch)
    server, handler = ollama
    good = {"record_type": "deadline", "title": "Draft due", "detail": "Draft due March 3rd.",
            "status": "open", "statement_kind": "explicit", "key_phrase": "draft march",
            "date_value": "2026-03-03", "date_raw": "March 3rd",
            "evidence": [{"evidence_id": "ev_a1", "role": "supports",
                          "excerpt": "Deliver the draft by March 3rd"}]}
    handler.responder = lambda body: chat_reply(json.dumps({"records": [good]}))
    database, folder_id = seed_store(tmp_path, consent=0)
    settings = local_settings(tmp_path, f"http://127.0.0.1:{server.server_port}")

    from yaad.llm import get_client
    summary = run_extraction(database, folder_id, "run-local", settings, get_client(settings))
    assert summary["accepted"] == 1

    result = answer_question(database, "When is the draft due?", folder_id, settings)
    assert result["memory"]["used"] is True
    assert result["memory"]["record_ids"] == [1]
    assert "March 3rd" in result["memory"]["claims"][0]["statement"]
    assert "ev_a1" in [card["id"] for card in result["evidence"]]
    assert len(handler.requests) == 1  # extraction only; exact lookup needs no review


def test_cloud_path_stays_blocked_without_consent(tmp_path, monkeypatch):
    from yaad.query.answer import answer_question

    bomb_cloud(monkeypatch)
    database, folder_id = seed_store(tmp_path, consent=0)
    seed_record(database, folder_id)
    settings = Settings(db_path=tmp_path / "c.sqlite3", cloud_enabled=True,
                        model_api_key="stub-key", model_provider="cloud")

    result = answer_question(database, "When is the draft due?", folder_id, settings)
    assert result["memory"]["used"] is False
    assert result["memory"]["record_ids"] == []


def test_consent_revocation_blocks_cloud_but_not_local(tmp_path, monkeypatch):
    from yaad.memory.extract import run_extraction
    from yaad.query.answer import answer_question

    bomb_cloud(monkeypatch)
    database, folder_id = seed_store(tmp_path, consent=1)
    seed_record(database, folder_id)
    cloud = Settings(db_path=tmp_path / "c.sqlite3", cloud_enabled=True,
                     model_api_key="stub-key", model_provider="cloud")
    assert answer_question(database, "When is the draft due?", folder_id, cloud)["memory"]["used"] is True

    with database.transaction() as con:
        con.execute("UPDATE folders SET cloud_consent=0 WHERE id=?", (folder_id,))
        con.execute("UPDATE files SET cloud_consent=0 WHERE folder_id=?", (folder_id,))
    assert answer_question(database, "When is the draft due?", folder_id, cloud)["memory"]["used"] is False
    local = local_settings(tmp_path, "http://127.0.0.1:9")
    assert answer_question(database, "When is the draft due?", folder_id, local)["memory"]["used"] is True
    with pytest.raises(RuntimeError, match="no consented evidence units"):
        run_extraction(database, folder_id, "run-revoked", cloud, object())


def test_local_record_uses_live_consent_after_provider_switch(tmp_path):
    from yaad.query.answer import answer_question

    database, folder_id = seed_store(tmp_path, consent=0)
    seed_record(database, folder_id)
    cloud = Settings(db_path=tmp_path / "c.sqlite3", cloud_enabled=True,
                     model_api_key="stub-key", model_provider="cloud")
    local = local_settings(tmp_path, "http://127.0.0.1:9")
    assert answer_question(database, "When is the draft due?", folder_id, local)["memory"]["used"] is True
    assert answer_question(database, "When is the draft due?", folder_id, cloud)["memory"]["used"] is False
    with database.transaction() as con:
        con.execute("UPDATE folders SET cloud_consent=1 WHERE id=?", (folder_id,))
        con.execute("UPDATE files SET cloud_consent=1 WHERE folder_id=?", (folder_id,))
    assert answer_question(database, "When is the draft due?", folder_id, cloud)["memory"]["used"] is True


def test_status_listing_local_without_consent(tmp_path):
    from yaad.query.answer import answer_question

    database, folder_id = seed_store(tmp_path, consent=0)
    seed_record(database, folder_id, statement_kind="proposal")
    local = local_settings(tmp_path, "http://127.0.0.1:9")
    cloud = Settings(db_path=tmp_path / "c.sqlite3", cloud_enabled=True,
                     model_api_key="stub-key", model_provider="cloud")
    result = answer_question(database, "list proposal memories", folder_id, local)
    assert "Draft due March 3rd" in result["answer"]
    assert result["memory"]["used"] is True
    denied = answer_question(database, "list proposal memories", folder_id, cloud)
    assert denied["memory"]["used"] is False


def test_list_memory_verdicts_local_without_consent(tmp_path, monkeypatch):
    import yaad.api.main as main

    database, folder_id = seed_store(tmp_path, consent=0)
    seed_record(database, folder_id)
    monkeypatch.setattr(main, "db", database)
    monkeypatch.setattr(main, "settings", local_settings(tmp_path, "http://127.0.0.1:9"))
    payload = main.list_memory(folder_id=folder_id)
    assert payload["records"][0]["serving"] == {
        "verdict": "eligible", "reason": "grounded explicit record"}
    assert payload["records"][0]["authorized_supports"] == 1
    monkeypatch.setattr(main, "settings", Settings(
        db_path=tmp_path / "c.sqlite3", cloud_enabled=True,
        model_api_key="stub-key", model_provider="cloud"))
    payload = main.list_memory(folder_id=folder_id)
    assert payload["records"][0]["serving"] == {
        "verdict": "excluded", "reason": "current consent does not authorize these sources"}


def test_authorized_supports_honors_consent_flag(tmp_path):
    from yaad.query.memory_answer import authorized_supports

    database, folder_id = seed_store(tmp_path, consent=0)
    record_id = seed_record(database, folder_id)
    with database.connect() as con:
        assert authorized_supports(con, record_id) == []
        supports = authorized_supports(con, record_id, require_consent=False)
        assert [item["evidence_id"] for item in supports] == ["ev_a1"]


def test_local_image_marks_local_origin_and_empty_result_clearly(ollama, tmp_path, monkeypatch):
    from PIL import Image
    from yaad.scan.worker import Indexer

    bomb_cloud(monkeypatch)
    server, handler = ollama
    folder = tmp_path / "img"
    folder.mkdir()
    Image.new("RGB", (8, 8)).save(folder / "pic.png")
    database = Database(tmp_path / "img.sqlite3")
    settings = local_settings(tmp_path, f"http://127.0.0.1:{server.server_port}")
    indexer = Indexer(database, settings)
    folder_id = indexer.add_folder(str(folder), False)

    handler.responder = lambda body: chat_reply(json.dumps({"caption": "a test view"}))
    indexer.index_folder(folder_id)
    with database.connect() as con:
        meta = json.loads(con.execute("SELECT meta FROM units").fetchone()["meta"])
        assert meta["origin"] == "local_model"
        assert con.execute("SELECT status FROM files").fetchone()["status"] == "indexed"

    (folder / "pic.png").write_bytes(b"\x89PNG-changed")
    handler.responder = lambda body: chat_reply(json.dumps({}))
    indexer.index_folder(folder_id)
    with database.connect() as con:
        error = con.execute("SELECT status,error FROM files").fetchone()
        assert error["status"] == "partial"
        assert error["error"].startswith("Local model returned no searchable")


def test_redescribe_runs_locally_without_consent_or_cloud(ollama, tmp_path, monkeypatch):
    from PIL import Image
    from yaad.scan.worker import Indexer

    bomb_cloud(monkeypatch)
    server, handler = ollama
    handler.responder = lambda body: chat_reply(json.dumps({"caption": "fresh local caption"}))
    folder = tmp_path / "redescribe"
    folder.mkdir()
    Image.new("RGB", (8, 8)).save(folder / "shot.png")
    database = Database(tmp_path / "redescribe.sqlite3")
    settings = local_settings(tmp_path, f"http://127.0.0.1:{server.server_port}")
    indexer = Indexer(database, settings)
    folder_id = indexer.add_folder(str(folder), False)
    indexer.index_folder(folder_id)
    with database.connect() as con:
        file_id = con.execute("SELECT id FROM files").fetchone()["id"]

    result = indexer.redescribe_image(file_id)
    assert result["status"] == "complete"
    with database.connect() as con:
        assert con.execute("SELECT caption FROM photo_descriptions WHERE file_id=?",
                           (result["file_id"],)).fetchone()["caption"] == "fresh local caption"
        providers = {row["provider"] for row in con.execute("SELECT DISTINCT provider FROM egress_log")}
    assert providers == {"local"}


def test_local_provider_never_builds_cloud_client_even_when_cloud_allowed(tmp_path, monkeypatch):
    from yaad.llm import get_client
    from yaad.llm.local_client import LocalClient
    from yaad.scan.worker import Indexer

    bomb_cloud(monkeypatch)
    settings = local_settings(tmp_path, "http://127.0.0.1:9",
                              cloud_enabled=True, model_api_key="synthetic")
    assert isinstance(get_client(settings), LocalClient)
    assert isinstance(Indexer(Database(tmp_path / "gate.sqlite3"), settings)._meta(), LocalClient)


def test_cloud_mode_off_consent_errors_name_the_local_alternative(tmp_path):
    import wave
    from PIL import Image
    from yaad.scan.worker import Indexer

    folder = tmp_path / "gated"
    folder.mkdir()
    Image.new("RGB", (8, 8)).save(folder / "pic.png")
    with wave.open(str(folder / "note.wav"), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\x00" * 3200)
    database = Database(tmp_path / "gated.sqlite3")
    settings = Settings(db_path=tmp_path / "gated.sqlite3", cloud_enabled=False)
    indexer = Indexer(database, settings)
    folder_id = indexer.add_folder(str(folder), False)
    indexer.index_folder(folder_id)
    with database.connect() as con:
        rows = {row["path"].split("/")[-1]: dict(row)
                for row in con.execute("SELECT path,status,error FROM files")}
    assert rows["pic.png"]["status"] == "partial"
    assert "YAAD_MODEL_PROVIDER=local" in rows["pic.png"]["error"]
    assert "no audio input" in rows["note.wav"]["error"]

import os
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

os.environ.setdefault("YAAD_DB_PATH", str(Path(tempfile.gettempdir()) / "yaad-api-refinement-tests.sqlite3"))

from yaad.api import main
from yaad.config import Settings
from yaad.db import Database
from yaad.scan.worker import Indexer


def test_status_table_counts_use_real_file_states(tmp_path, monkeypatch):
    db = Database(tmp_path / "status.sqlite3")
    folder_id = Indexer(db, Settings(db_path=tmp_path / "status.sqlite3")).add_folder(str(tmp_path), False)
    with db.transaction() as con:
        for name, status in (("ok.txt", "indexed"), ("scan.pdf", "partial"), ("bad.docx", "failed")):
            con.execute("INSERT INTO files(folder_id,path,sha256,kind,size,mtime,status) VALUES(?,?,?,?,?,?,?)",
                        (folder_id, str(tmp_path / name), "hash", "text", 1, 0, status))
    monkeypatch.setattr(main, "db", db)
    monkeypatch.setattr(main, "settings", SimpleNamespace(cloud_enabled=False, model_api_key=""))
    result = main.index_status()
    folder = result["folders"][0]
    assert (folder["files"], folder["indexed"], folder["partial"], folder["failed"], folder["status"]) == (3, 1, 1, 1, "partial")
    assert {file["path"].split("/")[-1] for file in result["files"]} == {"ok.txt", "scan.pdf", "bad.docx"}


def test_privacy_and_egress_pagination_are_independent_and_bounded(tmp_path, monkeypatch):
    db = Database(tmp_path / "privacy.sqlite3")
    with db.transaction() as con:
        for n in range(23):
            con.execute("INSERT INTO egress_log(purpose,provider,model,bytes,content_kind,success) VALUES(?,?,?,?,?,?)",
                        (f"operation {n}", "Meta", "model", 4, "synthetic", 1))
    monkeypatch.setattr(main, "db", db)
    monkeypatch.setattr(main, "settings", SimpleNamespace(cloud_enabled=True, model_api_key="test-secret", checked_model=lambda: "model"))
    privacy = main.privacy_status()
    assert privacy["api_key_configured"] is True
    assert "test-secret" not in str(privacy)
    assert privacy["recorded_requests"] == 23
    page = main.egress(limit=10, offset=10)
    assert (page["total"], page["offset"], len(page["items"])) == (23, 10, 10)
    assert page["items"][0]["purpose"] == "operation 12"


def test_redescription_refuses_unconsented_folder_without_provider_call(tmp_path, monkeypatch):
    db = Database(tmp_path / "consent.sqlite3")
    folder = tmp_path / "photos"
    folder.mkdir()
    image = folder / "sample.png"
    image.write_bytes(b"synthetic image placeholder")
    settings = Settings(db_path=tmp_path / "consent.sqlite3", cloud_enabled=True, model_api_key="synthetic")
    indexer = Indexer(db, settings)
    folder_id = indexer.add_folder(str(folder), False)
    with db.transaction() as con:
        file_id = con.execute("INSERT INTO files(folder_id,path,sha256,kind,size,mtime,status) VALUES(?,?,?,?,?,?,?)",
                              (folder_id, str(image), "hash", "image", image.stat().st_size, 0, "partial")).lastrowid
    indexer._meta = lambda: pytest.fail("Unconsented photo must never call Meta")
    monkeypatch.setattr(main, "db", db)
    monkeypatch.setattr(main, "indexer", indexer)
    with pytest.raises(HTTPException) as error:
        main.redescribe_photo(file_id)
    assert error.value.status_code == 409
    with db.connect() as con:
        assert con.execute("SELECT COUNT(*) FROM egress_log").fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0


def test_status_and_privacy_report_active_provider_and_models(tmp_path, monkeypatch):
    db = Database(tmp_path / "provider.sqlite3")
    monkeypatch.setattr(main, "db", db)
    settings = Settings(db_path=tmp_path / "provider.sqlite3", model_provider="local",
                        local_model="muse-test:1b", local_endpoint="http://127.0.0.1:11434")
    monkeypatch.setattr(main, "settings", settings)
    status = main.index_status()
    assert (status["model_provider"], status["local_model"]) == ("local", "muse-test:1b")
    assert status["local_endpoint"] == "http://127.0.0.1:11434"
    privacy = main.privacy_status()
    assert (privacy["model_provider"], privacy["local_model"]) == ("local", "muse-test:1b")

    monkeypatch.setattr(main, "settings", SimpleNamespace(cloud_enabled=False, model_api_key="",
                                                          checked_model=lambda: "model"))
    legacy = main.privacy_status()
    assert (legacy["model_provider"], legacy["local_model"], legacy["local_endpoint"]) == ("cloud", "", "")

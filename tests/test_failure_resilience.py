"""Ingest and review failure resilience (offline, stubbed models).

Corrupt files must fail per-file without aborting the folder index,
and a dead review model must never wipe the local answer. No network.
"""
import sys

sys.path.insert(0, "tests")

from test_cross_file_fixes import StubMeta, make_db

import yaad.llm.meta_client as meta_client
from yaad.llm.meta_api import MetaAPIError
from yaad.query.answer import answer_question


def test_corrupt_files_fail_per_file_without_aborting_index(tmp_path):
    db, indexer, folder, folder_id = make_db(tmp_path)
    (folder / "good.txt").write_text("Council booked Riverside Hall for Spring Fest.")
    (folder / "broken.pdf").write_bytes(b"%PDF-1.4 garbage \xff\xfe not a pdf" * 20)
    (folder / "truncated.png").write_bytes(b"\x89PNG\r\n\x1a\nTRUNCATED")
    indexer._meta = lambda: StubMeta()
    summary = indexer.index_folder(folder_id)
    assert summary["failed"] >= 1
    assert summary["indexed"] >= 1
    with db.connect() as con:
        rows = {row["path"]: (row["status"], row["error"])
                for row in con.execute("SELECT path,status,error FROM files")}
    assert rows[str(folder / "good.txt")][0] == "indexed"
    for name in ("broken.pdf", "truncated.png"):
        status, error = rows[str(folder / name)]
        assert status in ("failed", "partial", "indexed")
        if status != "indexed":
            assert error, f"{name} must record why it failed"
    result = answer_question(db, "What is the Spring Fest venue?", folder_id,
                             indexer.settings)
    assert result["status"] in ("answered", "unverified", "cloud_unavailable")


def test_dead_review_model_preserves_local_answer(tmp_path, monkeypatch):
    db, indexer, folder, folder_id = make_db(tmp_path)
    (folder / "notes.txt").write_text("Council booked Riverside Hall for Spring Fest. "
                                      "The venue holds five hundred guests.")
    indexer._meta = lambda: StubMeta()
    indexer.index_folder(folder_id)

    class _DeadReview:
        # Real transport failures still record last_call; the stub
        # mirrors that so the receipt path is genuinely exercised.
        last_call = {"provider": "Meta Model API", "model": "muse-spark-1.3",
                     "status_code": 503, "success": False, "duration_ms": 5,
                     "input_tokens": None, "output_tokens": None,
                     "estimated_cost_usd": 0.0, "error_code": "http_503"}

        def __init__(self, _settings):
            pass

        def reason_json(self, messages, schema, prompt_id):
            raise MetaAPIError(503, {}, 5, {})

    monkeypatch.setattr(meta_client, "MetaClient", _DeadReview)
    result = answer_question(db, "What is the Spring Fest venue?", folder_id,
                             indexer.settings)
    assert result["status"] != "error"
    assert result["evidence"], "local evidence must survive review outage"
    with db.connect() as con:
        receipt = con.execute("SELECT success,error_code FROM egress_log "
                              "WHERE purpose='semantic answer review' "
                              "ORDER BY id DESC LIMIT 1").fetchone()
    assert receipt is not None and not receipt["success"]


def test_fts_rebuild_restores_search_after_corruption(tmp_path):
    db, indexer, folder, folder_id = make_db(tmp_path)
    (folder / "notes.txt").write_text("Council booked Riverside Hall for Spring Fest.")
    indexer._meta = lambda: StubMeta()
    indexer.index_folder(folder_id)
    with db.connect() as con:
        before = con.execute("SELECT count(*) FROM units_fts WHERE units_fts MATCH 'riverside'").fetchone()[0]
        con.execute("DELETE FROM units_fts")
        wiped = con.execute("SELECT count(*) FROM units_fts WHERE units_fts MATCH 'riverside'").fetchone()[0]
    assert before == 1 and wiped == 0
    db.rebuild_fts()
    with db.connect() as con:
        after = con.execute("SELECT count(*) FROM units_fts WHERE units_fts MATCH 'riverside'").fetchone()[0]
    assert after == 1


def test_consent_revocation_blocks_review_but_keeps_local_answer(tmp_path, monkeypatch):
    from test_cross_file_fixes import CaptureReview
    db, indexer, folder, folder_id = make_db(tmp_path)
    (folder / "notes.txt").write_text("Council booked Riverside Hall for Spring Fest.")
    indexer._meta = lambda: StubMeta()
    indexer.index_folder(folder_id)
    with db.connect() as con:
        con.execute("UPDATE folders SET cloud_consent=0 WHERE id=?", (folder_id,))
        con.execute("UPDATE files SET cloud_consent=0 WHERE folder_id=?", (folder_id,))
    CaptureReview.seen = None
    monkeypatch.setattr(meta_client, "MetaClient", CaptureReview)
    result = answer_question(db, "What is the Spring Fest venue?", folder_id,
                             indexer.settings)
    assert CaptureReview.seen is None, "revoked consent must reach no model"
    assert result["status"] in ("answered", "unverified", "cloud_unavailable")
    assert result["evidence"], "local evidence stays readable after revocation"

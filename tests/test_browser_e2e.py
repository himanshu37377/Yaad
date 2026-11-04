"""Browser end-to-end tests: drive the real UI against a seeded server.

Skips cleanly when the sandbox forbids loopback servers or browser
launch (same convention as the loopback skips elsewhere); every
assertion below was grounded in verified API behavior and DOM ids,
so the suite is meaningful wherever browsers can run (CI, dev box).
"""
import socket
import threading
import time

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright not installed")
pytest.importorskip("uvicorn", reason="uvicorn not installed")

from playwright.sync_api import sync_playwright  # noqa: E402

from yaad.api import main  # noqa: E402
from yaad.config import Settings  # noqa: E402
from yaad.db import Database  # noqa: E402
from yaad.memory.records import mark_conflicts, upsert_records  # noqa: E402
from yaad.scan.worker import Indexer  # noqa: E402


def _free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("e2e")
    docs = tmp / "docs"
    docs.mkdir()
    (docs / "fee.txt").write_text("The workshop fee is Rs. 150 per person.")
    (docs / "venue.txt").write_text("The venue holds two hundred guests.")
    (docs / "catering.txt").write_text("Catering covers two hundred meals.")
    (docs / "fee_update.txt").write_text("The workshop fee is Rs. 200 per person.")
    settings = Settings(db_path=tmp / "e2e.sqlite3", cloud_enabled=False)
    db = Database(tmp / "e2e.sqlite3")
    indexer = Indexer(db, settings)
    folder_id = indexer.add_folder(str(docs), False)
    indexer.index_folder(folder_id)
    # Deterministic conflicted pair (no model): same matter, rival values.
    with db.transaction() as con:
        units = {row["evidence_id"]: row["text"] for row in con.execute(
            "SELECT evidence_id,text FROM units u JOIN files f ON f.id=u.file_id "
            "WHERE f.folder_id=?", (folder_id,))}
    fee_old = next(eid for eid, text in units.items() if "Rs. 150" in text)
    fee_new = next(eid for eid, text in units.items() if "Rs. 200" in text)
    with db.transaction() as con:
        upsert_records(con, folder_id, "e2e-seed", [
            {"record_type": "decision", "title": "Workshop fee",
             "detail": "Workshop fee Rs. 150", "date_value": None, "date_raw": None,
             "assignee": "", "status": "decided", "statement_kind": "explicit",
             "key_phrase": "workshop fee",
             "evidence": [{"evidence_id": fee_old, "role": "supports",
                           "excerpt": units[fee_old]}]},
            {"record_type": "decision", "title": "Workshop fee",
             "detail": "Workshop fee Rs. 200", "date_value": None, "date_raw": None,
             "assignee": "", "status": "decided", "statement_kind": "explicit",
             "key_phrase": "workshop fee",
             "evidence": [{"evidence_id": fee_new, "role": "supports",
                           "excerpt": units[fee_new]}]},
        ], units)
        mark_conflicts(con, folder_id, source="e2e-seed")
    old_db, old_settings, old_indexer = main.db, main.settings, main.indexer
    main.db, main.settings, main.indexer = db, settings, indexer
    try:
        port = _free_port()
    except OSError:
        main.db, main.settings, main.indexer = old_db, old_settings, old_indexer
        pytest.skip("sandbox forbids loopback servers")
    import uvicorn
    server = uvicorn.Server(uvicorn.Config(main.app, host="127.0.0.1", port=port,
                                           log_level="error"))
    thread = threading.Thread(target=server.serve, daemon=True)
    thread.start()
    deadline = time.time() + 15
    import urllib.request
    while time.time() < deadline:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/api/index/status", timeout=2)
            break
        except OSError:
            time.sleep(0.2)
    else:
        server.should_exit = True
        main.db, main.settings, main.indexer = old_db, old_settings, old_indexer
        pytest.skip("test server did not start")
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=10)
    main.db, main.settings, main.indexer = old_db, old_settings, old_indexer


@pytest.fixture(scope="module")
def page(server):
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(args=["--no-sandbox"])
        except Exception as exc:
            pytest.skip(f"browser unavailable: {type(exc).__name__}")
        context = browser.new_context()
        tab = context.new_page()
        tab.set_default_timeout(15000)
        yield tab
        context.close()
        browser.close()


def ask(page, question: str):
    page.fill("#question", question)
    page.click("#send-button")
    page.wait_for_selector(".assistant-message:last-child .assistant-answer")


def test_ask_conflict_surfaces_both_values_and_sources(server, page):
    """Seeded Rs. 150 vs Rs. 200: the conflict contract, asserted as
    the system actually behaves -- a conflict verdict surfacing both
    values and both sources, never one quietly picked winner. (This
    test previously expected a clean 'Answered', which the seeded
    conflict could never produce.)"""
    page.goto(server + "/")
    ask(page, "What is the workshop fee?")
    answer = page.locator(".assistant-message:last-child")
    assert answer.locator(".answer-badge.conflict").inner_text() == "Conflicting evidence"
    text = answer.locator(".assistant-answer").inner_text()
    assert "150" in text and "200" in text
    filenames = {chip.locator(".source-filename").inner_text()
                 for chip in answer.locator(".source-chip").all()}
    assert {"fee.txt", "fee_update.txt"} <= filenames


def test_missing_information_abstains_visibly(server, page):
    page.goto(server + "/")
    ask(page, "What is the Wi-Fi password?")
    answer = page.locator(".assistant-message:last-child")
    assert answer.locator(".answer-badge.not_found").inner_text() == "Not found in this scope"


def test_cloud_review_unavailable_without_cloud(server, page):
    page.goto(server + "/")
    ask(page, "What covers two hundred?")
    answer = page.locator(".assistant-message:last-child")
    badge = answer.locator(".answer-badge.cloud_unavailable")
    assert badge.inner_text() == "Cloud unavailable"
    assert answer.locator(".source-chip").count() >= 1


def test_memory_conflict_visible_and_inspectable(server, page):
    page.goto(server + "/")
    page.click('[data-view="memory"]')
    page.wait_for_selector("#memory-list tr")
    row = page.locator("#memory-list tr", has_text="Workshop fee").first
    assert "Conflicted" in row.locator(".status-badge.failed").inner_text()
    row.locator(".memory-inspect").click()
    dialog = page.locator("#memory-dialog")
    assert dialog.locator(".memory-excerpt").count() >= 1
    assert "Rs. 150" in dialog.inner_text() or "Rs. 200" in dialog.inner_text()


def test_manual_extraction_requires_consent(server, page):
    page.goto(server + "/")
    page.click('[data-view="memory"]')
    page.wait_for_function("document.querySelectorAll('#memory-folder option').length > 1")
    page.select_option("#memory-folder", index=1)
    page.click("#memory-extract")
    page.wait_for_selector("#memory-extract-result:not(:empty)")
    assert "consent" in page.locator("#memory-extract-result").inner_text().lower()

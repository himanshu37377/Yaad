import json
import subprocess
import threading
import time
import uuid
from pathlib import Path
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from yaad.config import get_settings
from yaad.db import Database
from yaad.schemas import FolderRequest, FolderPickerRequest, FolderConsentRequest, IndexRequest, AskRequest, MemoryExtractRequest, MemoryFlagRequest
from yaad.llm import get_client, is_local
from yaad.memory.extract import run_extraction
from yaad.memory.ledger import list_transitions
from yaad.memory.records import answer_eligibility, resolving_supports
from yaad.query.memory_answer import authorized_supports
from yaad.scan.worker import Indexer
from yaad.query.answer import answer_question
from yaad.extract.pdf import render_page
from yaad.query.photos import list_photos
import mimetypes

settings = get_settings()
db = Database(settings.db_path)
indexer = Indexer(db, settings)
app = FastAPI(title="Yaad", description="Evidence-backed personal memory engine")
UI = Path(__file__).parents[1] / "ui"
app.mount("/static", StaticFiles(directory=UI), name="static")

@app.get("/", response_class=HTMLResponse)
def home():
    try:
        return (UI / "index.html").read_text()
    except FileNotFoundError as exc:
        raise HTTPException(500, "The Yaad interface files are missing. Reinstall the app.") from exc

@app.post("/api/folders")
def add_folder(request: FolderRequest):
    try:
        folder_id = indexer.add_folder(request.path, request.cloud_consent)
    except (ValueError, OSError) as exc:
        raise HTTPException(400, str(exc))
    return {"folder_id": folder_id, "path": str(Path(request.path).expanduser().resolve()), "cloud_consent": request.cloud_consent}

@app.post("/api/folders/pick")
def pick_folder(request: FolderPickerRequest):
    """Open the local macOS folder chooser, then add the chosen folder."""
    script = 'POSIX path of (choose folder with prompt "Choose a folder for Yaad")'
    try:
        result = subprocess.run(["/usr/bin/osascript", "-e", script], capture_output=True, text=True, timeout=180)
    except FileNotFoundError as exc:
        raise HTTPException(501, "The native folder chooser is available in the macOS desktop app.") from exc
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(408, "The folder chooser timed out. Please try again.") from exc
    except OSError as exc:
        raise HTTPException(500, "Could not open the folder chooser. Please try again.") from exc
    if result.returncode:
        if "User canceled" in result.stderr:
            return {"cancelled": True}
        raise HTTPException(500, "Could not open the folder chooser. Please try again.")
    path = result.stdout.strip()
    selected = Path(path).expanduser().resolve()
    try:
        with db.connect() as con:
            existing = con.execute("SELECT id,cloud_consent FROM folders WHERE path=?", (str(selected),)).fetchone()
        already_added = existing is not None
        if existing:
            # Reopening an existing folder must not silently revoke its stored consent.
            folder_id = existing["id"]
            cloud_consent = bool(existing["cloud_consent"])
        else:
            folder_id = indexer.add_folder(str(selected), request.cloud_consent)
            cloud_consent = request.cloud_consent
    except (ValueError, OSError) as exc:
        raise HTTPException(400, "The selected folder could not be added.") from exc
    return {"cancelled": False, "already_added": already_added, "folder_id": folder_id, "path": str(selected), "cloud_consent": cloud_consent}

@app.post("/api/folders/{folder_id}/consent")
def update_folder_consent(folder_id: int, request: FolderConsentRequest):
    with db.transaction() as con:
        cursor = con.execute("UPDATE folders SET cloud_consent=? WHERE id=?", (int(request.cloud_consent), folder_id))
        if cursor.rowcount == 0:
            raise HTTPException(404, "Folder not found")
        # Record which consent setting was in force at the last file indexing pass.
        con.execute("UPDATE files SET error=CASE WHEN status IN ('partial','failed') THEN 'Folder consent changed; reindex this folder to retry eligible processing.' ELSE error END WHERE folder_id=?", (folder_id,))
        folder = con.execute("SELECT id,path,cloud_consent FROM folders WHERE id=?", (folder_id,)).fetchone()
    return {"id": folder["id"], "path": folder["path"], "cloud_consent": bool(folder["cloud_consent"])}

_index_lock = threading.Lock()


@app.post("/api/index")
def run_index(request: IndexRequest):
    if not _index_lock.acquire(blocking=False):
        raise HTTPException(409, "Indexing is already running. Wait for it to finish, then retry.")
    try:
        return indexer.index_folder(request.folder_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    finally:
        _index_lock.release()

@app.get("/api/index/status")
def index_status(folder_id: int | None = None):
    sql = "SELECT f.id,f.folder_id,f.path,f.kind,f.status,f.error,f.partial,f.cloud_consent AS indexed_with_cloud_consent,fo.cloud_consent AS folder_cloud_consent,fo.path AS folder,(SELECT schema_version FROM photo_descriptions pd WHERE pd.file_id=f.id) AS photo_schema_version FROM files f JOIN folders fo ON fo.id=f.folder_id"
    args = []
    if folder_id is not None:
        sql += " WHERE f.folder_id=?"
        args.append(folder_id)
    sql += " ORDER BY f.path"
    with db.connect() as con:
        files = [dict(row) for row in con.execute(sql, args)]
        folders = [dict(row) for row in con.execute("""SELECT fo.id,fo.path,fo.cloud_consent,
                    COUNT(f.id) AS files,
                    SUM(CASE WHEN f.status='indexed' THEN 1 ELSE 0 END) AS indexed,
                    SUM(CASE WHEN f.status='partial' OR f.partial=1 THEN 1 ELSE 0 END) AS partial,
                    SUM(CASE WHEN f.status='failed' THEN 1 ELSE 0 END) AS failed,
                    SUM(CASE WHEN f.status='skipped' THEN 1 ELSE 0 END) AS skipped
                    FROM folders fo LEFT JOIN files f ON f.folder_id=fo.id
                    GROUP BY fo.id ORDER BY fo.path""")]
        run_sql = "SELECT r.id,r.folder_id,r.state,r.started_at,r.finished_at,f.path AS folder FROM index_runs r JOIN folders f ON f.id=r.folder_id"
        run_args = []
        if folder_id is not None:
            run_sql += " WHERE r.folder_id=?"
            run_args.append(folder_id)
        run_sql += " ORDER BY r.id DESC LIMIT 20"
        runs = [dict(row) for row in con.execute(run_sql, run_args)]
    running = {run["folder_id"] for run in runs if run["state"] == "running"}
    for folder in folders:
        folder["status"] = ("indexing" if folder["id"] in running else "failed" if folder["files"] and folder["failed"] == folder["files"] else
                            "partial" if folder["partial"] or folder["failed"] or folder["skipped"] else "ready" if folder["files"] else "empty")
    return {"folders": folders, "files": files, "runs": runs, "cloud_enabled": settings.cloud_enabled,
            "api_key_configured": bool(settings.model_api_key),
            "model_provider": getattr(settings, "model_provider", "cloud") or "cloud",
            "local_model": getattr(settings, "local_model", "") or "",
            "local_endpoint": str(getattr(settings, "local_endpoint", "") or "")}

@app.get("/api/privacy")
def privacy_status():
    with db.connect() as con:
        folders = [dict(row) for row in con.execute("SELECT id,path,cloud_consent FROM folders ORDER BY path")]
        egress_count = con.execute("SELECT COUNT(*) n FROM egress_log").fetchone()["n"]
    try:
        model, model_error = settings.checked_model(), None
    except ValueError as exc:
        model, model_error = settings.model, str(exc)
    return {"cloud_enabled": settings.cloud_enabled, "api_key_configured": bool(settings.model_api_key),
            "model": model, "model_error": model_error, "folders": folders, "recorded_requests": egress_count,
            "model_provider": getattr(settings, "model_provider", "cloud") or "cloud",
            "local_model": getattr(settings, "local_model", "") or "",
            "local_endpoint": str(getattr(settings, "local_endpoint", "") or "")}

@app.get("/api/photos")
def photos(folder_id: int | None = None):
    return list_photos(db, folder_id)

@app.post("/api/photos/{file_id}/redescribe")
def redescribe_photo(file_id: int):
    try:
        return indexer.redescribe_image(file_id)
    except ValueError as exc:
        raise HTTPException(404, "Image file not found") from exc
    except PermissionError as exc:
        raise HTTPException(409, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc

@app.get("/api/photos/{file_id}/preview")
def photo_preview(file_id: int):
    with db.connect() as con:
        row = con.execute("SELECT path FROM files WHERE id=? AND kind='image'", (file_id,)).fetchone()
    if not row or not Path(row["path"]).is_file():
        raise HTTPException(404, "Photo source is no longer available")
    content_type = mimetypes.guess_type(row["path"])[0] or "application/octet-stream"
    return FileResponse(row["path"], media_type=content_type)

@app.post("/api/ask")
def ask(request: AskRequest):
    timings: dict = {}
    start = time.perf_counter()
    result = answer_question(db, request.question, request.folder_id, settings, timings)
    timings["total_ms"] = round((time.perf_counter() - start) * 1000)
    result["timings"] = timings
    return result

@app.get("/api/evidence/{evidence_id}/preview")
def evidence_preview(evidence_id: str):
    with db.connect() as con:
        row = con.execute("SELECT u.*,f.path FROM units u JOIN files f ON f.id=u.file_id WHERE u.evidence_id=?", (evidence_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Evidence not found")
    try:
        locator = json.loads(row["locator"])
    except (TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(422, "The stored source reference is invalid.") from exc
    path = Path(row["path"])
    if locator.get("type") == "pdf_page" and path.exists():
        try:
            return Response(render_page(str(path), locator["page"]), media_type="image/png")
        except Exception as exc:
            raise HTTPException(422, "Could not render source page") from exc
    if locator.get("type") == "image" and path.exists():
        return FileResponse(path, media_type="image/" + (path.suffix.lower().lstrip(".") if path.suffix.lower() != ".jpg" else "jpeg"))
    if locator.get("type") == "audio_segment" and path.exists():
        audio_type = {".wav":"audio/wav",".mp3":"audio/mpeg",".m4a":"audio/mp4",".ogg":"audio/ogg",".opus":"audio/ogg"}.get(path.suffix.lower(), "application/octet-stream")
        return FileResponse(path, media_type=audio_type)
    return {"file": str(path), "locator": locator, "quote": row["text"]}

@app.get("/api/egress")
def egress(limit: int = Query(default=20, ge=1, le=100), offset: int = Query(default=0, ge=0)):
    with db.connect() as con:
        total = con.execute("SELECT COUNT(*) n FROM egress_log").fetchone()["n"]
        items = [dict(row) for row in con.execute("""SELECT e.*,
                    COALESCE(f.path,CASE WHEN e.purpose='semantic answer review' THEN 'Retrieved excerpts from consented sources' ELSE 'Source file unavailable' END) AS source_file
                    FROM egress_log e LEFT JOIN files f ON f.id=e.file_id ORDER BY e.id DESC LIMIT ? OFFSET ?""", (limit, offset))]
    return {"items": items, "total": total, "limit": limit, "offset": offset}

@app.post("/api/feedback")
def feedback(payload: dict):
    # Feedback is local and minimal; no user text is forwarded to a model.
    with db.transaction() as con:
        con.execute("CREATE TABLE IF NOT EXISTS feedback(id INTEGER PRIMARY KEY, evidence_id TEXT, helpful INTEGER, created_at TEXT DEFAULT CURRENT_TIMESTAMP)")
        con.execute("INSERT INTO feedback(evidence_id,helpful) VALUES(?,?)", (payload.get("evidence_id"), int(bool(payload.get("helpful")))))
    return {"ok": True}

_memory_lock = threading.Lock()

@app.post("/api/memory/extract")
def extract_memory(request: MemoryExtractRequest):
    """Run one extraction pass over a folder's evidence.

    Explicit: never triggered by indexing. Cloud runs use consented evidence
    and are paid; local runs use all folder evidence and are free. Rejected
    candidates are reported in the response, never silently dropped."""
    if not _memory_lock.acquire(blocking=False):
        raise HTTPException(409, "Memory extraction is already running. Wait for it to finish, then retry.")
    try:
        with db.connect() as con:
            folder = con.execute("SELECT id FROM folders WHERE id=?", (request.folder_id,)).fetchone()
        if not folder:
            raise HTTPException(404, "Folder not found")
        run_id = f"api-{uuid.uuid4().hex[:12]}"
        try:
            summary = run_extraction(db, request.folder_id, run_id, settings, get_client(settings))
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"run_id": run_id, "folder_id": request.folder_id, **summary}
    finally:
        _memory_lock.release()

@app.get("/api/memory")
def list_memory(folder_id: int | None = None):
    """List stored records with evidence and per-record serving verdicts.

    Inferred, stale, superseded, and conflicted records are listed for
    inspection but marked excluded: they must not feed factual answers."""
    with db.connect() as con:
        if folder_id is not None and not con.execute("SELECT id FROM folders WHERE id=?", (folder_id,)).fetchone():
            raise HTTPException(404, "Folder not found")
        sql = "SELECT m.* FROM memory_records m"
        args: list = []
        if folder_id is not None:
            sql += " WHERE m.folder_id=?"
            args.append(folder_id)
        sql += " ORDER BY m.id"
        records = []
        for row in con.execute(sql, args):
            record = dict(row)
            evidence = [dict(item) for item in con.execute(
                """SELECT e.evidence_id,e.role,e.excerpt,f.path AS file,u.locator AS locator
                   FROM memory_record_evidence e
                   LEFT JOIN units u ON u.evidence_id=e.evidence_id
                   LEFT JOIN files f ON f.id=u.file_id
                   WHERE e.record_id=? ORDER BY e.rowid""", (record["id"],))]
            resolving = resolving_supports(con, record["id"])
            authorized = authorized_supports(con, record["id"],
                                             require_consent=not is_local(settings))
            verdict, reason = answer_eligibility(record["status"], record["statement_kind"], resolving)
            if verdict in ("eligible", "labeled", "undecided_only") and not authorized:
                verdict, reason = ("excluded", "current consent does not authorize these sources")
            record["evidence"] = evidence
            record["resolving_supports"] = resolving
            record["authorized_supports"] = len(authorized)
            record["serving"] = {"verdict": verdict, "reason": reason}
            records.append(record)
    return {"records": records}

@app.get("/api/memory/transitions")
def memory_transitions(folder_id: int | None = None, record_id: int | None = None,
                       kind: str | None = None, since: str | None = None,
                       until: str | None = None, limit: int = 50):
    """Read the temporal ledger: validated memory transitions, newest
    first, with previous/new states and evidence snapshots. Read-only;
    the ledger itself is append-only."""
    if not 1 <= limit <= 200:
        raise HTTPException(400, "limit must be between 1 and 200")
    with db.connect() as con:
        if folder_id is not None and not con.execute("SELECT id FROM folders WHERE id=?", (folder_id,)).fetchone():
            raise HTTPException(404, "Folder not found")
        try:
            rows = list_transitions(con, folder_id, record_id,
                                    [kind] if kind else None, since, until, limit)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    return {"transitions": rows}


@app.post("/api/memory/{record_id}/flag")
def flag_memory(record_id: int, request: MemoryFlagRequest):
    """Record a user correction request against one stored record."""
    with db.transaction() as con:
        record = con.execute("SELECT id FROM memory_records WHERE id=?", (record_id,)).fetchone()
        if not record:
            raise HTTPException(404, "Memory record not found")
        flag_id = con.execute("INSERT INTO record_flags(record_id,reason) VALUES(?,?)",
                              (record_id, request.reason.strip())).lastrowid
    return {"flag_id": flag_id, "record_id": record_id}

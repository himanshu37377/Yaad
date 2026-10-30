import json
import hashlib
import os
import re
import unicodedata
import tempfile
from pathlib import Path
from PIL import Image, ExifTags
from yaad.db import Database
from yaad.config import Settings
from yaad.evidence.ids import evidence_id
from yaad.llm import is_local
from yaad.scan.discover import discover
from yaad.scan.hashing import sha256_file
from yaad.extract.pdf import extract_pdf, render_page
from yaad.extract.text import MAX_TEXT_BYTES, extract_text
from yaad.extract.audio import convert_to_wav, chunk_wav
from yaad.memory.records import apply_supersession_links, mark_conflicts, mark_stale


VISION_SCHEMA = {
    "type": "object",
    "properties": {
        "caption": {"type": "string"},
        "people": {"type": "array", "items": {"type": "string"}},
        "objects": {"type": "array", "items": {"type": "string"}},
        "actions": {"type": "array", "items": {"type": "string"}},
        "scene": {"type": "object", "properties": {
            "setting": {"type": "string"}, "terrain": {"type": "array", "items": {"type": "string"}},
            "vegetation": {"type": "array", "items": {"type": "string"}},
            "buildings": {"type": "array", "items": {"type": "string"}},
        }, "required": ["setting", "terrain", "vegetation", "buildings"], "additionalProperties": False},
        "visible_text": {"type": "string"},
        "visual_attributes": {"type": "array", "items": {"type": "string"}},
        "uncertainties": {"type": "array", "items": {"type": "string"}},
        "fields": {"type": "array", "items": {"type": "object", "properties": {
            "name": {"type": "string"}, "value": {"type": "string"}},
            "required": ["name", "value"], "additionalProperties": False}},
    },
    "required": ["caption", "people", "objects", "actions", "scene", "visible_text", "visual_attributes", "uncertainties", "fields"],
    "additionalProperties": False,
}


def _searchable_image_text(result: dict) -> str:
    sections = []
    caption = result.get("caption") or result.get("description")
    if caption:
        sections.append("Description: " + str(caption).strip())
    for key in ("people", "objects", "actions", "visual_attributes", "uncertainties"):
        values = result.get(key) or []
        if values:
            sections.append(key.replace("_", " ").title() + ": " + "; ".join(str(v).strip() for v in values if str(v).strip()))
    scene = result.get("scene")
    if isinstance(scene, dict):
        scene_values = [str(v).strip() for v in scene.values() if isinstance(v, str) and str(v).strip()]
        scene_values += [str(item).strip() for v in scene.values() if isinstance(v, list) for item in v if str(item).strip()]
        if scene_values:
            sections.append("Scene: " + "; ".join(scene_values))
    if result.get("visible_text"):
        sections.append("Visible text: " + str(result["visible_text"]).strip())
    fields = result.get("fields") or []
    if fields:
        sections.append("Fields: " + "; ".join(
            f"{str(field.get('name', '')).strip()}: {str(field.get('value', '')).strip()}"
            for field in fields if isinstance(field, dict)
        ))
    return "\n".join(section for section in sections if section.strip())


def _normalized_source_text(value: str) -> str:
    return re.sub(r"\W+", "", unicodedata.normalize("NFKC", value).casefold(), flags=re.UNICODE)


def _failure_label(client, exc: Exception) -> str:
    status = (getattr(client, "last_call", {}) or {}).get("status_code")
    if isinstance(status, int) and not 200 <= status < 300:
        return f"HTTP {status}"
    return str(exc).strip() or type(exc).__name__


# Largest folder indexed in one pass; beyond this files are counted, loudly
# skipped from processing, and reported as capped (split the folder).
MAX_INDEX_FILES = 100_000

class Indexer:
    def __init__(self, db: Database, settings: Settings):
        self.db, self.settings = db, settings

    def add_folder(self, path: str, cloud_consent: bool = False) -> int:
        folder = Path(path).expanduser().resolve()
        if not folder.is_dir():
            raise ValueError("Folder must be an existing directory")
        if not os.access(folder, os.R_OK | os.X_OK):
            raise ValueError("Folder is not readable")
        try:
            with os.scandir(folder):
                pass
        except OSError as exc:
            raise ValueError("Folder cannot be opened") from exc
        with self.db.transaction() as con:
            con.execute("INSERT INTO folders(path,cloud_consent) VALUES(?,?) ON CONFLICT(path) DO UPDATE SET cloud_consent=excluded.cloud_consent", (str(folder), int(cloud_consent)))
            return con.execute("SELECT id FROM folders WHERE path=?", (str(folder),)).fetchone()["id"]

    def index_folder(self, folder_id: int | None = None) -> dict:
        with self.db.connect() as con:
            folders = con.execute("SELECT * FROM folders WHERE id=? OR ? IS NULL", (folder_id, folder_id)).fetchall()
        if folder_id is not None and not folders:
            raise ValueError("Folder not found")
        counts = {"indexed": 0, "unchanged": 0, "skipped": 0, "failed": 0, "partial": 0, "capped": 0}
        for folder in folders:
            with self.db.transaction() as con:
                run_id = con.execute("INSERT INTO index_runs(folder_id,state) VALUES(?,'running')", (folder["id"],)).lastrowid
            try:
                entries, overflow = discover(Path(folder["path"]), MAX_INDEX_FILES)
                counts["capped"] += overflow
                for path, kind in entries:
                    try:
                        changed = self._index_file(folder["id"], path, kind, bool(folder["cloud_consent"]))
                        with self.db.connect() as con:
                            row = con.execute("SELECT status FROM files WHERE path=?", (str(path),)).fetchone()
                        state = row["status"] if row else "failed"
                        if not changed:
                            counts["unchanged"] += 1
                        elif state == "indexed":
                            counts["indexed"] += 1
                        elif state == "partial":
                            counts["partial"] += 1
                        elif state == "skipped":
                            counts["skipped"] += 1
                        else:
                            counts["failed"] += 1
                    except Exception as exc:
                        self._record_failure(folder["id"], path, kind, exc, bool(folder["cloud_consent"]))
                        counts["failed"] += 1
                self._sweep_deleted(folder["id"])
                # Deterministic memory housekeeping only (no model calls):
                # reindexing changes evidence_ids, so refresh derived statuses.
                with self.db.transaction() as con:
                    sweep_source = f"reindex:{run_id}"
                    mark_conflicts(con, folder["id"], source=sweep_source)
                    apply_supersession_links(con, folder["id"], source=sweep_source)
                    mark_stale(con, folder["id"], source=sweep_source)
            finally:
                with self.db.transaction() as con:
                    con.execute("UPDATE index_runs SET state='complete',finished_at=CURRENT_TIMESTAMP WHERE id=?", (run_id,))
        return counts

    def _sweep_deleted(self, folder_id: int) -> int:
        """Drop index rows for files deleted, moved, or renamed since the last pass."""
        with self.db.connect() as con:
            rows = con.execute("SELECT id,path FROM files WHERE folder_id=?", (folder_id,)).fetchall()
        missing = [row["id"] for row in rows if not Path(row["path"]).exists()]
        if missing:
            with self.db.transaction() as con:
                # Keep cloud history browsable after the source file is gone.
                con.executemany("UPDATE egress_log SET file_id=NULL WHERE file_id=?", [(file_id,) for file_id in missing])
                con.executemany("DELETE FROM files WHERE id=?", [(file_id,) for file_id in missing])
        return len(missing)

    def _index_file(self, folder_id: int, path: Path, kind: str, consent: bool, force: bool = False, force_api: bool = False) -> bool:
        digest = sha256_file(str(path))
        stat = path.stat()
        with self.db.connect() as con:
            old = con.execute("SELECT id,sha256,mtime,status,cloud_consent FROM files WHERE path=?", (str(path),)).fetchone()
        # Partial files may become processable after configuration changes or a transient API error.
        if not force and old and old["sha256"] == digest and old["status"] in ("indexed", "skipped") and old["cloud_consent"] == int(consent):
            return False
        if old:
            with self.db.transaction() as con:
                # Keep the file row stable so egress history and UI references remain valid.
                con.execute("DELETE FROM units WHERE file_id=?", (old["id"],))
                con.execute("DELETE FROM photo_descriptions WHERE file_id=?", (old["id"],))
                con.execute("DELETE FROM photo_meta WHERE file_id=?", (old["id"],))
                con.execute("UPDATE files SET folder_id=?,sha256=?,kind=?,size=?,mtime=?,status='discovered',error=NULL,partial=0,cloud_consent=? WHERE id=?",
                            (folder_id, digest, kind, stat.st_size, stat.st_mtime, int(consent), old["id"]))
            file_id = old["id"]
        else:
            with self.db.transaction() as con:
                file_id = con.execute("INSERT INTO files(folder_id,path,sha256,kind,size,mtime,status,cloud_consent) VALUES(?,?,?,?,?,?,?,?)",
                                      (folder_id, str(path), digest, kind, stat.st_size, stat.st_mtime, "discovered", int(consent))).lastrowid
        status, partial = "indexed", 0
        file_error = None
        photo_exif = {}
        units = []
        local_mode = is_local(self.settings)
        model_allowed = local_mode or (consent and self.settings.cloud_enabled)
        if kind == "pdf":
            units, scanned = extract_pdf(path)
            text_pages = {locator["page"]: text for text, locator in units if locator.get("type") == "pdf_page"}
            if text_pages and model_allowed:
                client = self._meta()
                pdf_bytes = path.read_bytes()
                try:
                    analysis = self._cloud_call(
                        lambda: client.understand_pdf(pdf_bytes, path.name, sorted(text_pages.items())),
                        "PDF document understanding", len(pdf_bytes), "text-layer PDF",
                        model=client.model, file_id=file_id,
                        cache_bytes=pdf_bytes, client=client, cache_version="v2",
                    )
                    local_page_ids = {
                        locator["page"]: evidence_id(digest, locator)
                        for _, locator in units if locator.get("type") == "pdf_page"
                    }
                    for ordinal, fact in enumerate(analysis.get("facts", [])):
                        if not isinstance(fact, dict):
                            continue
                        try:
                            page_no = int(fact.get("page"))
                        except (TypeError, ValueError):
                            continue
                        source_page = text_pages.get(page_no)
                        source_excerpt = str(fact.get("source_excerpt", "")).strip()
                        fact_text = str(fact.get("fact", "")).strip()
                        if not source_page or not source_excerpt or not fact_text:
                            continue
                        if _normalized_source_text(source_excerpt) not in _normalized_source_text(source_page):
                            continue
                        locator = {"type": "pdf_page", "page": page_no, "quote": source_excerpt,
                                   "bbox": None, "derived": "meta_fact", "ordinal": ordinal}
                        units.append((fact_text, locator, {
                            "origin": "local_model" if local_mode else "meta_ai", "kind": "derived_fact", "source_excerpt": source_excerpt,
                            "source_evidence_id": local_page_ids[page_no], "model": client.model,
                        }))
                except Exception as exc:
                    status = "partial"
                    file_error = f"Local PDF text indexed; AI understanding failed ({_failure_label(client, exc)})"
            if scanned:
                if model_allowed:
                    client = self._meta()
                    selected = scanned[:self.settings.max_scanned_pdf_pages]
                    partial = int(len(selected) < len(scanned))
                    if partial:
                        file_error = f"Scanned-page OCR limited to {len(selected)} of {len(scanned)} pages"
                    for page_index in selected:
                        image = render_page(str(path), page_index + 1)
                        try:
                            result = self._cloud_call(lambda: client.vision_extract(image, VISION_SCHEMA, "pdf-ocr-v2"), f"Scanned PDF OCR, page {page_index + 1}", len(image), "page image", model=client.model, file_id=file_id, cache_bytes=image, client=client, cache_version="v2")
                            page_text = _searchable_image_text(result)
                            if page_text:
                                units.append((page_text, {"type": "pdf_page", "page": page_index + 1, "quote": "", "bbox": None},
                                              {"origin": "local_model" if local_mode else "meta_ai", "kind": "scanned_page_understanding", "model": client.model}))
                            else:
                                partial = 1
                                file_error = f"No OCR text returned for scanned page {page_index + 1}"
                        except Exception as exc:
                            partial = 1
                            file_error = f"Scanned page {page_index + 1} failed ({_failure_label(client, exc)})"
                else:
                    partial = 1
                    status = "partial"
                    file_error = "Scanned PDF OCR needs cloud consent with cloud processing enabled, or local model mode (YAAD_MODEL_PROVIDER=local)"
        elif kind in ("text", "docx"):
            units, truncated = extract_text(path)
            if truncated:
                partial = 1
                file_error = (f"Text exceeds {MAX_TEXT_BYTES:,} bytes; only the first "
                              f"{MAX_TEXT_BYTES:,} bytes were indexed. Split the file to index the rest.")
        elif kind == "image":
            photo_exif = self._read_exif(path)
            if model_allowed:
                client = self._meta()
                image_bytes = path.read_bytes()
                try:
                    result = self._cloud_call(lambda: client.vision_extract(image_bytes, VISION_SCHEMA, "image-understanding-v3"), "image understanding and visible text", len(image_bytes), "image", model=client.model, file_id=file_id, cache_bytes=image_bytes, client=client, cache_version="image-description-v4", bypass_cache=force_api)
                    text = _searchable_image_text(result)
                    if text:
                        units = [(text, {"type": "image", "bbox": None},
                                  {"origin": "local_model" if local_mode else "meta_ai", "kind": "image_understanding", "schema_version": 3, "model": client.model})]
                        with self.db.transaction() as con:
                            con.execute("""INSERT OR REPLACE INTO photo_descriptions(file_id,schema_version,model,caption,people,objects,actions,scene,visible_text,visual_attributes,uncertainties)
                                        VALUES(?,3,?,?,?,?,?,?,?,?,?)""",
                                        (file_id, client.model, str(result.get("caption") or result.get("description") or ""),
                                         json.dumps(result.get("people") or [], ensure_ascii=False), json.dumps(result.get("objects") or [], ensure_ascii=False),
                                         json.dumps(result.get("actions") or [], ensure_ascii=False), json.dumps(result.get("scene") or {}, ensure_ascii=False),
                                         str(result.get("visible_text") or ""), json.dumps(result.get("visual_attributes") or [], ensure_ascii=False),
                                         json.dumps(result.get("uncertainties") or [], ensure_ascii=False)))
                    else:
                        status = "partial"
                        file_error = ("Local model returned no searchable description or visible text for this image"
                                        if local_mode else
                                        "Meta returned no searchable description or visible text for this image")
                except Exception as exc:
                    status = "partial"
                    file_error = f"Image understanding failed ({_failure_label(client, exc)})"
            else:
                units = []
                status = "partial"
                file_error = "Image understanding needs cloud consent with cloud processing enabled, or local model mode (YAAD_MODEL_PROVIDER=local)"
        elif kind == "audio":
            transcribed = False
            if model_allowed:
                client = self._meta()
                try:
                    wav_path, duration = convert_to_wav(str(path))
                    try:
                        chunk_limit = min(540, max(60, self.settings.audio_max_minutes * 60 - 60))
                        for chunk, offset in chunk_wav(wav_path, limit_seconds=chunk_limit):
                            try:
                                chunk_bytes = Path(chunk).read_bytes()
                                turns = self._cloud_call(lambda: client.transcribe(chunk), "audio transcription", len(chunk_bytes), "audio chunk", client.model if local_mode else "muse-voice-transcribe-1.0", file_id, chunk_bytes, client=client)
                                transcribed = True
                                for turn in turns:
                                    start = float(turn.get("startMs", turn.get("start", turn.get("start_time", 0)))) / (1000 if "startMs" in turn else 1) + offset
                                    end = float(turn.get("endMs", turn.get("end", turn.get("end_time", start)))) / (1000 if "endMs" in turn else 1) + offset
                                    text = turn.get("transcript", turn.get("text", ""))
                                    if text:
                                        if start < 0 or end < start or end > duration:
                                            raise ValueError("Transcription returned a timestamp outside the recording")
                                        units.append((text, {"type": "audio_segment", "start": start, "end": end, "speaker": turn.get("speaker"), "lang": turn.get("language")}))
                            finally:
                                os.unlink(chunk)
                    finally:
                        if Path(wav_path).resolve() != path.resolve():
                            os.unlink(wav_path)
                except Exception as exc:
                    status = "partial"
                    file_error = f"Audio transcription failed ({_failure_label(client, exc)})"
            if units and all(locator.get("type") == "audio_segment" for _, locator in units):
                units.sort(key=lambda unit: unit[1]["start"])
                compact = []
                for unit in units:
                    if compact and unit[0] == compact[-1][0] and unit[1]["start"] <= compact[-1][1]["end"]:
                        continue
                    compact.append(unit)
                units = compact
            elif transcribed:
                status = "partial"
                if not file_error:
                    file_error = "No speech turns were returned by transcription"
            else:
                status = "partial"
                if not file_error:
                    file_error = "Audio transcription needs cloud consent with cloud processing enabled (the local model has no audio input)"
        else:
            status = "skipped"
        if partial and status == "indexed":
            status = "partial"
        with self.db.transaction() as con:
            con.execute("UPDATE files SET status=?,error=?,partial=? WHERE id=?", (status, file_error, partial, file_id))
            con.executemany("INSERT INTO photo_meta(file_id,source,key,value,confidence) VALUES(?,'exif',?,?,1)",
                            [(file_id, k, v) for k, v in photo_exif.items()])
            for unit in units:
                text, locator = unit[:2]
                unit_meta = unit[2] if len(unit) > 2 else {"origin": "local_extraction"}
                eid = evidence_id(digest, locator)
                cursor = con.execute("INSERT INTO units(file_id,evidence_id,kind,locator,text,meta) VALUES(?,?,?,?,?,?)",
                            (file_id, eid, unit_meta.get("kind", locator["type"]), json.dumps(locator), text,
                             json.dumps({**unit_meta, "unsupported": locator.get("unsupported", False)})))
                if unit_meta.get("kind") == "derived_fact":
                    con.execute("INSERT INTO facts(file_id,unit_id,doc_type,field,value_raw,value_norm,verification,evidence) VALUES(?,?,?,?,?,?,?,?)",
                                (file_id, cursor.lastrowid, "document", "fact", text, text.casefold(),
                                 "source_excerpt_verified", json.dumps({"evidence_id": unit_meta.get("source_evidence_id"),
                                                                         "excerpt": unit_meta.get("source_excerpt"),
                                                                         "page": locator.get("page")})))
                if locator.get("type") == "image" and unit_meta.get("origin") in ("meta_ai", "local_model"):
                    con.execute("INSERT INTO photo_meta(file_id,source,key,value,confidence) VALUES(?,'model','understanding',?,NULL)",
                                (file_id, text[:4000]))
        return True

    def redescribe_image(self, file_id: int) -> dict:
        from yaad.llm import is_local
        with self.db.connect() as con:
            row = con.execute("SELECT f.*,fo.cloud_consent AS folder_cloud_consent FROM files f JOIN folders fo ON fo.id=f.folder_id WHERE f.id=? AND f.kind='image'", (file_id,)).fetchone()
            if not row:
                raise ValueError("Image file not found")
            old_file = dict(row)
            old_units = [dict(r) for r in con.execute("SELECT * FROM units WHERE file_id=?", (file_id,))]
            old_photo = con.execute("SELECT * FROM photo_descriptions WHERE file_id=?", (file_id,)).fetchone()
            old_model_meta = [dict(r) for r in con.execute("SELECT * FROM photo_meta WHERE file_id=? AND source='model'", (file_id,))]
        local_mode = is_local(self.settings)
        if not local_mode and not self.settings.cloud_enabled:
            raise PermissionError("Cloud processing is disabled in app settings")
        if not local_mode and not old_file["folder_cloud_consent"]:
            raise PermissionError("Folder cloud consent is off")
        with self.db.transaction() as con:
            con.execute("INSERT INTO jobs(file_id,step,state,attempts,error) VALUES(NULL, 'photo_redescription_v3','running',1,NULL)")
            job_id = con.execute("SELECT last_insert_rowid()").fetchone()[0]
        try:
            self._index_file(old_file["folder_id"], Path(old_file["path"]), "image", True, force=True, force_api=True)
            with self.db.connect() as con:
                current = con.execute("SELECT id,status,error FROM files WHERE path=?", (old_file["path"],)).fetchone()
                description = con.execute("SELECT 1 FROM photo_descriptions WHERE file_id=?", (current["id"],)).fetchone() if current else None
            if description:
                with self.db.transaction() as con:
                    con.execute("UPDATE jobs SET file_id=?,state='complete',error=NULL WHERE id=?", (current["id"], job_id))
                return {"job_id": job_id, "status": "complete", "file_id": current["id"]}
            if current and old_units:
                # Keep a previous useful description/evidence if Meta cannot produce the new schema.
                with self.db.transaction() as con:
                    for unit in old_units:
                        con.execute("INSERT OR IGNORE INTO units(file_id,evidence_id,kind,locator,text,meta) VALUES(?,?,?,?,?,?)",
                                    (current["id"], unit["evidence_id"], unit["kind"], unit["locator"], unit["text"], unit["meta"]))
                    if old_photo:
                        values = dict(old_photo)
                        values["file_id"] = current["id"]
                        con.execute("""INSERT OR REPLACE INTO photo_descriptions(file_id,schema_version,model,caption,people,objects,actions,scene,visible_text,visual_attributes,uncertainties,updated_at)
                                     VALUES(:file_id,:schema_version,:model,:caption,:people,:objects,:actions,:scene,:visible_text,:visual_attributes,:uncertainties,:updated_at)""", values)
                    for meta in old_model_meta:
                        con.execute("INSERT INTO photo_meta(file_id,source,key,value,confidence) VALUES(?,'model',?,?,?)",
                                    (current["id"], meta["key"], meta["value"], meta["confidence"]))
            failure = current["error"] if current else "Image disappeared while it was re-described"
            with self.db.transaction() as con:
                con.execute("UPDATE jobs SET state='failed',error=? WHERE id=?", (str(failure or "Meta did not return a new description")[:500], job_id))
            return {"job_id": job_id, "status": "failed", "file_id": current["id"] if current else file_id, "error": failure}
        except Exception as exc:
            with self.db.transaction() as con:
                con.execute("UPDATE jobs SET state='failed',error=? WHERE id=?", (type(exc).__name__, job_id))
            raise

    def _read_exif(self, path: Path):
        try:
            with Image.open(path) as image:
                exif = image.getexif()
                result = {ExifTags.TAGS.get(k, str(k)): str(v) for k, v in exif.items() if not isinstance(v, (bytes, dict))}
                result.update({"ImageWidth": str(image.width), "ImageLength": str(image.height), "ImageFormat": str(image.format or "")})
                exif_ifd = exif.get_ifd(ExifTags.IFD.Exif) if 34665 in exif else {}
                result.update({ExifTags.TAGS.get(k, str(k)): str(v) for k, v in exif_ifd.items()
                               if not isinstance(v, (bytes, dict)) and ExifTags.TAGS.get(k, str(k)) in ("DateTimeOriginal", "DateTimeDigitized", "ExposureTime", "FNumber", "ISOSpeedRatings", "FocalLength")})
                gps = exif.get_ifd(ExifTags.IFD.GPSInfo) if 34853 in exif else {}
                if gps:
                    tags = {ExifTags.GPSTAGS.get(k, str(k)): v for k, v in gps.items()}
                    for name in ("GPSLatitudeRef", "GPSLongitudeRef", "GPSAltitudeRef", "GPSTimeStamp", "GPSDateStamp"):
                        if name in tags:
                            result[name] = str(tags[name])
                    for axis, ref_name in (("GPSLatitude", "GPSLatitudeRef"), ("GPSLongitude", "GPSLongitudeRef")):
                        values = tags.get(axis)
                        if values and len(values) >= 3:
                            decimal = sum(float(values[index]) / (60 ** index) for index in range(3))
                            if str(tags.get(ref_name, "N" if axis == "GPSLatitude" else "E")).upper() in ("S", "W"):
                                decimal = -decimal
                            result[axis + "Decimal"] = f"{decimal:.7f}"
                return result
        except Exception:
            return {}

    def _meta(self):
        from yaad.llm import get_client, is_local
        if not is_local(self.settings) and not self.settings.cloud_enabled:
            raise RuntimeError("Cloud inference is disabled in configuration")
        return get_client(self.settings)

    def _egress(self, file_id, unit_id, purpose, size, content_kind, model=None, call_info=None):
        info = call_info or {}
        with self.db.transaction() as con:
            con.execute("""INSERT INTO egress_log(file_id,unit_id,purpose,provider,model,bytes,content_kind,
                        status_code,success,duration_ms,input_tokens,output_tokens,cached_tokens,estimated_cost_usd,error_code)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (file_id, unit_id, purpose, info.get("provider", "Meta Model API"), model or info.get("model", self.settings.checked_model()),
                         size, content_kind, info.get("status_code"), None if info.get("success") is None else int(info["success"]),
                         info.get("duration_ms"), info.get("input_tokens"), info.get("output_tokens"), info.get("cached_tokens"),
                         info.get("estimated_cost_usd"), info.get("error_code")))

    def _cloud_call(self, operation, purpose, size, content_kind, model=None, file_id=None, cache_bytes=None, client=None, cache_version="v1", bypass_cache=False):
        key = None
        if cache_bytes is not None:
            params = json.dumps({"content_sha256": hashlib.sha256(cache_bytes).hexdigest(), "operation": purpose,
                                 "model": model or self.settings.checked_model(), "prompt_version": cache_version},
                                sort_keys=True, separators=(",", ":"))
            key = hashlib.sha256(params.encode()).hexdigest()
            with self.db.connect() as con:
                cached = None if bypass_cache else con.execute("SELECT response FROM cache WHERE key=?", (key,)).fetchone()
            if cached:
                return json.loads(cached["response"])
        try:
            result = operation()
            if key:
                with self.db.transaction() as con:
                    con.execute("INSERT OR REPLACE INTO cache(key,response) VALUES(?,?)", (key, json.dumps(result, ensure_ascii=False)))
            return result
        finally:
            self._egress(file_id, None, purpose, size, content_kind, model, getattr(client, "last_call", None))

    def _record_failure(self, folder_id, path, kind, exc, consent=False):
        digest = sha256_file(str(path)) if path.exists() else ""
        stat = path.stat() if path.exists() else None
        with self.db.transaction() as con:
            con.execute("INSERT INTO files(folder_id,path,sha256,kind,size,mtime,status,error,cloud_consent) VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(path) DO UPDATE SET status='failed',error=excluded.error,cloud_consent=excluded.cloud_consent",
                        (folder_id, str(path), digest, kind, stat.st_size if stat else 0, stat.st_mtime if stat else 0, "failed", str(exc)[:1000], int(consent)))

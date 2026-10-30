import json
import wave
from pathlib import Path

import fitz
from PIL import Image

from yaad.config import Settings
from yaad.db import Database
from yaad.query.answer import answer_question
from yaad.scan.worker import Indexer


class FakeMeta:
    model = "muse-spark-1.3"

    def __init__(self):
        self.calls = []
        self.last_call = {"provider": "Meta Model API", "model": self.model, "status_code": 200,
                          "success": True, "duration_ms": 3, "input_tokens": 20, "output_tokens": 5}

    def understand_pdf(self, pdf_bytes, filename, page_texts):
        self.calls.append(("pdf", len(pdf_bytes)))
        return {"facts": [{"fact": "The deadline is 2026-11-30.", "page": 1,
                           "source_excerpt": "The project deadline is 2026-11-30."}]}

    def vision_extract(self, image_bytes, schema, prompt_id):
        self.calls.append(("image", len(image_bytes)))
        return {"description": "A blue bicycle beside a brick wall.", "visible_text": "",
                "doc_type": "photo", "fields": []}

    def transcribe(self, audio_path):
        self.calls.append(("audio", Path(audio_path).stat().st_size))
        return [{"startMs": 250, "endMs": 1100, "transcript": "Meet at noon.", "language": "en"}]


def make_indexer(tmp_path, cloud_enabled=True, consent=True):
    database = Database(tmp_path / "memory.sqlite3")
    settings = Settings(db_path=tmp_path / "memory.sqlite3", cloud_enabled=cloud_enabled)
    indexer = Indexer(database, settings)
    folder = tmp_path / "source"
    folder.mkdir()
    folder_id = indexer.add_folder(str(folder), consent)
    fake = FakeMeta()
    indexer._meta = lambda: fake
    return database, indexer, folder, folder_id, fake


def test_pdf_facts_keep_verified_page_excerpt_and_are_searchable(tmp_path):
    db, indexer, folder, folder_id, fake = make_indexer(tmp_path)
    pdf = fitz.open()
    page = pdf.new_page()
    page.insert_text((72, 72), "The project deadline is 2026-11-30.")
    pdf.save(folder / "brief.pdf")
    pdf.close()

    result = indexer.index_folder(folder_id)
    repeated = indexer.index_folder(folder_id)
    answer = answer_question(db, "What is the deadline?", folder_id)
    with db.connect() as con:
        fact = con.execute("SELECT * FROM facts").fetchone()
        derived = con.execute("SELECT locator,meta FROM units WHERE kind='derived_fact'").fetchone()

    assert result["indexed"] == 1
    assert repeated["unchanged"] == 1
    assert fake.calls[0][0] == "pdf"
    assert answer["status"] == "answered"
    assert answer["values"][0]["value"] == "2026-11-30"
    assert fact["verification"] == "source_excerpt_verified"
    assert json.loads(derived["locator"])["page"] == 1
    assert json.loads(derived["meta"])["source_excerpt"] == "The project deadline is 2026-11-30."


def test_image_understanding_is_searchable_but_needs_both_cloud_gates(tmp_path):
    db, indexer, folder, folder_id, fake = make_indexer(tmp_path)
    Image.new("RGB", (80, 80), "blue").save(folder / "photo.png")
    indexer.index_folder(folder_id)
    indexer.settings.cloud_enabled = False
    offline = Settings(db_path=tmp_path / "memory.sqlite3", cloud_enabled=False)
    answer = answer_question(db, "Where is the blue bicycle?", folder_id, offline)
    assert answer["evidence"]
    assert "blue bicycle" in answer["evidence"][0]["quote"].lower()
    assert fake.calls[0][0] == "image"

    db2, indexer2, folder2, folder_id2, fake2 = make_indexer(tmp_path / "private", cloud_enabled=True, consent=False)
    Image.new("RGB", (80, 80), "blue").save(folder2 / "photo.png")
    counts = indexer2.index_folder(folder_id2)
    with db2.connect() as con:
        row = con.execute("SELECT status FROM files").fetchone()
    assert counts["partial"] == 1
    assert row["status"] == "partial"
    assert fake2.calls == []


def test_audio_transcript_keeps_valid_timestamped_evidence(tmp_path):
    db, indexer, folder, folder_id, fake = make_indexer(tmp_path)
    audio = folder / "voice.wav"
    with wave.open(str(audio), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\0\0" * 32000)

    result = indexer.index_folder(folder_id)
    with db.connect() as con:
        unit = con.execute("SELECT text,locator FROM units").fetchone()
    locator = json.loads(unit["locator"])
    assert result["indexed"] == 1
    assert unit["text"] == "Meet at noon."
    assert locator["type"] == "audio_segment"
    assert locator["start"] == 0.25 and locator["end"] == 1.1
    assert fake.calls and fake.calls[0][0] == "audio"


def test_scanned_pdf_ocr_requires_both_cloud_gates_and_creates_page_evidence(tmp_path):
    db, indexer, folder, folder_id, fake = make_indexer(tmp_path, cloud_enabled=False, consent=True)
    image_path = tmp_path / "scan.png"
    Image.new("RGB", (120, 60), "white").save(image_path)
    pdf = fitz.open()
    page = pdf.new_page()
    page.insert_image(page.rect, filename=str(image_path))
    pdf.save(folder / "scan.pdf")
    pdf.close()

    blocked = indexer.index_folder(folder_id)
    assert blocked["partial"] == 1
    assert fake.calls == []

    indexer.settings.cloud_enabled = True
    indexed = indexer.index_folder(folder_id)
    assert indexed["indexed"] == 1
    assert fake.calls and fake.calls[0][0] == "image"
    with db.connect() as con:
        unit = con.execute("SELECT locator,meta FROM units").fetchone()
    assert json.loads(unit["locator"])["page"] == 1
    assert json.loads(unit["meta"])["kind"] == "scanned_page_understanding"


def test_bad_audio_timestamps_mark_recording_partial(tmp_path):
    db, indexer, folder, folder_id, fake = make_indexer(tmp_path)
    audio = folder / "voice.wav"
    with wave.open(str(audio), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\0\0" * 16000)
    fake.transcribe = lambda _path: [{"startMs": 0, "endMs": 2000, "transcript": "Invalid duration."}]
    result = indexer.index_folder(folder_id)
    with db.connect() as con:
        file = con.execute("SELECT status,error FROM files").fetchone()
        units = con.execute("SELECT COUNT(*) FROM units").fetchone()[0]
    assert result["partial"] == 1
    assert file["status"] == "partial"
    assert "timestamp" in file["error"].lower()
    assert units == 0


def test_partial_files_retry_and_running_index_returns_indexing_state(tmp_path):
    db, indexer, folder, folder_id, fake = make_indexer(tmp_path, cloud_enabled=False)
    Image.new("RGB", (40, 40), "red").save(folder / "photo.png")
    first = indexer.index_folder(folder_id)
    indexer.settings.cloud_enabled = True
    second = indexer.index_folder(folder_id)
    assert first["partial"] == 1
    assert second["indexed"] == 1
    assert fake.calls and fake.calls[0][0] == "image"

    with db.transaction() as con:
        con.execute("INSERT INTO index_runs(folder_id,state) VALUES(?,'running')", (folder_id,))
    assert answer_question(db, "blue red", folder_id)["status"] == "indexing"


def test_semantic_answer_requires_consent_and_rejects_unknown_citations(tmp_path, monkeypatch):
    from yaad.llm import meta_client

    db, indexer, folder, folder_id, _ = make_indexer(tmp_path, cloud_enabled=True, consent=True)
    source = folder / "plans.txt"
    source.write_text("The team plans a short coastal field visit in early winter.", encoding="utf-8")
    indexer.index_folder(folder_id)
    with db.connect() as con:
        eid = con.execute("SELECT evidence_id FROM units").fetchone()[0]

    class ReviewClient:
        last_call = {"provider": "Meta Model API", "model": "muse-spark-1.3", "status_code": 200,
                     "success": True, "duration_ms": 4, "input_tokens": 40, "output_tokens": 8}

        def __init__(self, _settings):
            pass

        def reason_json(self, *_args):
            return {"status": "answered", "answer": "A coastal field visit in early winter.",
                    "claims": [{"claim": "A coastal field visit is planned for early winter.",
                                "evidence_ids": ["not-a-real-evidence-id"]}]}

    monkeypatch.setattr(meta_client, "MetaClient", ReviewClient)
    bad = answer_question(db, "What is the team planning?", folder_id, indexer.settings)
    assert bad["status"] == "cloud_unavailable"
    assert len(bad["evidence"]) == 1
    with db.connect() as con:
        calls = con.execute("SELECT COUNT(*) n FROM egress_log WHERE purpose='semantic answer review'").fetchone()["n"]
    assert calls == 1

    class ReviewClient(ReviewClient):
        def reason_json(self, *_args):
            return {"status": "answered", "answer": "A coastal field visit is planned for early winter.",
                    "claims": [{"claim": "A coastal field visit is planned for early winter.", "evidence_ids": [eid]}]}

    monkeypatch.setattr(meta_client, "MetaClient", ReviewClient)
    good = answer_question(db, "What is the team planning?", folder_id, indexer.settings)
    assert good["status"] == "answered"
    assert good["evidence"][0]["id"] == eid
    assert any(call["why"] == "semantic answer review" for call in good["disclosure"])


def test_deadline_question_uses_semantic_review_across_original_sources(tmp_path, monkeypatch):
    from docx import Document
    from yaad.llm import meta_client

    db, indexer, folder, folder_id, _ = make_indexer(tmp_path, cloud_enabled=True, consent=True)
    pdf = fitz.open()
    page = pdf.new_page()
    page.insert_text((72, 72), "Revised prototype submission deadline: 20 November 2026. The overall project target launch date remains 18 November 2026.")
    pdf.save(folder / "revised_deadline_notice.pdf")
    pdf.close()
    doc = Document()
    doc.add_paragraph("Target launch deadline: 18 November 2026.")
    doc.save(folder / "campus_connect_brief.docx")
    (folder / "project_notes.txt").write_text("Original prototype target: 18 November 2026.", encoding="utf-8")
    (folder / "README_TEST_GUIDE.txt").write_text("Expected deadline: 20 November 2026.", encoding="utf-8")
    indexer.index_folder(folder_id)
    with db.connect() as con:
        ids = {row["kind"]: row["evidence_id"] for row in con.execute("SELECT u.kind,u.evidence_id FROM units u")}
        rows = con.execute("SELECT evidence_id FROM units WHERE kind='pdf_page'").fetchall()
        pdf_id = rows[0]["evidence_id"]
        docx_id = con.execute("SELECT evidence_id FROM units WHERE kind='text_span' AND file_id=(SELECT id FROM files WHERE path LIKE '%campus_connect_brief.docx')").fetchone()[0]

    class ReviewClient:
        last_call = {"provider": "Meta Model API", "model": "muse-spark-1.3", "status_code": 200,
                     "success": True, "duration_ms": 12, "input_tokens": 500, "output_tokens": 60}

        def __init__(self, _settings):
            pass

        def reason_json(self, messages, schema, prompt_id):
            prompt = json.loads(messages[0]["content"])
            assert prompt["question"] == "what's the application deadline"
            names = {source["source_name"] for source in prompt["sources"]}
            assert "revised_deadline_notice.pdf" in names
            assert "campus_connect_brief.docx" in names
            assert "README_TEST_GUIDE.txt" not in names
            assert prompt_id == "evidence_answer"
            assert schema["required"] == ["status", "answer", "claims"]
            return {"status": "answered",
                    "answer": "The revised prototype submission deadline is 20 November 2026. The overall target launch date remains 18 November 2026; these are different milestones.",
                    "claims": [
                        {"claim": "Revised prototype submission deadline: 20 November 2026.", "evidence_ids": [pdf_id]},
                        {"claim": "Overall project target launch date remains 18 November 2026.", "evidence_ids": [pdf_id, docx_id]},
                    ]}

    monkeypatch.setattr(meta_client, "MetaClient", ReviewClient)
    result = answer_question(db, "what's the application deadline", folder_id, indexer.settings)
    assert result["status"] == "answered"
    assert "20 November 2026" in result["answer"]
    assert "18 November 2026" in result["answer"]
    assert {item["id"] for item in result["evidence"]} == {pdf_id, docx_id}
    assert sum(call["why"] == "semantic answer review" for call in result["disclosure"]) == 1


def test_malformed_structured_model_output_is_not_reported_as_success():
    from yaad.llm.meta_client import MetaClient

    client = MetaClient(Settings(model_api_key="synthetic-test-key"))
    client.responses = lambda *_args, **_kwargs: {"output_text": "not JSON"}
    try:
        client.reason_json([], {"type": "object", "properties": {}, "required": [], "additionalProperties": False}, "test")
    except ValueError as exc:
        assert "malformed structured output" in str(exc)
    else:
        raise AssertionError("malformed model output should be rejected")
    assert client.last_call["success"] is False
    assert client.last_call["error_code"] == "malformed_structured_response"


def test_open_decision_query_retrieves_undecided_offline_mode(tmp_path):
    db, indexer, folder, folder_id, _ = make_indexer(tmp_path)
    (folder / "notes.txt").write_text("The decision about offline mode remains undecided.", encoding="utf-8")
    indexer.index_folder(folder_id)
    settings = Settings(db_path=tmp_path / "memory.sqlite3", cloud_enabled=False)
    answer = answer_question(db, "Which project decision is still open?", folder_id, settings)
    assert answer["status"] == "cloud_unavailable"
    assert answer["evidence"]
    assert "offline mode" in answer["evidence"][0]["quote"].lower()


def test_audio_no_speech_turns_is_reported_without_fabricated_transcript(tmp_path):
    db, indexer, folder, folder_id, _ = make_indexer(tmp_path)
    audio = folder / "tone.wav"
    with wave.open(str(audio), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\x00\x00" * 3200)
    indexer.index_folder(folder_id)
    with db.transaction() as con:
        con.execute("UPDATE files SET status='partial',error='No speech turns were returned by transcription'")
    answer = answer_question(db, "What does the audio voice note say?", folder_id)
    assert answer["status"] == "unverified"
    assert "returned no speech turns" in answer["answer"]
    assert answer["evidence"] == []


def test_photo_search_gallery_and_visual_location_are_scoped_and_uncertain(tmp_path):
    db, indexer, folder, folder_id, fake = make_indexer(tmp_path)
    image = folder / "hillside.jpg"
    Image.new("RGB", (120, 80), "green").save(image)

    def rich_vision(*_args):
        fake.calls.append(("image", image.stat().st_size))
        return {"caption": "A person standing beside a bike on a mountain trail.",
                "people": ["One person wearing a backpack"], "objects": ["bicycle", "backpack"],
                "actions": ["standing beside a bicycle"],
                "scene": {"setting": "outdoor trail", "terrain": ["mountains"], "vegetation": ["pine trees"], "buildings": []},
                "visible_text": "", "visual_attributes": ["green hillside"], "uncertainties": ["Exact location not visible"]}

    fake.vision_extract = rich_vision
    indexer.index_folder(folder_id)
    bike = answer_question(db, "Find a photo of me with a bike", folder_id, indexer.settings)
    assert bike["status"] == "answered"
    assert bike["photos"][0]["filename"] == "hillside.jpg"
    assert "bicycle" in bike["photos"][0]["quote"]
    all_photos = answer_question(db, "Fetch all my photos", folder_id, indexer.settings)
    assert len(all_photos["photos"]) == 1
    place = answer_question(db, "Find my photos from Dharamshala, Himachal Pradesh", folder_id, indexer.settings)
    assert place["photos"][0]["location_confidence"] == "unconfirmed_visual_match"
    assert "visual matches only" in place["answer"]
    no_match = answer_question(db, "Find photos with a red car", folder_id, indexer.settings)
    assert no_match["status"] == "not_found"
    with db.connect() as con:
        stored = con.execute("SELECT schema_version,objects,scene FROM photo_descriptions").fetchone()
    assert stored["schema_version"] == 3
    assert "bicycle" in stored["objects"]
    assert "mountains" in stored["scene"]


def test_audio_questions_do_not_return_unrelated_document_matches(tmp_path):
    db, indexer, folder, folder_id, _ = make_indexer(tmp_path)
    (folder / "README_TEST_GUIDE.txt").write_text("Audio testing notes are in this README.", encoding="utf-8")
    indexer.index_folder(folder_id)
    answer = answer_question(db, "What was in the audio file I saved?", folder_id, indexer.settings)
    assert answer["status"] == "not_found"
    assert answer["evidence"] == []
    assert "No audio files" in answer["answer"]


def test_exif_gps_is_extracted_separately_from_visual_location(tmp_path, monkeypatch):
    from fractions import Fraction
    from yaad.scan import worker

    db, indexer, _, _, _ = make_indexer(tmp_path)

    class Exif(dict):
        def get_ifd(self, _tag):
            return {1: "N", 2: (Fraction(32), Fraction(43), Fraction(0)),
                    3: "W", 4: (Fraction(117), Fraction(9), Fraction(0))}

    class FakeImage:
        width = 800
        height = 600
        format = "JPEG"

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def getexif(self):
            return Exif({34853: "GPS", 271: "Synthetic Camera", 36867: "2024:05:06 10:11:12", 274: 1})

    monkeypatch.setattr(worker.Image, "open", lambda _path: FakeImage())
    metadata = indexer._read_exif(tmp_path / "with_gps.jpg")
    assert metadata["GPSLatitudeDecimal"] == "32.7166667"
    assert metadata["GPSLongitudeDecimal"] == "-117.1500000"
    assert metadata["Make"] == "Synthetic Camera"
    assert metadata["DateTimeOriginal"] == "2024:05:06 10:11:12"

    class NoExif(FakeImage):
        def getexif(self):
            return Exif()

    monkeypatch.setattr(worker.Image, "open", lambda _path: NoExif())
    without_gps = indexer._read_exif(tmp_path / "without_gps.jpg")
    assert "GPSLatitudeDecimal" not in without_gps
    assert without_gps["ImageWidth"] == "800"


def test_reindex_keeps_file_id_referenced_by_cloud_history(tmp_path):
    db, indexer, folder, folder_id, _ = make_indexer(tmp_path, cloud_enabled=False, consent=False)
    image = folder / "photo.png"
    Image.new("RGB", (24, 24), "blue").save(image)
    with db.transaction() as con:
        file_id = con.execute("INSERT INTO files(folder_id,path,sha256,kind,size,mtime,status) VALUES(?,?,?,?,?,?,?)",
                              (folder_id, str(image), "old-hash", "image", image.stat().st_size, 0, "indexed")).lastrowid
        con.execute("INSERT INTO egress_log(file_id,purpose,provider,model,bytes,content_kind) VALUES(?,?,?,?,?,?)",
                    (file_id, "previous image understanding", "Meta", "model", 20, "image"))
    indexer._index_file(folder_id, image, "image", False, force=True)
    with db.connect() as con:
        current_id = con.execute("SELECT id FROM files WHERE path=?", (str(image),)).fetchone()[0]
        history_id = con.execute("SELECT file_id FROM egress_log WHERE purpose='previous image understanding'").fetchone()[0]
    assert current_id == file_id == history_id


def test_failed_redescription_preserves_previous_photo_description(tmp_path):
    db, indexer, folder, folder_id, fake = make_indexer(tmp_path, cloud_enabled=True, consent=True)
    image = folder / "photo.png"
    Image.new("RGB", (24, 24), "blue").save(image)
    indexer.index_folder(folder_id)
    with db.connect() as con:
        file_id = con.execute("SELECT id FROM files WHERE path=?", (str(image),)).fetchone()[0]
        previous = con.execute("SELECT caption FROM photo_descriptions WHERE file_id=?", (file_id,)).fetchone()[0]

    class FailingMeta(FakeMeta):
        last_call = {"provider": "Meta Model API", "model": "muse-spark-1.3", "status_code": 503,
                     "success": False, "duration_ms": 2, "error_code": "http_503"}

        def vision_extract(self, *_args):
            raise RuntimeError("provider unavailable")

    indexer._meta = lambda: FailingMeta()
    result = indexer.redescribe_image(file_id)
    assert result["status"] == "failed"
    with db.connect() as con:
        restored = con.execute("SELECT caption FROM photo_descriptions WHERE file_id=?", (file_id,)).fetchone()[0]
        unit = con.execute("SELECT text FROM units WHERE file_id=? AND kind='image_understanding'", (file_id,)).fetchone()
        job = con.execute("SELECT state FROM jobs WHERE id=?", (result["job_id"],)).fetchone()[0]
    assert restored == previous
    assert unit is not None
    assert job == "failed"

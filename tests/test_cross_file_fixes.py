"""Regression tests for cross-file retrieval, citation dedupe, answer status,
and audio timestamp bounds."""
import json
import wave
from pathlib import Path

import fitz

from yaad.config import Settings
from yaad.db import Database
from yaad.query.answer import answer_question
from yaad.scan.worker import Indexer


class StubMeta:
    model = "muse-spark-1.3"

    def __init__(self, turns=None, facts=None):
        self.turns = turns if turns is not None else [
            {"startMs": 0, "endMs": 1500, "transcript": "Kickoff is March 3rd, budget 4000 rupees.", "language": "en"}]
        self.facts = facts
        self.last_call = {"provider": "Meta Model API", "model": self.model, "status_code": 200,
                          "success": True, "duration_ms": 3, "input_tokens": 20, "output_tokens": 5}

    def understand_pdf(self, pdf_bytes, filename, page_texts):
        if self.facts is None:
            return {"facts": []}
        return {"facts": self.facts}

    def vision_extract(self, image_bytes, schema, prompt_id):
        return {"description": "", "visible_text": "", "doc_type": "photo", "fields": []}

    def transcribe(self, audio_path):
        return self.turns


def make_db(tmp_path, cloud_enabled=True):
    database = Database(tmp_path / "case.sqlite3")
    settings = Settings(db_path=tmp_path / "case.sqlite3", cloud_enabled=cloud_enabled)
    indexer = Indexer(database, settings)
    folder = tmp_path / "source"
    folder.mkdir(exist_ok=True)
    folder_id = indexer.add_folder(str(folder), True)
    return database, indexer, folder, folder_id


def write_wav(path, seconds=2, rate=16000):
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(b"\x00\x00" * int(seconds * rate))


def unit_ids(db):
    with db.connect() as con:
        return {row["evidence_id"]: row["kind"] for row in con.execute("SELECT evidence_id,kind FROM units")}


class CaptureReview:
    """Stub reviewer that records the sources it was given."""

    last_call = {"provider": "Meta Model API", "model": "muse-spark-1.3", "status_code": 200,
                 "success": True, "duration_ms": 4, "input_tokens": 40, "output_tokens": 8}
    seen = None
    verdict = {"status": "answered", "answer": "Combined.", "claims": []}

    def __init__(self, _settings):
        pass

    def reason_json(self, messages, schema, prompt_id):
        type(self).seen = json.loads(messages[0]["content"])["sources"]
        return dict(type(self).verdict)


def test_corroboration_question_retrieves_audio_and_other_files(tmp_path, monkeypatch):
    from yaad.llm import meta_client

    db, indexer, folder, folder_id = make_db(tmp_path)
    (folder / "notes.txt").write_text("Kickoff is March 3rd. Budget is 4000 rupees.", encoding="utf-8")
    write_wav(folder / "brief.wav")
    indexer._meta = lambda: StubMeta()
    indexer.index_folder(folder_id)
    with db.connect() as con:
        eids = [row["evidence_id"] for row in con.execute("SELECT evidence_id FROM units")]
    CaptureReview.verdict = {"status": "answered", "answer": "Both mention March 3rd.",
                             "claims": [{"claim": "March 3rd in both", "evidence_ids": eids[:2]}]}
    CaptureReview.seen = None
    monkeypatch.setattr(meta_client, "MetaClient", CaptureReview)
    result = answer_question(db, "Which details discussed in the audio are corroborated by other files?",
                             folder_id, indexer.settings)
    assert result["status"] == "answered"
    kinds = {source["source_type"] for source in CaptureReview.seen}
    assert "audio" in kinds
    assert kinds - {"audio"}, "review saw no non-audio sources"
    files = {Path(item["file"]).name for item in result["evidence"]}
    assert files == {"brief.wav", "notes.txt"}


def test_exact_reported_corroboration_wording_retrieves_other_files(tmp_path, monkeypatch):
    """Exact wording from the reported runtime failure."""
    from yaad.llm import meta_client
    from yaad.query.answer import _is_cross_file_question

    question = ("What decisions or arrangements are discussed in the audio, and which "
                "of those details are corroborated by the other files?")
    assert _is_cross_file_question(question)
    db, indexer, folder, folder_id = make_db(tmp_path)
    (folder / "notes.txt").write_text("Kickoff is March 3rd. Budget is 4000 rupees.", encoding="utf-8")
    write_wav(folder / "brief.wav")
    indexer._meta = lambda: StubMeta()
    indexer.index_folder(folder_id)
    with db.connect() as con:
        eids = [row["evidence_id"] for row in con.execute("SELECT evidence_id FROM units")]
    CaptureReview.verdict = {"status": "answered", "answer": "Both mention March 3rd.",
                             "claims": [{"claim": "March 3rd in both", "evidence_ids": eids[:2]}]}
    CaptureReview.seen = None
    monkeypatch.setattr(meta_client, "MetaClient", CaptureReview)
    result = answer_question(db, question, folder_id, indexer.settings)
    assert result["status"] == "answered"
    kinds = {source["source_type"] for source in CaptureReview.seen}
    assert "audio" in kinds
    assert kinds - {"audio"}, "review saw no non-audio sources"
    assert {Path(item["file"]).name for item in result["evidence"]} == {"brief.wav", "notes.txt"}


def test_plain_audio_question_stays_audio_scoped(tmp_path, monkeypatch):
    from yaad.llm import meta_client

    db, indexer, folder, folder_id = make_db(tmp_path)
    (folder / "notes.txt").write_text("Kickoff is March 3rd. Budget is 4000 rupees.", encoding="utf-8")
    write_wav(folder / "brief.wav")
    indexer._meta = lambda: StubMeta()
    indexer.index_folder(folder_id)
    CaptureReview.verdict = {"status": "answered", "answer": "The budget is 4000 rupees.",
                             "claims": [{"claim": "budget 4000", "evidence_ids": [
                                 unit_id for unit_id, kind in unit_ids(db).items() if kind == "audio_segment"][:1]}]}
    CaptureReview.seen = None
    monkeypatch.setattr(meta_client, "MetaClient", CaptureReview)
    result = answer_question(db, "What budget does the audio recording mention?", folder_id, indexer.settings)
    assert result["status"] == "answered"
    assert CaptureReview.seen, "review saw no sources"
    assert {source["source_type"] for source in CaptureReview.seen} == {"audio"}


def test_same_page_citations_collapse_to_one_card(tmp_path):
    db, indexer, folder, folder_id = make_db(tmp_path)
    pdf = fitz.open()
    page = pdf.new_page()
    page.insert_text((72, 72), "Launch date is April 9th. Doors open at nine.")
    pdf.save(folder / "launch.pdf")
    pdf.close()
    indexer._meta = lambda: StubMeta(facts=[
        {"fact": "Launch date is April 9th.", "page": 1, "source_excerpt": "Launch date is April 9th."},
        {"fact": "Doors open at nine.", "page": 1, "source_excerpt": "Doors open at nine."}])
    indexer.index_folder(folder_id)
    settings = Settings(db_path=tmp_path / "case.sqlite3", cloud_enabled=False)
    result = answer_question(db, "When is the launch?", folder_id, settings)
    assert result["evidence"], "expected cited evidence"
    cards = [(item["file"], item["locator"].get("type"), item["locator"].get("page"))
             for item in result["evidence"]]
    assert len(cards) == len(set(cards)) == 1
    shown = {item["id"] for item in result["evidence"]}
    for value in result["values"]:
        assert set(value["evidence_ids"]) <= shown, "value cites a dropped card"


def test_explicitly_undecided_answer_counts_as_supported(tmp_path, monkeypatch):
    from yaad.llm import meta_client

    db, indexer, folder, folder_id = make_db(tmp_path)
    (folder / "notes.txt").write_text("Two venue options still need to be decided. Meet Friday noon.", encoding="utf-8")
    indexer.index_folder(folder_id)
    with db.connect() as con:
        eid = con.execute("SELECT evidence_id FROM units").fetchone()[0]

    class CautiousReview(CaptureReview):
        pass

    CautiousReview.verdict = {"status": "unverified",
                              "answer": "The venue options are still undecided; the meeting is Friday noon.",
                              "claims": [{"claim": "venue undecided, meeting Friday noon", "evidence_ids": [eid]}]}
    monkeypatch.setattr(meta_client, "MetaClient", CautiousReview)
    result = answer_question(db, "Which venue was decided and when is the meeting?", folder_id, indexer.settings)
    assert result["status"] == "answered"
    assert result["evidence"]


def test_genuinely_unanswerable_question_stays_unverified(tmp_path, monkeypatch):
    from yaad.llm import meta_client

    db, indexer, folder, folder_id = make_db(tmp_path)
    (folder / "notes.txt").write_text("Two venue options still need to be decided.", encoding="utf-8")
    indexer.index_folder(folder_id)
    with db.connect() as con:
        eid = con.execute("SELECT evidence_id FROM units").fetchone()[0]

    class HonestReview(CaptureReview):
        pass

    HonestReview.verdict = {"status": "unverified",
                            "answer": "No caterer is mentioned anywhere in the notes.",
                            "claims": [{"claim": "no caterer mentioned", "evidence_ids": [eid]}]}
    monkeypatch.setattr(meta_client, "MetaClient", HonestReview)
    result = answer_question(db, "Which caterer was decided?", folder_id, indexer.settings)
    assert result["status"] == "unverified"


def test_question_with_no_evidence_is_not_found(tmp_path):
    db, indexer, folder, folder_id = make_db(tmp_path)
    (folder / "notes.txt").write_text("Two venue options still need to be decided.", encoding="utf-8")
    indexer.index_folder(folder_id)
    result = answer_question(db, "What is the quantum flux capacity?", folder_id, indexer.settings)
    assert result["status"] == "not_found"
    assert result["evidence"] == []


def test_partial_multipart_answer_is_preserved(tmp_path, monkeypatch):
    from yaad.llm import meta_client

    db, indexer, folder, folder_id = make_db(tmp_path)
    (folder / "notes.txt").write_text("Meeting Friday noon in the seminar hall.", encoding="utf-8")
    indexer.index_folder(folder_id)
    with db.connect() as con:
        eid = con.execute("SELECT evidence_id FROM units").fetchone()[0]

    class PartialReview(CaptureReview):
        pass

    PartialReview.verdict = {"status": "partial",
                             "answer": "The meeting is Friday noon in the seminar hall; no caterer is mentioned.",
                             "claims": [{"claim": "meeting Friday noon seminar hall", "evidence_ids": [eid]}]}
    monkeypatch.setattr(meta_client, "MetaClient", PartialReview)
    result = answer_question(db, "Where is the meeting and who is catering?", folder_id, indexer.settings)
    assert result["status"] == "partial"
    assert [item["id"] for item in result["evidence"]] == [eid]


def test_audio_timestamps_are_stored_exactly_within_duration(tmp_path):
    db, indexer, folder, folder_id = make_db(tmp_path)
    write_wav(folder / "brief.wav", seconds=10)
    indexer._meta = lambda: StubMeta(turns=[
        {"startMs": 0, "endMs": 5670, "transcript": "First part.", "language": "en"},
        {"startMs": 5718, "endMs": 8046, "transcript": "Second part.", "language": "en"}])
    outcome = indexer.index_folder(folder_id)
    assert outcome["indexed"] == 1
    with db.connect() as con:
        locators = [json.loads(row["locator"]) for row in
                    con.execute("SELECT locator FROM units ORDER BY id")]
    assert [(loc["start"], loc["end"]) for loc in locators] == [(0.0, 5.67), (5.718, 8.046)]
    assert all(0 <= loc["start"] <= loc["end"] <= 10 for loc in locators)


def test_audio_timestamp_beyond_duration_fails_openly(tmp_path):
    db, indexer, folder, folder_id = make_db(tmp_path)
    write_wav(folder / "brief.wav", seconds=4)
    indexer._meta = lambda: StubMeta(turns=[
        {"startMs": 0, "endMs": 9000, "transcript": "Impossibly long turn.", "language": "en"}])
    outcome = indexer.index_folder(folder_id)
    assert outcome["partial"] == 1
    with db.connect() as con:
        row = con.execute("SELECT status,error FROM files").fetchone()
    assert row["status"] == "partial"
    assert "timestamp" in row["error"].lower()


def test_chunk_offsets_shift_later_segment_timestamps(tmp_path):
    db, indexer, folder, folder_id = make_db(tmp_path)
    indexer.settings.audio_max_minutes = 2  # 60s chunks with a 2s overlap
    write_wav(folder / "long.wav", seconds=65)
    indexer._meta = lambda: StubMeta(turns=[
        {"startMs": 0, "endMs": 1000, "transcript": "Chunk marker.", "language": "en"}])
    indexer.index_folder(folder_id)
    with db.connect() as con:
        locators = [json.loads(row["locator"]) for row in
                    con.execute("SELECT locator FROM units ORDER BY id")]
    assert len(locators) == 2
    assert locators[0]["start"] == 0.0
    assert locators[1]["start"] == 58.0  # second chunk starts after the 58s stride
    assert all(loc["end"] <= 65 for loc in locators)


def test_ordinal_dates_are_extracted_for_expansion(tmp_path):
    from yaad.query.answer import extract_field_value

    field, values = extract_field_value(
        "When is it?", "The big day is November 18th 2026 and planning ends November 6th.")
    assert field == "date"
    assert "November 18th 2026" in values
    assert "November 6th" in values

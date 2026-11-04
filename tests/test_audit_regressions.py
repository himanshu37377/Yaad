"""Final-audit regressions: one focused test per fixed defect.

Each test names the defect it pins. Temporary databases/files only;
no network, no model spend.
"""
import pytest

from yaad.db import Database


# --- db: ':memory:' must be one database, not a fresh one per connect ---

def test_memory_database_roundtrips_across_connections():
    db = Database(":memory:")
    with db.transaction() as con:
        con.execute("INSERT INTO folders(path, cloud_consent) VALUES('/tmp/x', 1)")
    with db.connect() as con:
        row = con.execute("SELECT path FROM folders").fetchone()
    assert row["path"] == "/tmp/x"
    other = Database(":memory:")
    with other.connect() as con:
        assert con.execute("SELECT COUNT(*) n FROM folders").fetchone()["n"] == 0


def test_backfill_amount_keys_merges_collision_instead_of_bricking(tmp_path):
    from yaad.memory.records import backfill_amount_keys

    db = Database(tmp_path / "legacy.sqlite3")
    with db.transaction() as con:
        con.execute("INSERT INTO folders(path) VALUES('/tmp/f')")
        base = "decision|title|detail|2026-01-01|sig"
        for key in (base, base + "|INR100"):
            con.execute(
                "INSERT INTO memory_records(folder_id,record_type,title,record_key,extraction_run)"
                " VALUES(1,'decision','T',?,'r')", (key,))
        legacy_id = con.execute(
            "SELECT id FROM memory_records WHERE record_key=?", (base,)).fetchone()["id"]
        con.execute(
            "INSERT INTO memory_record_evidence(record_id,evidence_id,role,excerpt)"
            " VALUES(?, 'ev1', 'supports', 'Fee INR 100 due')", (legacy_id,))
    with db.connect() as con:
        assert backfill_amount_keys(con) >= 1
        keys = [r["record_key"] for r in con.execute("SELECT record_key FROM memory_records")]
    assert keys == [base + "|INR100"]  # merged, no UNIQUE explosion


# --- discovery: budget and consent boundary ---

def test_discover_unsupported_files_do_not_starve_supported(tmp_path):
    from yaad.scan.discover import discover

    for i in range(5):
        (tmp_path / f"a{i}.zip").write_bytes(b"x")
    for i in range(5):
        (tmp_path / f"b{i}.txt").write_text("hello")
    entries, overflow = discover(tmp_path, 5)
    assert [p.name for p, _ in entries] == [f"b{i}.txt" for i in range(5)]
    assert overflow == 0


def test_discover_never_follows_symlink_outside_folder(tmp_path):
    from yaad.scan.discover import discover

    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    folder = tmp_path / "in"
    folder.mkdir()
    (folder / "ok.txt").write_text("fine")
    (folder / "link.txt").symlink_to(outside)
    entries, _ = discover(folder, 10)
    assert [p.name for p, _ in entries] == ["ok.txt"]


# --- extraction: DOCX tables are indexed ---

def test_docx_table_text_is_extracted_in_order(tmp_path):
    from docx import Document

    from yaad.extract.text import extract_text

    path = tmp_path / "t.docx"
    doc = Document()
    doc.add_paragraph("Intro")
    table = doc.add_table(rows=1, cols=2)
    table.rows[0].cells[0].text = "Fee"
    table.rows[0].cells[1].text = "Rs 5000"
    doc.save(str(path))
    units, truncated = extract_text(path)
    texts = [t for t, _ in units]
    assert truncated is False
    assert any("Rs 5000" in t for t in texts)


# --- privacy: redaction classifies by meaning, fails closed ---

@pytest.mark.parametrize("raw,kept", [
    ("My SSN is 123-45-6789", "6789"),
    ("Passport Z1234567 issued", "4567"),
    ("PAN ABCDE1234F holder", "234F"),
    ("Call 98765 43210 now", "3210"),
    ("password=hunter2", "ter2"),
])
def test_redact_text_masks_previously_missed_identifiers(raw, kept):
    from yaad.privacy.masking import redact_text

    redacted, count = redact_text(raw)
    assert count >= 1
    assert kept in redacted
    for secret in ("123-45-6789", "Z1234567", "ABCDE1234F", "98765 43210", "hunter2"):
        assert secret not in redacted


def test_redact_text_leaves_amounts_and_dates_readable():
    from yaad.privacy.masking import redact_text

    redacted, count = redact_text("Fee is Rs 1500 on 2026-03-03 at 12 30 pm")
    assert count == 0
    assert "1500" in redacted and "2026-03-03" in redacted


def test_is_sensitive_ignores_substring_false_positives():
    from yaad.privacy.masking import is_sensitive

    assert is_sensitive("the syntax of the language", "notes.txt") is False
    assert is_sensitive("your tax return is ready", "notes.txt") is True


# --- verify: a model can never create evidence ---

def test_model_cannot_upgrade_zero_evidence_claim():
    from yaad.config import Settings
    from yaad.query.verify import verify_claims

    class FakeClient:
        last_call = {"model": "muse-spark-1.3", "status_code": 200}

        def reason_json(self, messages, schema, prompt_id):
            return {"items": [{"index": 0, "verdict": "supported", "reason": "looks fine"}]}

    settings = Settings(cloud_enabled=True, model_api_key="k")
    report = verify_claims([("The fee is 500 rupees.", [])], {},
                           settings=settings, client=FakeClient(), use_model=True)
    assert report.claims[0].verdict == "insufficient"


def test_blank_evidence_never_contains_a_claim():
    from yaad.query.verify import deterministic_check

    verdict, _ = deterministic_check("Zebra invented in Paris.", [""])
    assert verdict != "supported"


# --- amounts: symbols, leading zeros, no silent truncation ---

def test_euro_and_pound_amounts_rival():
    from yaad.memory.records import amount_class_of

    assert amount_class_of(["Fee €100"])[0] == "EUR100"
    assert amount_class_of(["Fee £200"])[0] == "GBP200"
    assert amount_class_of(["Fee €100"])[0] != amount_class_of(["Fee €200"])[0]


def test_amount_normalization_folds_leading_zeros_and_keeps_precision():
    from yaad.memory.records import normalize_amount

    assert normalize_amount("INR 0100") == normalize_amount("INR 100")
    assert normalize_amount("INR 10.999") == "INR10.999"


# --- lifecycle: deterministic effective time, replacement beats hedge ---

def test_effective_time_is_order_independent_and_supersession_only():
    from yaad.memory.lifecycle import effective_time

    record = {"status": "superseded", "created_at": "2026-01-01"}
    transitions = [
        {"transition": "superseded", "created_at": "2026-02-01", "id": 1},
        {"transition": "conflicted", "created_at": "2026-04-01", "id": 3},
        {"transition": "superseded", "created_at": "2026-03-01", "id": 2},
    ]
    assert effective_time(record, transitions) == "2026-03-01"
    assert effective_time(record, list(reversed(transitions))) == "2026-03-01"


def test_classify_update_replacement_language_beats_hedges():
    from yaad.memory.lifecycle import SUPERSESSION, classify_update

    rival = {"id": 7}
    decision = classify_update("Maybe fee moved to INR 200 instead", rival)
    assert decision.kind == SUPERSESSION
    assert decision.rival_record_id == 7


# --- hybrid: optional-channel failure isolation ---

def test_hybrid_isolates_broken_optional_channel(monkeypatch, tmp_path):
    from yaad.query import hybrid

    db = Database(tmp_path / "h.sqlite3")
    real = hybrid.retrieve_channel

    def flaky(db_, question, channel, depth, folder_id, file_kinds, local):
        if channel == "semantic":
            raise RuntimeError("weights missing")
        return real(db_, question, channel, depth, folder_id, file_kinds, local)

    monkeypatch.setattr(hybrid, "retrieve_channel", flaky)
    rows = hybrid.hybrid_search(db, "anything", channels=("keyword", "semantic"))
    assert rows == []  # keyword found nothing, semantic failure isolated
    with pytest.raises(RuntimeError):
        hybrid.hybrid_search(db, "anything", channels=("semantic",))


# --- pipeline: spend gate fails closed ---

def test_pipeline_spend_gate_fails_closed_on_error(monkeypatch):
    from yaad.config import Settings
    from yaad.llm import meta_config
    from yaad.query.pipeline import _model_gate

    def boom(*a, **k):
        raise RuntimeError("db gone")

    monkeypatch.setattr(meta_config, "spend_refusal", boom)
    settings = Settings(cloud_enabled=True, model_api_key="k")
    refusal = _model_gate(settings, None, "stage")
    assert refusal is not None and "spend check failed" in refusal


# --- intents: factual photo/status questions are not diverted ---

def test_photo_intents_use_word_boundaries():
    from yaad.query.photos import location_intent, photo_value_intent

    assert location_intent("Show photos in 2024") is False
    assert location_intent("Show photos in Shimla") is True
    assert photo_value_intent("Show photo of candidate form") is False


def test_status_intent_ignores_value_seeking_questions():
    from yaad.query.memory_answer import detect_status_intent

    assert detect_status_intent("What is proposal fee?") == []
    assert detect_status_intent("List the unresolved records") == ["unresolved"]
    assert detect_status_intent("Show conflicted records") == ["conflicted"]


def test_multimodal_conflict_detects_bare_amounts_and_capital_venues():
    from yaad.memory.multimodal import _texts_conflict

    assert _texts_conflict("INR 100", "INR 200") is True
    assert _texts_conflict("Venue is Riverside Hall", "Venue is Hilltop Arena") is True
    assert _texts_conflict("INR 100", "INR 100") is False


# --- llm guards ---

def test_local_endpoint_validation_rejects_ssrf_shapes():
    from pydantic import ValidationError as PydanticValidationError

    from yaad.config import Settings

    for bad in ("file:///etc/passwd", "http://169.254.169.254/latest",
                "http://user:pass@evil.example.com", "not-a-url"):
        with pytest.raises(PydanticValidationError):
            Settings(local_endpoint=bad)
    assert Settings(local_endpoint="http://localhost:11434").local_endpoint


def test_cost_estimate_tolerates_null_usage():
    from yaad.llm.meta_config import estimate_text_cost_usd

    assert estimate_text_cost_usd("muse-spark-1.3", {"input_tokens": None}) == 0.0


def test_strict_schema_preserves_nullable_enum_and_rejects_cycles():
    from typing import Literal

    from pydantic import BaseModel

    from yaad.llm.strict_schema import to_strict_schema

    class WithNullableEnum(BaseModel):
        status: Literal["a", "b"] | None = None

    schema = to_strict_schema(WithNullableEnum)
    assert schema["properties"]["status"].get("enum") == ["a", "b"]

    class NodeB(BaseModel):
        a: "NodeA"

    class NodeA(BaseModel):
        b: NodeB

    NodeB.model_rebuild()
    NodeA.model_rebuild()
    with pytest.raises(ValueError, match="cyclic"):
        to_strict_schema(NodeA)

"""Joint image comparison: client shape, cache, spend, verdict validation.

Single-request two-image shape, content-hash caching (a hit makes zero
new calls), one egress receipt per compared pair, strict schema plus
grounding validation, and advisory purity: a verdict never clears
conflicted status, never creates supersedes edges, and never touches
record, evidence, unit, or photo rows.

Stub and fake clients only; the Meta adapter is exercised with a
captured transport, never the network.
"""

import base64
import json
from io import BytesIO

import pytest
from test_cross_modal_conflicts import (
    IMG_A,
    IMG_B,
    ImageStore,
    conflict_store,
    image_candidate,
    route,
    supports,
)

from yaad.config import Settings
from yaad.llm.meta_client import JOINT_IMAGE_COMPARISON_SCHEMA, MetaClient
from yaad.llm.meta_config import estimate_text_cost_usd
from yaad.memory.multimodal import (
    COMPARE_PROMPT_VERSION,
    CompareDecision,
    compare_cache_key,
    compare_via_client,
    difference_grounded,
    route_record_compare,
    run_image_compare,
    validate_compare_verdict,
)
from yaad.query.photos import annotate_with_compare

VERDICT = {
    "same_subject": True,
    "differences": ["Left image shows a wall poster while right image shows "
                    "a gate notice board"],
    "uncertainties": ["fee figures differ across posters"],
}


class StubCompareClient:
    """Deterministic joint-compare stub: records calls, never dials out."""

    def __init__(self, verdict, last_call=None):
        self.verdict = verdict
        self.calls: list[dict] = []
        self.last_call = {"provider": "stub", "model": "stub-compare",
                          "status_code": 200, "success": True}
        if last_call:
            self.last_call.update(last_call)

    def compare_images(self, a_bytes, b_bytes, schema, prompt_id):
        self.calls.append({"a": a_bytes, "b": b_bytes, "schema": schema,
                           "prompt_id": prompt_id})
        return json.loads(json.dumps(self.verdict))


class FakeLocalClient:
    """Local-adapter shape without compare_images: one chat call with
    both images attached."""

    def __init__(self, verdict):
        self.verdict = verdict
        self.calls: list = []
        self.last_call = {"provider": "local", "model": "fake-local",
                          "status_code": 200, "success": True,
                          "estimated_cost_usd": 0.0}

    def _chat(self, messages, schema, timeout=300.0):
        self.calls.append((messages, schema))
        return json.loads(json.dumps(self.verdict))


def png_bytes(color):
    from PIL import Image

    buffer = BytesIO()
    Image.new("RGB", (8, 8), color).save(buffer, format="PNG")
    return buffer.getvalue()


def egress_rows(store):
    with store.db.connect() as con:
        return [dict(row) for row in
                con.execute("SELECT * FROM egress_log ORDER BY id")]


def cache_rows(store):
    with store.db.connect() as con:
        return [dict(row) for row in con.execute("SELECT * FROM cache")]


# --- client shape: single request, two image blocks, strict schema ---

def test_meta_compare_images_sends_single_joint_request(tmp_path):
    settings = Settings(db_path=tmp_path / "m.sqlite3", model_api_key="test-key")
    client = MetaClient(settings)
    seen: dict = {}

    def fake_responses(payload, timeout=90):
        seen["payload"] = payload
        client.last_call = {"provider": "Meta Model API", "model": settings.model,
                            "status_code": 200, "success": True}
        return {"output_text": json.dumps(VERDICT)}

    client.responses = fake_responses
    out = client.compare_images(png_bytes((255, 0, 0)), png_bytes((0, 0, 255)),
                                JOINT_IMAGE_COMPARISON_SCHEMA, "image-compare-v1")
    assert out == VERDICT
    payload = seen["payload"]
    assert payload["model"] == settings.model
    assert len(payload["input"]) == 1
    content = payload["input"][0]["content"]
    assert [block["type"] for block in content] == ["input_text", "input_image",
                                                   "input_image"]
    assert content[1]["image_url"].startswith("data:image/png;base64,")
    assert content[2]["image_url"].startswith("data:image/png;base64,")
    assert content[1]["image_url"] != content[2]["image_url"]
    text_format = payload["text"]["format"]
    assert text_format["strict"] is True
    assert text_format["schema"] == JOINT_IMAGE_COMPARISON_SCHEMA
    assert text_format["name"] == "image_compare_v1"


def test_meta_vision_extract_shape_unchanged(tmp_path):
    """The shared image-encoding refactor keeps single-image calls
    byte-identical in shape."""
    settings = Settings(db_path=tmp_path / "v.sqlite3", model_api_key="test-key")
    client = MetaClient(settings)
    seen: dict = {}

    def fake_responses(payload, timeout=90):
        seen["payload"] = payload
        client.last_call = {"provider": "Meta Model API", "model": settings.model,
                            "status_code": 200, "success": True}
        return {"output_text": '{"caption": "x"}'}

    client.responses = fake_responses
    schema = {"type": "object", "properties": {"caption": {"type": "string"}},
              "required": ["caption"], "additionalProperties": False}
    assert client.vision_extract(png_bytes((1, 2, 3)), schema, "photo-v1") == {"caption": "x"}
    content = seen["payload"]["input"][0]["content"]
    assert [block["type"] for block in content] == ["input_text", "input_image"]
    assert content[1]["image_url"].startswith("data:image/png;base64,")


def test_compare_via_client_prefers_compare_images():
    stub = StubCompareClient(VERDICT)
    out = compare_via_client(stub, b"a-bytes", b"b-bytes")
    assert out == VERDICT
    assert len(stub.calls) == 1
    assert stub.calls[0]["prompt_id"] == "image-compare-v1"
    assert stub.calls[0]["schema"] == JOINT_IMAGE_COMPARISON_SCHEMA


def test_compare_via_client_local_fallback_is_single_two_image_call():
    fake = FakeLocalClient(VERDICT)
    out = compare_via_client(fake, b"a-bytes", b"b-bytes")
    assert out == VERDICT
    assert len(fake.calls) == 1
    messages, schema = fake.calls[0]
    assert len(messages) == 1
    assert len(messages[0]["images"]) == 2
    assert base64.b64decode(messages[0]["images"][0]) == b"a-bytes"
    assert base64.b64decode(messages[0]["images"][1]) == b"b-bytes"
    assert schema == JOINT_IMAGE_COMPARISON_SCHEMA


def test_compare_via_client_rejects_unsupported_client():
    with pytest.raises(RuntimeError, match="does not support joint image comparison"):
        compare_via_client(object(), b"a", b"b")


# --- cache key: content hash over bytes + schema + prompt version ---

def test_cache_key_is_ab_order_invariant():
    assert compare_cache_key(b"aaa", b"bbb") == compare_cache_key(b"bbb", b"aaa")


def test_cache_key_changes_with_bytes_schema_or_version():
    base = compare_cache_key(b"aaa", b"bbb")
    assert compare_cache_key(b"aaa", b"bbc") != base
    assert compare_cache_key(b"aaa", b"bbb", schema={"type": "object"}) != base
    assert compare_cache_key(b"aaa", b"bbb", prompt_version="other-v9") != base


# --- run: one call, one receipt, then cache hits make zero calls ---

def test_run_makes_one_call_and_one_receipt_then_cache_hit(tmp_path):
    store = conflict_store(tmp_path / "run.sqlite3")
    stub = StubCompareClient(VERDICT)
    decision = route(store)
    assert decision.fires

    first = run_image_compare(store.db, store.settings, stub, decision)
    assert first["status"] == "compared"
    assert first["advisory"] is True
    assert first["same_subject"] is True
    assert first["differences"] == VERDICT["differences"]
    assert first["annotated_uncertainty"] == VERDICT["uncertainties"]
    assert first["cache_hit"] is False
    assert first["calls_made"] == 1
    assert len(stub.calls) == 1
    assert stub.calls[0]["a"] != stub.calls[0]["b"]

    receipts = egress_rows(store)
    assert len(receipts) == 1
    receipt = receipts[0]
    assert receipt["purpose"] == "joint image comparison"
    assert receipt["provider"] == "local"
    assert receipt["bytes"] == len(stub.calls[0]["a"]) + len(stub.calls[0]["b"])
    assert receipt["estimated_cost_usd"] == 0.0
    assert len(cache_rows(store)) == 1

    second = run_image_compare(store.db, store.settings, stub, decision)
    assert second["status"] == "compared"
    assert second["cache_hit"] is True
    assert second["calls_made"] == 0
    assert len(stub.calls) == 1
    assert second["differences"] == VERDICT["differences"]
    # Cache hit: zero new calls and zero new receipts.
    assert len(egress_rows(store)) == 1
    assert len(cache_rows(store)) == 1


def test_run_cloud_receipt_records_consent_and_spend(tmp_path):
    store = conflict_store(tmp_path / "receipt.sqlite3", consent=1,
                           cloud_enabled=True, provider="cloud")
    stub = StubCompareClient(VERDICT)
    decision = route(store)
    result = run_image_compare(store.db, store.settings, stub, decision)
    assert result["status"] == "compared"
    assert result["calls_made"] == 1
    receipts = egress_rows(store)
    assert len(receipts) == 1
    assert receipts[0]["purpose"] == "joint image comparison"
    assert receipts[0]["content_kind"] == ("two consented images, PII n/a "
                                                   "(pixel payload, no EXIF/paths sent)")
    assert receipts[0]["estimated_cost_usd"] == pytest.approx(decision.estimated_cost_usd)


def test_run_cloud_receipt_uses_reported_usage(tmp_path):
    store = conflict_store(tmp_path / "usage.sqlite3", consent=1,
                           cloud_enabled=True, provider="cloud")
    last_call = {"input_tokens": 2000, "output_tokens": 100, "cached_tokens": 500}
    stub = StubCompareClient(VERDICT, last_call=last_call)
    decision = route(store)
    result = run_image_compare(store.db, store.settings, stub, decision)
    assert result["status"] == "compared"
    expected = estimate_text_cost_usd(
        store.settings.model,
        {"input_tokens": 2000, "output_tokens": 100,
         "input_tokens_details": {"cached_tokens": 500}})
    assert egress_rows(store)[0]["estimated_cost_usd"] == pytest.approx(expected)


def test_run_refuses_post_call_spend_breach_without_caching(tmp_path):
    store = conflict_store(tmp_path / "breach.sqlite3", consent=1,
                           cloud_enabled=True, provider="cloud")
    stub = StubCompareClient(
        VERDICT, last_call={"input_tokens": 100_000_000, "output_tokens": 0})
    decision = route(store)
    with pytest.raises(RuntimeError, match="exceeded its ceiling"):
        run_image_compare(store.db, store.settings, stub, decision)
    assert cache_rows(store) == []
    # The attempt may have been billed: exactly one receipt stands.
    assert len(egress_rows(store)) == 1


def test_run_refused_decision_makes_zero_calls(tmp_path):
    store = conflict_store(tmp_path / "refused.sqlite3")
    stub = StubCompareClient(VERDICT)
    with store.db.connect() as con:
        decision = route_record_compare(con, store.folder_id, 999, 1000,
                                        store.settings)
    assert not decision.fires
    result = run_image_compare(store.db, store.settings, stub, decision)
    assert result == {"status": "refused", "reason": "unknown_record",
                      "calls_made": 0, "cache_hit": False}
    assert stub.calls == []
    assert egress_rows(store) == []
    assert cache_rows(store) == []


def test_run_refuses_unreadable_images(tmp_path):
    store = conflict_store(tmp_path / "missing.sqlite3")
    with store.db.transaction() as con:
        con.execute("UPDATE files SET path='/nonexistent/missing-' || id || '.png'")
    stub = StubCompareClient(VERDICT)
    # The spend gate prices post-thumbnail bytes, so unreadable images
    # refuse at routing time. The run-level check below stays as
    # defense-in-depth for decisions built before deletion.
    decision = route(store)
    assert decision.fires is False
    assert decision.reason == "image_unavailable"
    assert stub.calls == []
    assert egress_rows(store) == []
    stale = CompareDecision(True, "fires", decision.left, decision.right,
                            decision.estimated_cost_usd)
    result = run_image_compare(store.db, store.settings, stub, stale)
    assert result["status"] == "refused"
    assert result["reason"] == "image_unavailable"
    assert stub.calls == []
    assert egress_rows(store) == []


# --- verdict validation: strict schema, grounding, advisory purity ---

def test_validate_accepts_grounded_verdict():
    check = validate_compare_verdict(VERDICT, [IMG_A, IMG_B])
    assert check.ok is True
    assert check.verdict == VERDICT
    assert check.unverifiable == []


def test_validate_accepts_joint_schema_summaries():
    from yaad.memory.multimodal import validate_compare_verdict
    verdict = {"same_subject": True, "image_a_summary": "wall poster",
               "image_b_summary": "gate board",
               "differences": ["wall poster gate board"], "uncertainties": []}
    check = validate_compare_verdict(verdict, ["wall poster", "gate board"])
    assert check.ok is True
    assert check.verdict == {"same_subject": True,
                             "differences": ["wall poster gate board"],
                             "uncertainties": []}


@pytest.mark.parametrize("bad", [
    "not-a-dict",
    {"same_subject": True, "differences": ["wall poster gate board"],
     "uncertainties": [], "extra": 1},
    {"same_subject": "yes", "differences": ["wall poster gate board"],
     "uncertainties": []},
    {"same_subject": 1, "differences": ["wall poster gate board"],
     "uncertainties": []},
    {"same_subject": True, "differences": ["   "], "uncertainties": []},
    {"same_subject": True, "differences": "wall poster",
     "uncertainties": []},
    {"same_subject": True, "differences": ["wall poster gate board"],
     "uncertainties": "unsure"},
])
def test_validate_rejects_malformed_verdicts(bad):
    check = validate_compare_verdict(bad, [IMG_A, IMG_B])
    assert check.ok is False
    assert check.errors


def test_validate_flags_ungrounded_differences():
    verdict = {"same_subject": True,
               "differences": ["wall poster beside a gate notice board",
                               "the moon landing was staged in a studio"],
               "uncertainties": []}
    check = validate_compare_verdict(verdict, [IMG_A, IMG_B])
    assert check.ok is False
    assert check.unverifiable == [1]
    assert any("unverifiable" in error for error in check.errors)


def test_difference_grounded_accepts_verbatim_quote():
    assert difference_grounded("Autumn Fest registration fee Rs 150 per person",
                               [IMG_A, IMG_B]) is True
    assert difference_grounded("", [IMG_A]) is False
    assert difference_grounded("the moon landing was staged", [IMG_A]) is False


def test_run_rejects_ungrounded_verdict_without_caching(tmp_path):
    store = conflict_store(tmp_path / "ungrounded.sqlite3")
    stub = StubCompareClient({"same_subject": True,
                              "differences": ["the moon landing was staged"],
                              "uncertainties": []})
    decision = route(store)
    with pytest.raises(ValueError, match="unverifiable"):
        run_image_compare(store.db, store.settings, stub, decision)
    assert cache_rows(store) == []
    assert len(egress_rows(store)) == 1


def test_run_rejects_empty_differences_when_subjects_differ(tmp_path):
    store = conflict_store(tmp_path / "empty-diff.sqlite3")
    stub = StubCompareClient({"same_subject": False, "differences": [],
                              "uncertainties": []})
    decision = route(store)
    with pytest.raises(ValueError, match="non-empty"):
        run_image_compare(store.db, store.settings, stub, decision)
    assert cache_rows(store) == []


def test_run_accepts_empty_differences_for_identical_images(tmp_path):
    store = conflict_store(tmp_path / "identical.sqlite3")
    stub = StubCompareClient({"same_subject": True, "differences": [],
                              "uncertainties": ["pixel-identical"]})
    decision = route(store)
    result = run_image_compare(store.db, store.settings, stub, decision)
    assert result["status"] == "compared"
    assert result["differences"] == []
    assert len(cache_rows(store)) == 1


def test_verdict_never_mutates_conflict_state(tmp_path):
    """Advisory only: statuses stay conflicted, no supersedes edges are
    created, and current evidence rows are byte-identical after."""
    store = conflict_store(tmp_path / "purity.sqlite3")
    tables = ("memory_records", "memory_record_evidence", "photo_descriptions",
              "units", "files", "folders")
    before = store.snapshot_tables(tables)
    stub = StubCompareClient(VERDICT)
    decision = route(store)
    result = run_image_compare(store.db, store.settings, stub, decision)
    assert result["status"] == "compared"
    assert store.snapshot_tables(tables) == before
    with store.db.connect() as con:
        assert {row["status"] for row in
                con.execute("SELECT status FROM memory_records")} == {"conflicted"}
        assert con.execute("SELECT COUNT(*) n FROM memory_record_evidence"
                           " WHERE role='supersedes'").fetchone()["n"] == 0


# --- negative test + A/B invariance ---

def test_near_identical_captions_verdict_comes_from_joint_call(tmp_path):
    """Captions match word-for-word (a caption diff would say nothing),
    yet the joint visual verdict reports a concrete grounded
    difference: the advisory is the model's, not a caption diff."""
    near_a = ("Description: Red poster on wall\n"
              "Visible text: Autumn Fest registration fee Rs 150")
    near_b = ("Description: Red poster on wall\n"
              "Visible text: Autumn Fest registration fee Rs 200")
    store = ImageStore(tmp_path / "near.sqlite3")
    store.add_image("a.png", "ev_a", near_a)
    store.add_image("b.png", "ev_b", near_b)
    store.add_records("run-1", [
        image_candidate("Autumn Fest registration fee", "registration fee",
                        [supports("ev_a", "Autumn Fest registration fee Rs 150")]),
        image_candidate("Autumn Fest registration fee", "registration fee",
                        [supports("ev_b", "Autumn Fest registration fee Rs 200")]),
    ])
    with store.db.connect() as con:
        captions = [row["caption"] for row in
                    con.execute("SELECT caption FROM photo_descriptions ORDER BY file_id")]
    assert len(captions) == 2
    caption_diff = set(captions[0].split()) ^ set(captions[1].split())
    assert caption_diff == set()

    verdict = {"same_subject": True,
               "differences": ["registration fee differs between wall posters"],
               "uncertainties": []}
    stub = StubCompareClient(verdict)
    decision = route(store)
    assert decision.fires
    result = run_image_compare(store.db, store.settings, stub, decision)
    assert result["status"] == "compared"
    assert result["differences"] == verdict["differences"]
    assert result["differences"] != []


def test_swapped_ab_order_keeps_same_subject_and_reuses_cache(tmp_path):
    store = conflict_store(tmp_path / "swap.sqlite3")
    stub = StubCompareClient(VERDICT)
    left, right = store.record_ids()
    with store.db.connect() as con:
        forward = route_record_compare(con, store.folder_id, left, right,
                                       store.settings)
        backward = route_record_compare(con, store.folder_id, right, left,
                                        store.settings)
    assert forward.fires and backward.fires
    first = run_image_compare(store.db, store.settings, stub, forward)
    second = run_image_compare(store.db, store.settings, stub, backward)
    assert first["same_subject"] == second["same_subject"] is True
    assert first["differences"] == second["differences"]
    assert second["cache_hit"] is True
    assert second["calls_made"] == 0
    assert len(stub.calls) == 1


def test_local_fallback_run_costs_zero_with_one_receipt(tmp_path):
    store = conflict_store(tmp_path / "local-fallback.sqlite3")
    fake = FakeLocalClient(VERDICT)
    decision = route(store)
    result = run_image_compare(store.db, store.settings, fake, decision)
    assert result["status"] == "compared"
    assert result["calls_made"] == 1
    assert len(fake.calls) == 1
    receipts = egress_rows(store)
    assert len(receipts) == 1
    assert receipts[0]["provider"] == "local"
    assert receipts[0]["estimated_cost_usd"] == 0.0


# --- serving: conflicts still served with both sources cited ---

def test_annotate_with_compare_marks_both_cited_sides(tmp_path):
    store = conflict_store(tmp_path / "annotate.sqlite3")
    stub = StubCompareClient(VERDICT)
    decision = route(store)
    advisory = run_image_compare(store.db, store.settings, stub, decision)
    photos = [{"evidence_id": "ev_a", "file": "a.png"},
              {"evidence_id": "ev_b", "file": "b.png"},
              {"evidence_id": "ev_other", "file": "c.png"}]
    annotated = annotate_with_compare(photos, advisory)
    assert [photo["file"] for photo in annotated] == ["a.png", "b.png", "c.png"]
    assert annotated[0]["image_compare"]["same_subject"] is True
    assert annotated[0]["image_compare"]["differences"] == VERDICT["differences"]
    assert annotated[0]["image_compare"]["advisory"] is True
    assert annotated[1]["image_compare"]["same_subject"] is True
    assert "image_compare" not in annotated[2]
    # Inputs untouched.
    assert all("image_compare" not in photo for photo in photos)


def test_annotate_with_compare_ignores_refusals():
    photos = [{"evidence_id": "ev_a"}]
    annotated = annotate_with_compare(photos, {"status": "refused",
                                               "reason": "no_consent"})
    assert annotated == photos
    assert "image_compare" not in annotated[0]


def test_validate_empty_differences_allowed_only_when_same():
    from yaad.memory.multimodal import validate_compare_verdict
    identical = {"same_subject": True, "differences": [], "uncertainties": []}
    assert validate_compare_verdict(identical, ["a", "b"]).ok is True
    different = {"same_subject": False, "differences": [], "uncertainties": []}
    check = validate_compare_verdict(different, ["a", "b"])
    assert check.ok is False

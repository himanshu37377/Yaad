"""Joint image-compare wiring into the production answer path.

The compare stack (router + executor + annotation) existed with zero
production callers. answer_question must now run one advisory-only
joint compare when a photo-value question meets exactly one validated
image-vs-image conflict pair: annotated onto the gallery plus a
top-level display-only key, never resolving the conflict, fully
spend/consent gated, failing closed to the plain answer. Stub and
fake clients only; no network.
"""
import pytest
from test_cross_modal_conflicts import (
    IMG_A,
    IMG_B,
    ImageStore,
    conflict_store,
    image_candidate,
    supports,
)
from test_image_compare import VERDICT, FakeLocalClient, StubCompareClient

from yaad.memory.multimodal import EGRESS_PURPOSE, route_record_compare
from yaad.query.answer import answer_question

QUESTION = "What is the registration fee in the photos?"


def _ask(store, stub, question=QUESTION):
    import yaad.llm

    real = yaad.llm.get_client
    yaad.llm.get_client = lambda settings: stub
    try:
        return answer_question(store.db, question, store.folder_id,
                               store.settings)
    finally:
        yaad.llm.get_client = real


def _compare_receipts(store):
    with store.db.connect() as con:
        return [dict(row) for row in con.execute(
            "SELECT * FROM egress_log WHERE purpose=? ORDER BY id",
            (EGRESS_PURPOSE,))]


def _cache_rows(store):
    with store.db.connect() as con:
        return [dict(row) for row in con.execute("SELECT * FROM cache")]


def test_answer_attaches_compare_advisory_on_photo_conflict(tmp_path):
    store = conflict_store(tmp_path / "w1.sqlite3")
    stub = StubCompareClient(VERDICT)
    result = _ask(store, stub)
    assert result["status"] == "conflict"
    assert stub.calls and len(stub.calls) == 1
    assert _compare_receipts(store) and len(_compare_receipts(store)) == 1
    assert "image_compare" in result
    assert result["image_compare"]["advisory"] is True
    assert result["image_compare"]["record_ids"] == sorted(store.record_ids())
    assert set(result["image_compare"]["evidence_ids"]) == {"ev_a", "ev_b"}
    annotated = [photo for photo in result["photos"]
                 if photo.get("evidence_id") in ("ev_a", "ev_b")]
    assert len(annotated) == 2
    assert all(photo["image_compare"]["advisory"] is True for photo in annotated)
    assert {card["file"] for card in result["evidence"]} == {
        str(store.files_dir / "a.png"), str(store.files_dir / "b.png")}
    assert any(call["why"] == EGRESS_PURPOSE for call in result["disclosure"])


def test_compare_advisory_never_resolves_conflict(tmp_path):
    verdict = dict(VERDICT, same_subject=True)
    store = conflict_store(tmp_path / "w2.sqlite3")
    before = store.snapshot_tables(("memory_records", "memory_record_evidence",
                                    "photo_descriptions", "units", "files"))
    stub = StubCompareClient(verdict)
    result = _ask(store, stub)
    assert result["status"] == "conflict"
    assert result["image_compare"]["same_subject"] is True
    assert {card["id"] for card in result["evidence"]} == {"ev_a", "ev_b"}
    with store.db.connect() as con:
        assert con.execute("SELECT COUNT(*) AS n FROM memory_record_evidence "
                           "WHERE role='supersedes'").fetchone()["n"] == 0
    assert store.snapshot_tables(("memory_records", "memory_record_evidence",
                                  "photo_descriptions", "units", "files")) == before


def test_browse_branch_makes_zero_compare_calls(tmp_path):
    store = conflict_store(tmp_path / "w3.sqlite3")
    stub = StubCompareClient(VERDICT)
    result = _ask(store, stub, question="show photos")
    assert stub.calls == []
    assert "image_compare" not in result
    assert _compare_receipts(store) == []


def test_non_photo_conflict_makes_zero_calls(tmp_path):
    store = conflict_store(tmp_path / "w4.sqlite3")
    stub = StubCompareClient(VERDICT)
    result = _ask(store, stub, question="What is the registration fee?")
    assert result["status"] == "conflict"
    assert stub.calls == []
    assert "image_compare" not in result


def test_text_image_pair_refuses(tmp_path):
    store = ImageStore(tmp_path / "w5.sqlite3")
    store.add_image("a.png", "ev_a", IMG_A)
    store.add_text("note.txt", "ev_t",
                   "Autumn Fest registration fee is Rs 200 per person")
    store.add_records("run-1", [
        image_candidate("Autumn Fest registration fee", "registration fee",
                        [supports("ev_a", "Autumn Fest registration fee Rs 150")]),
        image_candidate("Autumn Fest registration fee", "registration fee",
                        [supports("ev_t", "Autumn Fest registration fee is Rs 200")]),
    ])
    from yaad.memory.records import mark_conflicts
    with store.db.transaction() as con:
        mark_conflicts(con, store.folder_id)
    ids = store.record_ids()
    with store.db.connect() as con:
        decision = route_record_compare(con, store.folder_id, ids[0], ids[1],
                                        store.settings)
    assert decision.fires is False
    assert decision.reason == "text_image_pair"
    stub = StubCompareClient(VERDICT)
    result = _ask(store, stub)
    assert stub.calls == []
    assert "image_compare" not in result


def test_unrelated_images_make_zero_calls(tmp_path):
    store = ImageStore(tmp_path / "w6.sqlite3")
    store.add_image("a.png", "ev_a", IMG_A)
    store.add_image("b.png", "ev_b",
                    "Description: Winter Gala poster on a wall\n"
                    "Visible text: Winter Gala volunteer dinner at 7pm")
    store.add_records("run-1", [
        image_candidate("Autumn Fest registration fee", "registration fee",
                        [supports("ev_a", "Autumn Fest registration fee Rs 150")]),
        image_candidate("Winter Gala volunteer dinner", "volunteer dinner",
                        [supports("ev_b", "Winter Gala volunteer dinner at 7pm")]),
    ])
    from yaad.memory.records import mark_conflicts
    with store.db.transaction() as con:
        mark_conflicts(con, store.folder_id)
    stub = StubCompareClient(VERDICT)
    result = _ask(store, stub)
    assert stub.calls == []
    assert "image_compare" not in result


def test_no_consent_refuses_cloud(tmp_path):
    store = conflict_store(tmp_path / "w7.sqlite3", consent=0,
                           cloud_enabled=True, provider="cloud")
    store.settings.model_api_key = "stub"
    stub = StubCompareClient(VERDICT)
    result = _ask(store, stub)
    assert stub.calls == []
    assert "image_compare" not in result
    # Without consent, memory is unusable and review is refused, so the
    # honest unavailability message stands exactly as without compare.
    assert result["status"] == "cloud_unavailable"


def test_cloud_disabled_refuses(tmp_path):
    store = conflict_store(tmp_path / "w8.sqlite3", consent=1,
                           cloud_enabled=False, provider="cloud")
    store.settings.model_api_key = "stub"
    stub = StubCompareClient(VERDICT)
    result = _ask(store, stub)
    assert stub.calls == []
    assert "image_compare" not in result


def test_global_cap_refuses_cloud(tmp_path):
    store = conflict_store(tmp_path / "w9.sqlite3", consent=1,
                           cloud_enabled=True, provider="cloud")
    store.settings.model_api_key = "stub"
    store.settings.cloud_spend_cap_usd = 0.25
    with store.db.transaction() as con:
        con.execute(
            "INSERT INTO egress_log(file_id,unit_id,purpose,provider,model,bytes,"
            "content_kind,status_code,success,estimated_cost_usd)"
            " VALUES(NULL,NULL,'prior spend','stub','stub',1,'test',200,1,10.0)")
    stub = StubCompareClient(VERDICT)
    result = _ask(store, stub)
    assert stub.calls == []
    assert "image_compare" not in result
    assert _compare_receipts(store) == []


def test_router_over_ceiling_refuses(tmp_path):
    store = conflict_store(tmp_path / "w9b.sqlite3", consent=1,
                           cloud_enabled=True, provider="cloud")
    ids = store.record_ids()
    with store.db.connect() as con:
        decision = route_record_compare(con, store.folder_id, ids[0], ids[1],
                                        store.settings,
                                        compare_max_spend_usd=0.000001)
    assert decision.fires is False
    assert decision.reason == "over_ceiling"


def test_cache_hit_makes_zero_new_calls(tmp_path):
    store = conflict_store(tmp_path / "w10.sqlite3")
    stub = StubCompareClient(VERDICT)
    first = _ask(store, stub)
    assert first["image_compare"]["cache_hit"] is False
    second = _ask(store, stub)
    assert second["image_compare"]["cache_hit"] is True
    assert len(stub.calls) == 1
    assert len(_compare_receipts(store)) == 1
    assert len(_cache_rows(store)) == 1


def test_ungrounded_verdict_fails_closed(tmp_path):
    store = conflict_store(tmp_path / "w11.sqlite3")
    stub = StubCompareClient({"same_subject": False,
                              "differences": ["quasar philately zebra"],
                              "uncertainties": []})
    result = _ask(store, stub)
    assert result["status"] == "conflict"
    assert "image_compare" not in result
    assert len(_compare_receipts(store)) == 1
    assert _cache_rows(store) == []


def test_two_conflict_pairs_skip_compare(tmp_path):
    store = ImageStore(tmp_path / "w12.sqlite3")
    store.add_image("a.png", "ev_a", IMG_A)
    store.add_image("b.png", "ev_b", IMG_B)
    spring_a = ("Description: Spring Fest poster on a wall\n"
                "Visible text: Spring Fest registration fee Rs 300 per person")
    spring_b = ("Description: Spring Fest notice board by the gate\n"
                "Visible text: Spring Fest registration fee Rs 400 per person")
    store.add_image("c.png", "ev_c", spring_a)
    store.add_image("d.png", "ev_d", spring_b)
    store.add_records("run-1", [
        image_candidate("Autumn Fest registration fee", "autumn registration fee",
                        [supports("ev_a", "Autumn Fest registration fee Rs 150")]),
        image_candidate("Autumn Fest registration fee", "autumn registration fee",
                        [supports("ev_b", "Autumn Fest registration fee Rs 200")]),
        image_candidate("Spring Fest registration fee", "spring registration fee",
                        [supports("ev_c", "Spring Fest registration fee Rs 300")]),
        image_candidate("Spring Fest registration fee", "spring registration fee",
                        [supports("ev_d", "Spring Fest registration fee Rs 400")]),
    ])
    from yaad.memory.records import mark_conflicts
    with store.db.transaction() as con:
        pairs = mark_conflicts(con, store.folder_id)
    assert len(pairs) == 2
    stub = StubCompareClient(VERDICT)
    result = _ask(store, stub)
    assert result["status"] == "conflict"
    assert stub.calls == []
    assert "image_compare" not in result


def test_local_mode_compare_costs_zero(tmp_path):
    store = conflict_store(tmp_path / "w13.sqlite3")
    fake = FakeLocalClient(VERDICT)
    result = _ask(store, fake)
    assert result["status"] == "conflict"
    assert result["image_compare"]["advisory"] is True
    assert len(fake.calls) == 1
    receipts = _compare_receipts(store)
    assert len(receipts) == 1
    assert receipts[0]["provider"] == "local"
    assert receipts[0]["estimated_cost_usd"] == 0.0


def test_compare_exception_keeps_plain_answer(tmp_path):
    class RaisingStub(StubCompareClient):
        def compare_images(self, a_bytes, b_bytes, schema, prompt_id):
            raise RuntimeError("boom")

    store = conflict_store(tmp_path / "w14.sqlite3")
    result = _ask(store, RaisingStub(VERDICT))
    assert "image_compare" not in result
    assert _compare_receipts(store) == []
    assert _cache_rows(store) == []
    # The failed attempt wrote nothing, so the same store re-asked
    # with a working client shows the annotated twin to diff against.
    expected = _ask(store, StubCompareClient(VERDICT))
    # Same conflict served with and without the advisory: strip only
    # the compare-owned key and volatile disclosure timestamps.
    got = dict(result)
    want = dict(expected)
    want.pop("image_compare")
    for photo in want.get("photos", []):
        photo.pop("image_compare", None)
    for key in ("disclosure",):
        got.pop(key, None)
        want.pop(key, None)
    assert got["status"] == want["status"] == "conflict"
    assert got["answer"] == want["answer"]
    assert got["evidence"] == want["evidence"]
    assert got["candidates"] == want["candidates"]
    assert got["memory"] == want["memory"]
    assert got["photos"] == want["photos"]

"""Cross-modal conflict detection on the real answer path (offline).

Conflicting values from different modalities (PDF page vs image
understanding, audio transcript vs text file) must surface as
conflicts with both sources cited -- through the same memory,
validation, and conflict machinery as same-modality rivals.
Deterministic fixtures; no model calls.
"""
from io import BytesIO
import json

import pytest
from PIL import Image, ImageDraw
from test_memory_answers import Store, candidate

from yaad.config import Settings
from yaad.db import Database
from yaad.llm.meta_client import JOINT_IMAGE_COMPARISON_SCHEMA, MetaClient
from yaad.memory.multimodal import (
    COMPARE_MAX_SPEND_USD_DEFAULT,
    CompareEdge,
    CompareGraph,
    compare_graph_for_event_graph,
    route_graph_compare,
    route_record_compare,
)
from yaad.memory.records import mark_conflicts, upsert_records


def _png_bytes(label: str) -> bytes:
    img = Image.new("RGB", (120, 40), "white")
    ImageDraw.Draw(img).text((5, 5), label, fill="black")
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_compare_images_sends_single_joint_request(monkeypatch):
    """Joint comparison is one strict request carrying both images --
    the verdict is cross-image reasoning, not stitched captions."""
    seen = {}

    def fake_responses(self, payload, timeout=90):
        seen.update(payload)
        return {"output_text": '{"same_subject": false, "image_a_summary": "A", '
                               '"image_b_summary": "B", "differences": ["venue"]}'}

    monkeypatch.setattr(MetaClient, "responses", fake_responses)
    client = MetaClient(Settings(model_api_key="stub", cloud_enabled=True))
    result = client.compare_images(_png_bytes("A"), _png_bytes("B"))
    assert result["same_subject"] is False
    assert result["differences"] == ["venue"]
    blocks = seen["input"][0]["content"]
    images = [block for block in blocks if block["type"] == "input_image"]
    assert len(images) == 2
    assert all(block["image_url"].startswith("data:image/png;base64,") for block in images)
    text_format = seen["text"]["format"]
    assert text_format["strict"] is True
    assert text_format["schema"] == JOINT_IMAGE_COMPARISON_SCHEMA


def test_compare_images_accepts_custom_schema(monkeypatch):
    custom = {"type": "object", "properties": {"same_venue": {"type": "boolean"}},
              "required": ["same_venue"], "additionalProperties": False}
    seen = {}

    def fake_responses(self, payload, timeout=90):
        seen.update(payload)
        return {"output_text": '{"same_venue": false}'}

    monkeypatch.setattr(MetaClient, "responses", fake_responses)
    client = MetaClient(Settings(model_api_key="stub", cloud_enabled=True))
    assert client.compare_images(_png_bytes("A"), _png_bytes("B"),
                                 schema=custom) == {"same_venue": False}
    assert seen["text"]["format"]["schema"] == custom


def _add_unit(store, name, file_kind, unit_kind, locator, eid, text):
    with store.db.transaction() as con:
        con.execute("INSERT INTO files(folder_id,path,sha256,kind,size,mtime,status,cloud_consent)"
                    " VALUES(?,?,?,?,?,?,?,?)",
                    (store.folder_id, f"/src/{name}", "ab" * 32, file_kind,
                     10, 0.0, "indexed", 1))
        file_id = con.execute("SELECT id FROM files WHERE path=?",
                              (f"/src/{name}",)).fetchone()["id"]
        con.execute("INSERT INTO units(file_id,evidence_id,kind,locator,text) VALUES(?,?,?,?,?)",
                    (file_id, eid, unit_kind, locator, text))
        store.unit_texts[eid] = text


def test_pdf_vs_image_venue_conflict(tmp_path):
    store = Store(tmp_path / "xm1.sqlite3")
    _add_unit(store, "proposal.pdf", "pdf", "text",
              '{"type":"pdf_page","page":1}', "ev_pdf",
              "Council booked Riverside Hall for Spring Fest")
    _add_unit(store, "poster.jpg", "image", "image_understanding",
              '{"type":"image"}', "ev_img",
              "Description: Council Spring Fest poster shows venue is Grand Arena on March 3rd")
    store.add_records("run-1", [
        candidate("Council Spring Fest venue Riverside Hall", "spring fest",
                  [("ev_pdf", "Council booked Riverside Hall for Spring Fest")],
                  record_type="decision", status="decided", date_value=None, date_raw=None,
                  detail="Council booked Riverside Hall for Spring Fest"),
        candidate("Council Spring Fest poster venue Grand Arena", "spring fest",
                  [("ev_img", "Council Spring Fest poster shows venue is Grand Arena on March 3rd")],
                  record_type="decision", status="decided",
                  date_value="2026-03-03", date_raw="March 3rd",
                  detail="Council Spring Fest poster shows venue is Grand Arena"),
    ])
    with store.db.transaction() as con:
        mark_conflicts(con, store.folder_id)
    result = store.ask("What is the Spring Fest venue?")
    assert result["status"] == "conflict"
    files = {card["file"] for card in result["evidence"]}
    assert files == {"/src/proposal.pdf", "/src/poster.jpg"}
    assert "Riverside Hall" in result["answer"] and "Grand Arena" in result["answer"]


def test_audio_vs_text_venue_conflict(tmp_path):
    store = Store(tmp_path / "xm2.sqlite3")
    store.add_file("notes.txt",
                   [("ev_txt", "Council booked Riverside Hall for Spring Fest")])
    _add_unit(store, "brief.wav", "audio", "text",
              '{"type":"audio_segment","start":0,"end":30}', "ev_wav",
              "Council organizer confirms Spring Fest venue moved to Grand Arena on March 3rd")
    store.add_records("run-1", [
        candidate("Council Spring Fest venue Riverside Hall", "spring fest",
                  [("ev_txt", "Council booked Riverside Hall for Spring Fest")],
                  record_type="decision", status="decided", date_value=None, date_raw=None,
                  detail="Council booked Riverside Hall for Spring Fest"),
        candidate("Council Spring Fest venue moved Grand Arena", "spring fest",
                  [("ev_wav", "Council organizer confirms Spring Fest venue moved to Grand Arena on March 3rd")],
                  record_type="decision", status="decided",
                  date_value="2026-03-03", date_raw="March 3rd",
                  detail="Council organizer confirms Spring Fest venue moved to Grand Arena"),
    ])
    with store.db.transaction() as con:
        mark_conflicts(con, store.folder_id)
    result = store.ask("What is the Spring Fest venue?")
    assert result["status"] == "conflict"
    files = {card["file"] for card in result["evidence"]}
    assert files == {"/src/notes.txt", "/src/brief.wav"}


# --- Query-time joint image-compare router gates (offline) ---


IMG_A = ("Description: Autumn Fest poster on a wall\n"
         "Visible text: Autumn Fest registration fee Rs 150 per person")
IMG_B = ("Description: Autumn Fest notice board by the gate\n"
         "Visible text: Autumn Fest registration fee Rs 200 per person")
IMG_SPRING = ("Description: Spring Fest poster on a wall\n"
              "Visible text: Spring Fest registration fee Rs 200 per person")
TXT_C = "Autumn Fest registration fee is Rs 150 per person"


def image_candidate(title, key_phrase, evidence, **over):
    base = {"record_type": "decision", "title": title, "detail": title,
            "date_value": None, "date_raw": None, "assignee": None,
            "status": "decided", "statement_kind": "explicit",
            "key_phrase": key_phrase, "evidence": evidence}
    base.update(over)
    return base


def supports(eid, excerpt):
    return {"evidence_id": eid, "role": "supports", "excerpt": excerpt}


class ImageStore:
    """Disposable db with real image bytes on disk and image records."""

    def __init__(self, path, consent=1, cloud_enabled=False, provider="local"):
        self.files_dir = path.parent / (path.stem + "_files")
        self.files_dir.mkdir(parents=True, exist_ok=True)
        self.db = Database(path)
        self.settings = Settings(db_path=path, cloud_enabled=cloud_enabled,
                                 model_provider=provider)
        self.unit_texts: dict[str, str] = {}
        with self.db.transaction() as con:
            con.execute("INSERT INTO folders(path,cloud_consent) VALUES(?,?)",
                        (str(self.files_dir), consent))
            self.folder_id = con.execute("SELECT id FROM folders").fetchone()["id"]

    def add_image(self, name, eid, text, visible_text=None, size=None):
        from io import BytesIO
        from PIL import Image
        shade = abs(hash(name)) % 256
        buffer = BytesIO()
        Image.new("RGB", (8, 8), (shade, shade, shade)).save(buffer, format="PNG")
        blob = buffer.getvalue()
        disk = self.files_dir / name
        disk.write_bytes(blob)
        with self.db.transaction() as con:
            con.execute("INSERT INTO files(folder_id,path,sha256,kind,size,mtime,status,cloud_consent)"
                        " VALUES(?,?,?,?,?,?,?,?)",
                        (self.folder_id, str(disk), "0" * 64, "image",
                         len(blob) if size is None else size, 0.0, "indexed", 1))
            file_id = con.execute("SELECT id FROM files WHERE path=?",
                                  (str(disk),)).fetchone()["id"]
            con.execute("INSERT INTO units(file_id,evidence_id,kind,locator,text)"
                        " VALUES(?,?,?,?,?)",
                        (file_id, eid, "image_understanding",
                         json.dumps({"type": "image"}), text))
            caption = text.splitlines()[0] if text else ""
            con.execute("INSERT INTO photo_descriptions(file_id,schema_version,caption,visible_text)"
                        " VALUES(?,3,?,?)",
                        (file_id, caption,
                         text if visible_text is None else visible_text))
            self.unit_texts[eid] = text
        return file_id

    def add_text(self, name, eid, text):
        with self.db.transaction() as con:
            con.execute("INSERT INTO files(folder_id,path,sha256,kind,size,mtime,status,cloud_consent)"
                        " VALUES(?,?,?,?,?,?,?,?)",
                        (self.folder_id, f"/src/{name}", "0" * 64, "text",
                         len(text), 0.0, "indexed", 1))
            file_id = con.execute("SELECT id FROM files WHERE path=?",
                                  (f"/src/{name}",)).fetchone()["id"]
            con.execute("INSERT INTO units(file_id,evidence_id,kind,locator,text)"
                        " VALUES(?,?,?,?,?)",
                        (file_id, eid, "text_span",
                         json.dumps({"type": "text_span"}), text))
            self.unit_texts[eid] = text
        return file_id

    def add_records(self, run_id, candidates):
        with self.db.transaction() as con:
            return upsert_records(con, self.folder_id, run_id, candidates,
                                  dict(self.unit_texts))

    def record_ids(self):
        with self.db.connect() as con:
            return [row["id"] for row in
                    con.execute("SELECT id FROM memory_records ORDER BY id")]

    def snapshot_tables(self, tables):
        with self.db.connect() as con:
            return {table: [dict(row) for row in con.execute(f"SELECT * FROM {table}")]
                    for table in tables}


def conflict_store(path, **store_kwargs):
    """Two image records, same matter, rival amounts: a genuine
    mark_conflicts-supported image-image conflict."""
    store = ImageStore(path, **store_kwargs)
    store.add_image("a.png", "ev_a", IMG_A)
    store.add_image("b.png", "ev_b", IMG_B)
    store.add_records("run-1", [
        image_candidate("Autumn Fest registration fee", "registration fee",
                        [supports("ev_a", "Autumn Fest registration fee Rs 150")]),
        image_candidate("Autumn Fest registration fee", "registration fee",
                        [supports("ev_b", "Autumn Fest registration fee Rs 200")]),
    ])
    with store.db.transaction() as con:
        pairs = mark_conflicts(con, store.folder_id)
    assert len(pairs) == 1
    return store


def route(store, left=None, right=None, **kwargs):
    ids = store.record_ids()
    left = ids[0] if left is None else left
    right = ids[1] if right is None else right
    with store.db.connect() as con:
        return route_record_compare(con, store.folder_id, left, right,
                                    store.settings, **kwargs)


# --- fires: valid image-image conflict + uncertainty ---

def test_router_fires_on_validated_image_conflict_local(tmp_path):
    store = conflict_store(tmp_path / "fire.sqlite3")
    before = store.snapshot_tables(("memory_records", "memory_record_evidence",
                                    "photo_descriptions", "units", "files"))
    decision = route(store)
    assert decision.fires is True
    assert decision.reason == "fires"
    assert decision.left is not None and decision.right is not None
    assert decision.left.file_id != decision.right.file_id
    assert decision.estimated_cost_usd == 0.0
    # The router is read-only: gating changes nothing.
    assert store.snapshot_tables(("memory_records", "memory_record_evidence",
                                  "photo_descriptions", "units", "files")) == before


def test_router_fires_cloud_with_consent_and_enabled(tmp_path):
    store = conflict_store(tmp_path / "fire-cloud.sqlite3", consent=1,
                           cloud_enabled=True, provider="cloud")
    decision = route(store)
    assert decision.fires is True
    assert decision.reason == "fires"
    assert 0.0 < decision.estimated_cost_usd < COMPARE_MAX_SPEND_USD_DEFAULT


def test_router_fires_without_prior_marking_when_validated(tmp_path):
    """Like the serving-time promotion path, query-time validation (not
    the stored status) is what counts: rivals validate even before any
    sweep marks them."""
    store = ImageStore(tmp_path / "unmarked.sqlite3")
    store.add_image("a.png", "ev_a", IMG_A)
    store.add_image("b.png", "ev_b", IMG_B)
    store.add_records("run-1", [
        image_candidate("Autumn Fest registration fee", "registration fee",
                        [supports("ev_a", "Autumn Fest registration fee Rs 150")]),
        image_candidate("Autumn Fest registration fee", "registration fee",
                        [supports("ev_b", "Autumn Fest registration fee Rs 200")]),
    ])
    decision = route(store)
    assert (decision.fires, decision.reason) == (True, "fires")


def test_graph_branch_fires_on_contradicts_plus_uncertainty(tmp_path):
    store = conflict_store(tmp_path / "graph.sqlite3")
    left, right = store.record_ids()
    graph = CompareGraph(nodes=(left, right),
                       edges=(CompareEdge(left, right, "contradicts"),),
                       uncertainties=("rival_values_without_supersession",))
    with store.db.connect() as con:
        decision = route_graph_compare(con, graph, store.folder_id, store.settings)
    assert (decision.fires, decision.reason) == (True, "fires")
    assert decision.left.file_id != decision.right.file_id


def test_graph_branch_accepts_phrased_uncertainty(tmp_path):
    store = conflict_store(tmp_path / "graph-phrase.sqlite3")
    left, right = store.record_ids()
    graph = CompareGraph(nodes=(left, right),
                       edges=(CompareEdge(right, left, "contradicts"),),
                       uncertainties=("Rival values without supersession",))
    with store.db.connect() as con:
        decision = route_graph_compare(con, graph, store.folder_id, store.settings)
    assert (decision.fires, decision.reason) == (True, "fires")


# --- rejects ---

def test_rejects_text_vs_image_pair(tmp_path):
    store = ImageStore(tmp_path / "text-image.sqlite3")
    store.add_text("c.txt", "ev_c", TXT_C)
    store.add_image("b.png", "ev_b", IMG_B)
    store.add_records("run-1", [
        image_candidate("Autumn Fest registration fee", "registration fee",
                        [supports("ev_c", "Autumn Fest registration fee is Rs 150")]),
        image_candidate("Autumn Fest registration fee", "registration fee",
                        [supports("ev_b", "Autumn Fest registration fee Rs 200")]),
    ])
    decision = route(store)
    assert decision.fires is False
    assert decision.reason == "text_image_pair"


def test_rejects_three_images(tmp_path):
    store = ImageStore(tmp_path / "three.sqlite3")
    store.add_image("a.png", "ev_a", IMG_A)
    store.add_image("b.png", "ev_b", IMG_B)
    store.add_image("c.png", "ev_c", IMG_B)
    store.add_records("run-1", [
        image_candidate("Autumn Fest registration fee", "registration fee",
                        [supports("ev_a", "Autumn Fest registration fee Rs 150")]),
        image_candidate("Autumn Fest registration fee", "registration fee",
                        [supports("ev_b", "Autumn Fest registration fee Rs 200"),
                         supports("ev_c", "Autumn Fest registration fee Rs 200")]),
    ])
    decision = route(store)
    assert decision.fires is False
    assert decision.reason == "too_many_images"


def test_rejects_pair_resolved_by_supersedes_edge(tmp_path):
    store = ImageStore(tmp_path / "superseded-edge.sqlite3")
    store.add_image("a.png", "ev_a", IMG_A)
    store.add_image("b.png", "ev_b", IMG_B)
    store.add_records("run-1", [
        image_candidate("Autumn Fest registration fee", "registration fee",
                        [supports("ev_a", "Autumn Fest registration fee Rs 150")]),
        image_candidate("Autumn Fest registration fee", "registration fee",
                        [supports("ev_b", "Autumn Fest registration fee Rs 200"),
                         {"evidence_id": "ev_a", "role": "supersedes",
                          "excerpt": "Autumn Fest registration fee Rs 150"}]),
    ])
    decision = route(store)
    assert decision.fires is False
    assert decision.reason == "superseded"


def test_rejects_pair_with_superseded_status(tmp_path):
    store = conflict_store(tmp_path / "superseded-status.sqlite3")
    left, _ = store.record_ids()
    with store.db.transaction() as con:
        con.execute("UPDATE memory_records SET status='superseded' WHERE id=?", (left,))
    decision = route(store)
    assert decision.fires is False
    assert decision.reason == "superseded"


def test_rejects_without_consent_cloud(tmp_path):
    store = conflict_store(tmp_path / "no-consent.sqlite3", consent=0,
                           cloud_enabled=True, provider="cloud")
    decision = route(store)
    assert decision.fires is False
    assert decision.reason == "no_consent"


def test_rejects_when_cloud_disabled(tmp_path):
    store = conflict_store(tmp_path / "cloud-off.sqlite3", consent=1,
                           cloud_enabled=False, provider="cloud")
    decision = route(store)
    assert decision.fires is False
    assert decision.reason == "no_consent"


def test_local_mode_allowed_without_consent_or_cloud(tmp_path):
    store = conflict_store(tmp_path / "local-free.sqlite3", consent=0,
                           cloud_enabled=False, provider="local")
    decision = route(store)
    assert (decision.fires, decision.reason) == (True, "fires")
    assert decision.estimated_cost_usd == 0.0


def test_rejects_over_per_answer_ceiling(tmp_path):
    store = conflict_store(tmp_path / "ceiling.sqlite3", consent=1,
                           cloud_enabled=True, provider="cloud")
    decision = route(store, compare_max_spend_usd=0.000001)
    assert decision.fires is False
    assert decision.reason == "over_ceiling"
    assert decision.estimated_cost_usd > 0.000001


def test_default_caps_report_over_ceiling_first(tmp_path):
    """An estimate blowing past both caps reports the tighter
    per-answer ceiling: the compare-specific gate binds first.
    Gating prices the bytes actually sent (post-thumbnail), so the
    test writes genuinely large images rather than pinning a size
    column."""
    import os
    from PIL import Image
    store = ImageStore(tmp_path / "big.sqlite3", consent=1,
                       cloud_enabled=True, provider="cloud")
    store.add_image("a.png", "ev_a", IMG_A)
    store.add_image("b.png", "ev_b", IMG_B)
    for name in ("a.png", "b.png"):
        noise = Image.frombytes("RGB", (1400, 1400), os.urandom(1400 * 1400 * 3))
        noise.save(store.files_dir / name, format="PNG")
    store.add_records("run-1", [
        image_candidate("Autumn Fest registration fee", "registration fee",
                        [supports("ev_a", "Autumn Fest registration fee Rs 150")]),
        image_candidate("Autumn Fest registration fee", "registration fee",
                        [supports("ev_b", "Autumn Fest registration fee Rs 200")]),
    ])
    decision = route(store)
    assert decision.fires is False
    assert decision.reason == "over_ceiling"


def test_rejects_on_global_spend_refusal(tmp_path):
    store = conflict_store(tmp_path / "global.sqlite3", consent=1,
                           cloud_enabled=True, provider="cloud")
    decision = route(store, global_cap_usd=0.000001)
    assert decision.fires is False
    assert decision.reason == "spend_refusal"


def test_rejects_matter_mismatch(tmp_path):
    store = ImageStore(tmp_path / "matter.sqlite3")
    store.add_image("a.png", "ev_a", IMG_A)
    store.add_image("s.png", "ev_s", IMG_SPRING)
    store.add_records("run-1", [
        image_candidate("Autumn Fest registration fee", "registration fee",
                        [supports("ev_a", "Autumn Fest registration fee Rs 150")]),
        image_candidate("Spring Fest registration", "spring registration",
                        [supports("ev_s", "Spring Fest registration fee")]),
    ])
    decision = route(store)
    assert decision.fires is False
    assert decision.reason == "matter_mismatch"


def test_rejects_unvalidated_pair(tmp_path):
    """Same matter, but different events: validation contradicts the
    conflict, so there is nothing to compare."""
    store = ImageStore(tmp_path / "unvalidated.sqlite3")
    store.add_image("a.png", "ev_a", IMG_A)
    store.add_image("s.png", "ev_s", IMG_SPRING)
    store.add_records("run-1", [
        image_candidate("Autumn Fest registration fee", "registration fee",
                        [supports("ev_a", "Autumn Fest registration fee Rs 150")]),
        image_candidate("Spring Fest registration fee", "registration fee",
                        [supports("ev_s", "Spring Fest registration fee Rs 200")]),
    ])
    decision = route(store)
    assert decision.fires is False
    assert decision.reason == "not_validated"


def test_rejects_unknown_records(tmp_path):
    store = conflict_store(tmp_path / "unknown.sqlite3")
    with store.db.connect() as con:
        decision = route_record_compare(con, store.folder_id, 999, 1000,
                                        store.settings)
    assert decision.fires is False
    assert decision.reason == "unknown_record"


def test_rejects_single_image_cited_twice(tmp_path):
    store = ImageStore(tmp_path / "one-image.sqlite3")
    store.add_image("a.png", "ev_a", IMG_A)
    store.add_records("run-1", [
        image_candidate("Autumn Fest registration fee", "registration fee",
                        [supports("ev_a", "Autumn Fest registration fee Rs 150")]),
        image_candidate("Autumn Fest registration fee", "registration fee",
                        [supports("ev_a", "Autumn Fest registration fee Rs 150")],
                        record_type="commitment", status="open"),
    ])
    decision = route(store)
    assert decision.fires is False
    assert decision.reason == "not_image_pair"


def test_graph_rejects_without_contradicts_edge(tmp_path):
    store = conflict_store(tmp_path / "graph-noedge.sqlite3")
    left, right = store.record_ids()
    graph = CompareGraph(nodes=(left, right), edges=(),
                       uncertainties=("rival_values_without_supersession",))
    with store.db.connect() as con:
        decision = route_graph_compare(con, graph, store.folder_id, store.settings)
    assert decision.fires is False
    assert decision.reason == "not_validated"


def test_graph_rejects_without_uncertainty(tmp_path):
    store = conflict_store(tmp_path / "graph-nounc.sqlite3")
    left, right = store.record_ids()
    graph = CompareGraph(nodes=(left, right),
                       edges=(CompareEdge(left, right, "contradicts"),),
                       uncertainties=())
    with store.db.connect() as con:
        decision = route_graph_compare(con, graph, store.folder_id, store.settings)
    assert decision.fires is False
    assert decision.reason == "no_uncertainty"


def test_graph_rejects_supersedes_edge(tmp_path):
    store = conflict_store(tmp_path / "graph-sup.sqlite3")
    left, right = store.record_ids()
    graph = CompareGraph(nodes=(left, right),
                       edges=(CompareEdge(left, right, "contradicts"),
                              CompareEdge(left, right, "supersedes")),
                       uncertainties=("rival_values_without_supersession",))
    with store.db.connect() as con:
        decision = route_graph_compare(con, graph, store.folder_id, store.settings)
    assert decision.fires is False
    assert decision.reason == "superseded"


def test_graph_rejects_three_nodes(tmp_path):
    store = conflict_store(tmp_path / "graph-three.sqlite3")
    left, right = store.record_ids()
    graph = CompareGraph(nodes=(left, right, 999),
                       edges=(CompareEdge(left, right, "contradicts"),),
                       uncertainties=("rival_values_without_supersession",))
    with store.db.connect() as con:
        decision = route_graph_compare(con, graph, store.folder_id, store.settings)
    assert decision.fires is False
    assert decision.reason == "too_many_images"


def test_event_graph_adapter_routes_real_image_conflict(tmp_path):
    from yaad.memory.multimodal import build_event_graphs
    store = conflict_store(tmp_path / "adapter.sqlite3")
    with store.db.connect() as con:
        graphs = build_event_graphs(con, store.folder_id)
    assert len(graphs) == 1
    graph = next(iter(graphs.values()))
    assert any(e.kind == "contradicts" for e in graph.edges)
    adapted = compare_graph_for_event_graph(graph)
    assert len(adapted.nodes) == 2
    with store.db.connect() as con:
        decision = route_graph_compare(con, adapted, store.folder_id, store.settings)
    assert (decision.fires, decision.reason) == (True, "fires")


def test_spend_gate_prices_thumbnails_not_files(tmp_path):
    """Gating prices the bytes actually sent. With a ceiling between
    the file-size estimate and the thumbnail estimate, a large image
    pair still fires (file-size pricing would wrongly refuse it)."""
    import os
    from PIL import Image
    from yaad.memory.multimodal import (
        estimate_compare_cost_usd,
        thumb_bytes,
    )
    store = ImageStore(tmp_path / "thumb.sqlite3", consent=1,
                       cloud_enabled=True, provider="cloud")
    store.add_image("a.png", "ev_a", IMG_A)
    store.add_image("b.png", "ev_b", IMG_B)
    for name in ("a.png", "b.png"):
        big = Image.frombytes("RGB", (2000, 2000), os.urandom(2000 * 2000 * 3))
        big.save(store.files_dir / name, format="PNG")
    store.add_records("run-1", [
        image_candidate("Autumn Fest registration fee", "registration fee",
                        [supports("ev_a", "Autumn Fest registration fee Rs 150")]),
        image_candidate("Autumn Fest registration fee", "registration fee",
                        [supports("ev_b", "Autumn Fest registration fee Rs 200")]),
    ])
    file_est = estimate_compare_cost_usd(
        store.settings,
        (store.files_dir / "a.png").stat().st_size,
        (store.files_dir / "b.png").stat().st_size)
    thumb_est = estimate_compare_cost_usd(
        store.settings,
        len(thumb_bytes((store.files_dir / "a.png").read_bytes())),
        len(thumb_bytes((store.files_dir / "b.png").read_bytes())))
    assert thumb_est < file_est  # thumbnails actually shrink noise
    mid = (thumb_est + file_est) / 2
    decision = route(store, compare_max_spend_usd=mid, global_cap_usd=10.0)
    assert (decision.fires, decision.reason) == (True, "fires")
    assert decision.estimated_cost_usd == pytest.approx(thumb_est)
    refused = route(store, compare_max_spend_usd=thumb_est / 2, global_cap_usd=10.0)
    assert (refused.fires, refused.reason) == (False, "over_ceiling")


def test_router_refuses_agreement_without_rivalry(tmp_path):
    """Same matter, same value, no rival amounts or dates: validation
    cannot support a conflict, so the router refuses instead of
    manufacturing one. (Separate record_keys via explicit/proposal
    kinds; same matter key.)"""
    store = ImageStore(tmp_path / "agree.sqlite3")
    store.add_image("a.png", "ev_a", IMG_A)
    store.add_image("b.png", "ev_b", IMG_B)
    store.add_records("run-1", [
        image_candidate("Autumn Fest budget", "autumn fest",
                        [supports("ev_a", "Autumn Fest poster on a wall")],
                        record_type="commitment", status="open",
                        statement_kind="explicit"),
        image_candidate("Autumn Fest budget", "autumn fest",
                        [supports("ev_b", "Autumn Fest notice board by the gate")],
                        record_type="commitment", status="open",
                        statement_kind="proposal"),
    ])
    decision = route(store)
    assert decision.fires is False
    assert decision.reason == "not_validated"

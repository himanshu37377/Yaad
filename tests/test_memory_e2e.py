"""Final E2E verification: formats, consent, fallback, contract readiness.

Deterministic fixtures, offline answers, no model spend. Locks the
conservative behaviors: format variants conflict rather than merge, revoked
evidence is unusable in answers while staying inspectable, ineligible
records leave units-only behavior byte-identical, and the response contract
carries record IDs, claim qualifications, and citation attribution for UI.
"""
import os
import tempfile
from pathlib import Path

os.environ.setdefault("YAAD_DB_PATH", str(Path(tempfile.gettempdir()) / "yaad-memory-e2e-tests.sqlite3"))

from test_memory_answers import Store, candidate

from yaad.api import main
from yaad.config import Settings
from yaad.query.answer import answer_question


def check_citation_integrity(store, result):
    card_ids = {card["id"] for card in result["evidence"]}
    for value in result["values"]:
        assert set(value["evidence_ids"]) <= card_ids
    for claim in result["memory"]["claims"]:
        assert set(claim["evidence_ids"]) <= card_ids
    for pair in result["memory"]["conflicts"]:
        for claim in pair:
            assert set(claim["evidence_ids"]) <= card_ids
    with store.db.connect() as con:
        for eid in card_ids:
            assert con.execute("SELECT 1 FROM units WHERE evidence_id=?", (eid,)).fetchone()


def test_same_date_different_formats_conflicts_conservatively(tmp_path):
    store = Store(tmp_path / "fmt.sqlite3")
    store.add_file("a.txt", [("ev_a", "The draft is due March 3rd")])
    store.add_file("b.txt", [("ev_b", "Draft deadline recorded as 2026-03-03")])
    store.add_records("run-1", [candidate("Draft due March 3rd", "draft due",
                                          [("ev_a", "draft is due March 3rd")])])
    result = store.ask("when is the draft due?")
    # Same calendar date, different surface forms: the established rule sees
    # distinct values across files, so this stays a conflict offline rather
    # than a silently merged settled fact (semantic review resolves it online).
    assert result["status"] == "conflict"
    assert result["memory"]["used"] is True
    assert {card["id"] for card in result["evidence"]} == {"ev_a", "ev_b"}
    check_citation_integrity(store, result)


def test_same_amount_different_formats_conflicts_conservatively(tmp_path):
    store = Store(tmp_path / "amt.sqlite3")
    store.add_file("a.txt", [("ev_a", "Registration fee is Rs 150 per head")])
    store.add_file("b.txt", [("ev_b", "Fee fixed at \u20b9150")])
    store.add_records("run-1", [candidate(
        "Registration fee Rs 150", "registration fee",
        [("ev_a", "Registration fee is Rs 150")],
        record_type="decision", status="decided", date_value=None, date_raw=None)])
    result = store.ask("what is the registration fee?")
    assert result["status"] == "conflict"
    assert result["memory"]["used"] is True
    assert {card["id"] for card in result["evidence"]} == {"ev_a", "ev_b"}
    check_citation_integrity(store, result)


def test_genuinely_disagreeing_values_never_settle(tmp_path):
    store = Store(tmp_path / "dis.sqlite3")
    store.add_file("a.txt", [("ev_a", "The draft is due March 3rd")])
    store.add_file("b.txt", [("ev_b", "The draft is due April 4th")])
    store.add_records("run-1", [candidate("Draft due March 3rd", "draft due",
                                          [("ev_a", "draft is due March 3rd")])])
    result = store.ask("when is the draft due?")
    assert result["status"] == "conflict"
    assert "March 3rd" in result["answer"] or result["values"]
    check_citation_integrity(store, result)


def test_partial_consent_serves_only_authorized_citations(tmp_path, monkeypatch):
    store = Store(tmp_path / "pc.sqlite3")
    store.add_file("a.txt", [("ev_a", "The draft is due March 3rd")])
    store.add_file("b.txt", [("ev_b", "Draft due March 3rd, all teams")])
    store.add_records("run-1", [candidate(
        "Draft due March 3rd", "draft due",
        [("ev_a", "draft is due March 3rd"), ("ev_b", "Draft due March 3rd")])])
    with store.db.transaction() as con:
        con.execute("UPDATE files SET cloud_consent=0 WHERE path='/src/b.txt'")
    result = store.ask("when is the draft due?")
    assert result["memory"]["used"] is True
    assert {card["id"] for card in result["evidence"]} == {"ev_a"}
    # Historical provenance stays inspectable but cannot answer.
    monkeypatch.setattr(main, "db", store.db)
    listed = main.list_memory(folder_id=store.folder_id)["records"][0]
    assert {item["evidence_id"] for item in listed["evidence"]} == {"ev_a", "ev_b"}
    assert (listed["resolving_supports"], listed["authorized_supports"]) == (2, 1)
    assert listed["serving"]["verdict"] == "eligible"
    with store.db.transaction() as con:
        con.execute("UPDATE files SET cloud_consent=0")
    revoked = store.ask("when is the draft due?")
    assert revoked["memory"]["used"] is False
    relisted = main.list_memory(folder_id=store.folder_id)["records"][0]
    assert relisted["serving"]["verdict"] == "excluded"
    assert "consent" in relisted["serving"]["reason"]


def test_consent_restore_reenables_memory_without_duplicates(tmp_path):
    store = Store(tmp_path / "restore.sqlite3")
    store.add_file("a.txt", [("ev_a", "The draft is due March 3rd")])
    store.add_records("run-1", [candidate(
        "Draft due March 3rd", "draft due", [("ev_a", "draft is due March 3rd")])])
    assert store.ask("when is the draft due?")["memory"]["used"] is True
    with store.db.transaction() as con:
        con.execute("UPDATE folders SET cloud_consent=0 WHERE id=?", (store.folder_id,))
    assert store.ask("when is the draft due?")["memory"]["used"] is False
    with store.db.transaction() as con:
        con.execute("UPDATE folders SET cloud_consent=1 WHERE id=?", (store.folder_id,))
    result = store.ask("when is the draft due?")
    assert result["memory"]["used"] is True
    assert result["status"] == "answered"
    with store.db.connect() as con:
        assert con.execute("SELECT COUNT(*) n FROM memory_records").fetchone()["n"] == 1


def test_ineligible_records_leave_units_behavior_identical(tmp_path):
    left = Store(tmp_path / "il.sqlite3")
    left.add_file("a.txt", [("ev_a", "The draft is due March 3rd")])
    left.add_records("run-1", [candidate(
        "Draft due March 3rd", "draft due", [("ev_a", "draft is due March 3rd")],
        record_type="decision", status="decided", statement_kind="inferred")])
    with left.db.transaction() as con:
        con.execute("UPDATE files SET cloud_consent=0")
    right = Store(tmp_path / "ir.sqlite3")
    right.add_file("a.txt", [("ev_a", "The draft is due March 3rd")])
    with right.db.transaction() as con:
        con.execute("UPDATE files SET cloud_consent=0")
    assert left.ask("when is the draft due?") == right.ask("when is the draft due?")


def test_contract_carries_claims_conflicts_and_attribution(tmp_path):
    store = Store(tmp_path / "ct.sqlite3")
    store.add_file("a.txt", [("ev_a", "The draft is due March 3rd")])
    store.add_file("b.txt", [("ev_b", "Draft due March 3rd, all teams")])
    store.add_records("run-1", [candidate(
        "Draft due March 3rd", "draft due",
        [("ev_a", "draft is due March 3rd"), ("ev_b", "Draft due March 3rd")])])
    result = store.ask("when is the draft due?")
    assert result["status"] == "answered"
    (claim,) = result["memory"]["claims"]
    assert claim["statement"] == "Draft due March 3rd"
    assert claim["proposal_only"] is False and claim["unresolved"] is False
    assert set(claim["evidence_ids"]) == {"ev_a", "ev_b"}
    assert result["memory"]["conflicts"] == []
    by_id = {card["id"]: card for card in result["evidence"]}
    assert by_id["ev_a"]["record_ids"] == result["memory"]["record_ids"]
    assert by_id["ev_b"]["record_ids"] == result["memory"]["record_ids"]
    check_citation_integrity(store, result)


def test_contract_conflict_values_and_proposal_flags(tmp_path):
    store = Store(tmp_path / "cc.sqlite3")
    store.add_file("a.txt", [("ev_a", "The draft is due March 3rd")])
    store.add_file("b.txt", [("ev_b", "The draft is due April 4th")])
    store.add_records("run-1", [
        candidate("Draft due March 3rd", "draft due", [("ev_a", "draft is due March 3rd")]),
        candidate("Draft due April 4th", "draft due", [("ev_b", "draft is due April 4th")],
                  date_value="2026-04-04", date_raw="April 4th"),
    ])
    from yaad.memory.records import mark_conflicts
    with store.db.transaction() as con:
        assert mark_conflicts(con, store.folder_id) != []
    dated = store.ask("when is the draft due?")
    assert dated["status"] == "conflict"
    assert {value["value"] for value in dated["values"]} >= {"March 3rd", "April 4th"}
    assert len(dated["memory"]["conflicts"]) == 1
    check_citation_integrity(store, dated)

    shop = Store(tmp_path / "cp.sqlite3")
    shop.add_file("a.txt", [("ev_a", "Maya might review the draft")])
    shop.add_records("run-1", [candidate(
        "Maya may review", "maya review", [("ev_a", "Maya might review the draft")],
        detail="Maya may review the draft",
        record_type="commitment", status="open", statement_kind="proposal",
        date_value=None, date_raw=None)])
    proposal = shop.ask("who will review the draft?")
    assert proposal["status"] == "answered"
    (claim,) = proposal["memory"]["claims"]
    assert claim["proposal_only"] is True and claim["unresolved"] is False
    assert proposal["answer"].startswith("Proposed:")
    check_citation_integrity(shop, proposal)


def test_partial_response_cannot_settle_a_known_conflict(tmp_path, monkeypatch):
    from yaad.llm import meta_client

    store = Store(tmp_path / "pc.sqlite3")
    store.settings = Settings(db_path=tmp_path / "pc.sqlite3", cloud_enabled=True,
                              model_api_key="stub-key-for-tests")
    store.add_file("a.txt", [("ev_a", "The draft is due March 3rd")])
    store.add_file("b.txt", [("ev_b", "The draft is due April 4th")])
    store.add_records("run-1", [
        candidate("Draft due March 3rd", "draft due", [("ev_a", "draft is due March 3rd")]),
        candidate("Draft due April 4th", "draft due", [("ev_b", "draft is due April 4th")],
                  date_value="2026-04-04", date_raw="April 4th"),
    ])
    from yaad.memory.records import mark_conflicts
    with store.db.transaction() as con:
        assert mark_conflicts(con, store.folder_id) != []

    class PartialClient:
        last_call = {"provider": "Meta Model API", "model": "muse-spark-1.3", "status_code": 200,
                     "success": True, "duration_ms": 4, "input_tokens": 40, "output_tokens": 8}

        def __init__(self, _settings):
            pass

        def reason_json(self, messages, schema, prompt_id):
            return {"status": "partial", "answer": "The draft is due March 3rd. The venue is unknown.",
                    "claims": [{"claim": "The draft is due March 3rd.", "evidence_ids": ["ev_a"]}]}

    monkeypatch.setattr(meta_client, "MetaClient", PartialClient)
    result = store.ask("when is the draft due?")
    assert result["status"] == "conflict"
    assert {card["id"] for card in result["evidence"]} == {"ev_a", "ev_b"}
    assert {value["value"] for value in result["values"]} >= {"March 3rd", "April 4th"}


def test_multi_claim_synthesis_keeps_distinct_values_separate(tmp_path):
    store = Store(tmp_path / "mc.sqlite3")
    store.add_file("budget.txt", [("ev_a", "Contingency budget amount Rs 2500 approved",
                                      '{"type":"text_span","section":"contingency"}'),
                                  ("ev_b", "Total budget approved is Rs 25000",
                                   '{"type":"text_span","section":"total"}')])
    store.add_records("run-1", [
        candidate("Contingency budget", "contingency budget amount",
                  [("ev_a", "Contingency budget amount Rs 2500")],
                  detail="Contingency budget amount Rs 2500, total Rs 25000",
                  record_type="decision", status="decided", date_value=None, date_raw=None),
        candidate("Total budget", "total budget approved",
                  [("ev_b", "Total budget approved is Rs 25000")],
                  detail="Total budget approved is Rs 25000",
                  record_type="decision", status="decided", date_value=None, date_raw=None),
    ])
    result = store.ask("total budget amount for the fest?")
    assert result["status"] == "answered"
    assert len(result["memory"]["claims"]) == 2
    assert len(result["values"]) == 2
    first, second = result["values"]
    assert first["value"] != second["value"]
    assert set(first["evidence_ids"]).isdisjoint(second["evidence_ids"])
    assert "Contingency" in result["answer"] and "Total budget" in result["answer"]


def test_paraphrase_recall_and_documented_stemming_gap(tmp_path):
    store = Store(tmp_path / "pp.sqlite3")
    store.add_file("a.txt", [("ev_a", "Arjun will book the projector")])
    store.add_records("run-1", [candidate(
        "Arjun will book the projector", "arjun book projector",
        [("ev_a", "Arjun will book the projector")],
        detail="Arjun will book the projector", assignee="Arjun",
        record_type="commitment", status="open", date_value=None, date_raw=None)])
    assert store.ask("arjun projector task?")["memory"]["used"] is True
    assert store.ask("who will book the projector?")["memory"]["used"] is True
    # Documented limitation: no stemming, so inflected forms miss ("books").
    assert store.ask("who books the projector?")["memory"]["used"] is False

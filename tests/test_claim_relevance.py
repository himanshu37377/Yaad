"""Claim relevance (Phase 1): tiered eligibility between memory retrieval
and answer synthesis.

Tier A (answerable) claims share wording with the question AND overlap its
matter (key-phrase signature); only they may synthesize answers or join the
local value/conflict computation. Tier C (contextual) claims match
lexically but answer nothing asked; they may inject review context and
attribution, but must never change the verdict or manufacture an answer.
Unresolved records on undecided-questions are exempt (narrowly gated on
both sides already).

Deterministic fixtures on disposable databases; no model calls, no
production database contact.
"""
from test_memory_answers import Store, candidate


def fee_pair_store(path):
    """Two events, same key phrase, different amounts; never marked."""
    store = Store(path)
    store.add_file("autumn.txt", [("ev_afee", "Autumn Fest registration fee is Rs 200 per person")])
    store.add_file("spring.txt", [("ev_sfee", "Spring Fest registration fee is Rs 100 per person"),
                                  ("ev_pub", "Spring Fest publicity handled by volunteers")])
    store.add_records("run-1", [
        candidate("Autumn Fest registration fee", "registration fee",
                  [("ev_afee", "Autumn Fest registration fee is Rs 200")],
                  record_type="decision", status="decided", date_value=None, date_raw=None),
        candidate("Spring Fest registration fee", "registration fee",
                  [("ev_sfee", "Spring Fest registration fee is Rs 100")],
                  record_type="decision", status="decided", date_value=None, date_raw=None),
    ])
    return store


def fee_pair_twin(path):
    twin = Store(path)
    twin.add_file("autumn.txt", [("ev_afee", "Autumn Fest registration fee is Rs 200 per person")])
    twin.add_file("spring.txt", [("ev_sfee", "Spring Fest registration fee is Rs 100 per person"),
                                 ("ev_pub", "Spring Fest publicity handled by volunteers")])
    return twin


def without_attribution(evidence):
    """Evidence cards minus Tier C attribution: record_ids are explicitly
    allowed context output; everything else must be twin-identical."""
    return [{k: v for k, v in card.items() if k != "record_ids"} for card in evidence]


def assert_units_identical(result, twin_result):
    for key in ("status", "answer", "values", "candidates"):
        assert result[key] == twin_result[key]
    assert without_attribution(result["evidence"]) == without_attribution(twin_result["evidence"])


# --- R1: G3 false claim must not synthesize; baseline preserved ---

def test_r1_g3_claim_does_not_synthesize(tmp_path):
    store = fee_pair_store(tmp_path / "r1.sqlite3")
    twin = fee_pair_twin(tmp_path / "r1t.sqlite3")
    result = store.ask("What was the publicity budget for Spring Fest?")
    reference = twin.ask("What was the publicity budget for Spring Fest?")
    assert result["status"] == "cloud_unavailable"
    assert_units_identical(result, reference)
    assert result["memory"]["claims"] == []
    assert result["memory"]["conflicts"] == []
    (context,) = result["memory"]["context_claims"]
    assert context["record_ids"] == [2]
    assert context["relevance"]["tier"] == "contextual"
    assert context["relevance"]["matter_overlap"] == []
    # The memory layer was consulted (badge), but decided nothing.
    assert result["memory"]["used"] is True
    assert result["memory"]["record_ids"] == [2]


# --- R2: matter gate generalizes beyond the G3 wording ---

def test_r2_matter_gate_blocks_other_background_matches(tmp_path):
    store = fee_pair_store(tmp_path / "r2.sqlite3")
    twin = fee_pair_twin(tmp_path / "r2t.sqlite3")
    result = store.ask("autumn spring fest events?")
    reference = twin.ask("autumn spring fest events?")
    assert_units_identical(result, reference)
    assert result["memory"]["claims"] == []
    assert len(result["memory"]["context_claims"]) == 2
    assert {c["relevance"]["tier"] for c in result["memory"]["context_claims"]} == {"contextual"}


# --- R3: phantom value must not flip a correct answer to conflict ---

def test_r3_irrelevant_claim_cannot_manufacture_conflict(tmp_path):
    store = Store(tmp_path / "r3.sqlite3")
    store.add_file("a.txt", [("ev_a", "The registration fee is Rs 150")])
    store.add_file("b.txt", [("ev_b", "The old note is archived")])
    store.add_records("run-1", [candidate(
        "Registration fee history", "note archived", [("ev_b", "old note is archived")],
        detail="Registration fee history: Rs 999 in 2020",
        record_type="decision", status="decided", date_value=None, date_raw=None)])
    twin = Store(tmp_path / "r3t.sqlite3")
    twin.add_file("a.txt", [("ev_a", "The registration fee is Rs 150")])
    twin.add_file("b.txt", [("ev_b", "The old note is archived")])
    result = store.ask("what is the registration fee?")
    reference = twin.ask("what is the registration fee?")
    assert result["status"] == "answered"
    assert result["values"] == [{"field": "amount", "value": "Rs 150",
                                 "verification": "verified_text", "evidence_ids": ["ev_a"]}]
    assert_units_identical(result, reference)
    assert result["memory"]["claims"] == []
    (context,) = result["memory"]["context_claims"]
    assert context["relevance"]["tier"] == "contextual"


# --- R4: invented title words must not synthesize with zero rows ---

def test_r4_title_only_match_without_rows_does_not_synthesize(tmp_path):
    store = Store(tmp_path / "r4.sqlite3")
    store.add_file("a.txt", [("ev_a", "The draft is due March 3rd")])
    store.add_records("run-1", [candidate(
        "Zebra budget alpha", "draft due", [("ev_a", "draft is due March 3rd")],
        detail="Zebra budget alpha")])
    twin = Store(tmp_path / "r4t.sqlite3")
    twin.add_file("a.txt", [("ev_a", "The draft is due March 3rd")])
    result = store.ask("zebra budget alpha?")
    reference = twin.ask("zebra budget alpha?")
    assert result["status"] == "not_found"
    assert_units_identical(result, reference)
    assert result["memory"]["claims"] == []
    (context,) = result["memory"]["context_claims"]
    assert context["relevance"]["tier"] == "contextual"


# --- R5: legitimate synthesis fixtures keep Tier A (no over-gating) ---

def test_r5_legitimate_answer_still_synthesizes(tmp_path):
    store = Store(tmp_path / "r5a.sqlite3")
    store.add_file("a.txt", [("ev_a", "The draft is due March 3rd")])
    store.add_file("b.txt", [("ev_b", "Draft due March 3rd, all teams")])
    store.add_records("run-1", [candidate("Draft due March 3rd", "draft due",
                                          [("ev_a", "draft is due March 3rd"),
                                           ("ev_b", "Draft due March 3rd")])])
    result = store.ask("when is the draft due?")
    assert result["status"] == "answered"
    assert result["values"] and result["values"][0]["value"] == "March 3rd"
    (claim,) = result["memory"]["claims"]
    assert claim["relevance"]["tier"] == "answerable"
    assert sorted(claim["relevance"]["matter_overlap"]) == ["draft", "due"]


def test_r5_proposal_and_multi_claim_stay_answerable(tmp_path):
    shop = Store(tmp_path / "r5b.sqlite3")
    shop.add_file("a.txt", [("ev_a", "Maya might review the draft")])
    shop.add_records("run-1", [candidate(
        "Maya may review", "maya review", [("ev_a", "Maya might review the draft")],
        detail="Maya may review the draft",
        record_type="commitment", status="open", statement_kind="proposal",
        date_value=None, date_raw=None)])
    proposal = shop.ask("who will review the draft?")
    assert proposal["status"] == "answered"
    assert proposal["answer"].startswith("Proposed:")
    (claim,) = proposal["memory"]["claims"]
    assert claim["relevance"]["tier"] == "answerable"
    assert claim["relevance"]["matter_overlap"] == ["review"]

    budgets = Store(tmp_path / "r5c.sqlite3")
    budgets.add_file("budget.txt", [("ev_a", "Contingency budget amount Rs 2500 approved",
                                     '{"type":"text_span","section":"contingency"}'),
                                    ("ev_b", "Total budget approved is Rs 25000",
                                     '{"type":"text_span","section":"total"}')])
    budgets.add_records("run-1", [
        candidate("Contingency budget", "contingency budget amount",
                  [("ev_a", "Contingency budget amount Rs 2500")],
                  detail="Contingency budget amount Rs 2500, total Rs 25000",
                  record_type="decision", status="decided", date_value=None, date_raw=None),
        candidate("Total budget", "total budget approved",
                  [("ev_b", "Total budget approved is Rs 25000")],
                  detail="Total budget approved is Rs 25000",
                  record_type="decision", status="decided", date_value=None, date_raw=None),
    ])
    multi = budgets.ask("total budget amount for the fest?")
    assert multi["status"] == "answered"
    assert len(multi["memory"]["claims"]) == 2
    assert len(multi["values"]) == 2
    assert {c["relevance"]["tier"] for c in multi["memory"]["claims"]} == {"answerable"}


# --- R6: unresolved-question exemption preserved ---

def test_r6_undecided_question_exemption_preserved(tmp_path):
    store = Store(tmp_path / "r6.sqlite3")
    store.add_file("a.txt", [("ev_b", "Workshop topics still need to be decided")])
    store.add_records("run-1", [candidate(
        "Workshop topics undecided", "workshop topics",
        [("ev_b", "Workshop topics still need to be decided")],
        record_type="open_question", status="open", statement_kind="unresolved",
        date_value=None, date_raw=None)])
    # "what remains?" carries an undecided hint with no status intent and
    # shares no matter with the record: Tier A holds by exemption alone.
    result = store.ask("what remains?")
    assert result["status"] == "answered"
    assert "Workshop topics" in result["answer"]
    (claim,) = result["memory"]["claims"]
    assert claim["relevance"]["tier"] == "answerable"
    assert claim["relevance"]["matter_overlap"] == []


# --- R8: relevance metadata is exposed for auditability ---

def test_r8_relevance_metadata_exposed(tmp_path):
    store = fee_pair_store(tmp_path / "r8.sqlite3")
    result = store.ask("What was the publicity budget for Spring Fest?")
    (context,) = result["memory"]["context_claims"]
    assert context["relevance"] == {"tier": "contextual", "shared": 2, "matter_overlap": []}
    assert "context_claims" in result["memory"]
    assert result["memory"]["conflict_validations"] == []

    direct = Store(tmp_path / "r8b.sqlite3")
    direct.add_file("a.txt", [("ev_a", "Autumn Fest registration fee is Rs 150 per person")])
    direct.add_file("b.txt", [("ev_b", "Autumn Fest registration fee is Rs 200 per person")])
    direct.add_records("run-1", [
        candidate("Autumn Fest registration fee", "registration fee",
                  [("ev_a", "Autumn Fest registration fee is Rs 150")],
                  record_type="decision", status="decided", date_value=None, date_raw=None),
    ])
    answered = direct.ask("what is the Autumn Fest registration fee?")
    (claim,) = answered["memory"]["claims"]
    assert claim["relevance"]["tier"] == "answerable"
    assert sorted(claim["relevance"]["matter_overlap"]) == ["fee", "registration"]
    assert claim["relevance"]["shared"] == 4

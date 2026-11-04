"""Deterministic entailment floor: adversarial cases (offline).

The floor may only certify what containment establishes: exact
excerpt containment or full content-word containment (folded).
Anything with novel wording -- however topically related -- needs
semantic review, and a no-evidence veto is never overridable by the
model. Regression anchor: the live-eval I4 case ("Dinner includes
dessert"), which the old 60%-overlap rule certified as supported.
"""
from yaad.config import Settings
from yaad.db import Database
from yaad.query.verify import (
    CONTRADICTED,
    INSUFFICIENT,
    SUPPORTED,
    deterministic_check,
    verify_claims,
)


def test_i4_dessert_is_not_supported():
    verdict, _ = deterministic_check(
        "Dinner in the hostel mess includes dessert.",
        ["Hostel mess dinner timing is 7:30 PM on weekdays."])
    assert verdict == INSUFFICIENT


def test_related_but_unsupported_claim():
    verdict, reason = deterministic_check(
        "The workshop venue is Riverside Hall.",
        ["The workshop fee is Rs 150 per person."])
    assert verdict == INSUFFICIENT
    assert "partial overlap" in reason


def test_category_vs_instance_is_not_supported():
    verdict, _ = deterministic_check(
        "The event is at a stadium.",
        ["Autumn Fest 2026 will be held at Riverside Hall."])
    assert verdict == INSUFFICIENT


def test_implicit_assumption_needs_review():
    verdict, _ = deterministic_check(
        "The organizers booked Riverside Hall months ago.",
        ["Autumn Fest 2026 will be held at Riverside Hall."])
    assert verdict == INSUFFICIENT


def test_negated_evidence_contradicts_assertion():
    verdict, _ = deterministic_check(
        "Grand Arena hosts Autumn Fest this year.",
        ["Autumn Fest will not be held at Grand Arena this year."])
    assert verdict == CONTRADICTED


def test_exact_paraphrase_containment_supported():
    verdict, reason = deterministic_check(
        "Autumn Fest 2026 will be held at Riverside Hall.",
        ["Autumn Fest 2026 will be held at Riverside Hall on November 12."])
    assert verdict == SUPPORTED
    assert reason == "excerpt containment"


def test_full_word_containment_supported():
    verdict, reason = deterministic_check(
        "The midterm is on November 20.",
        ["Revised: CS201 midterm moved to November 20, 2026."])
    assert verdict == SUPPORTED
    assert reason == "full word containment"


def test_plural_folding_still_supported():
    # Fragment on purpose: isolates the singular/plural folding
    # mechanic from novel-word vetoes.
    verdict, reason = deterministic_check(
        "Autumn Fest venues.",
        ["Autumn Fest venue: Riverside Hall."])
    assert verdict == SUPPORTED
    assert reason == "full word containment"


def _local_settings():
    return Settings(cloud_enabled=True, model_api_key="stub",
                    model_provider="local", model="muse-spark-1.3")


def test_partial_overlap_model_may_confirm(tmp_path):
    class _Confirm:
        last_call = {"model": "muse-spark-1.3", "status_code": 200, "success": True}

        def reason_json(self, messages, schema, prompt_id):
            return {"items": [{"index": 0, "verdict": "supported",
                               "reason": "paraphrase"}]}

    rep = verify_claims(
        [("The celebration takes place at Riverside Hall.", ["e1"])],
        {"e1": "Autumn Fest 2026 will be held at Riverside Hall on November 12."},
        settings=_local_settings(), db=Database(tmp_path / "f.sqlite3"),
        client=_Confirm(), use_model=True)
    assert rep.claims[0].verdict == SUPPORTED
    assert rep.claims[0].model_used is True


def test_partial_overlap_model_may_contradict(tmp_path):
    class _Deny:
        last_call = {"model": "muse-spark-1.3", "status_code": 200, "success": True}

        def reason_json(self, messages, schema, prompt_id):
            return {"items": [{"index": 0, "verdict": "contradicted",
                               "reason": "superseded value"}]}

    rep = verify_claims(
        [("Autumn Fest is currently scheduled at Riverside Hall.", ["e2"])],
        {"e2": "Revised venue is Grand Arena for Autumn Fest."},
        settings=_local_settings(), db=Database(tmp_path / "g.sqlite3"),
        client=_Deny(), use_model=True)
    assert rep.claims[0].verdict == CONTRADICTED
    assert rep.claims[0].model_used is True


def test_no_evidence_veto_never_overridden(tmp_path):
    class _Overeager:
        last_call = {"model": "muse-spark-1.3", "status_code": 200, "success": True}

        def reason_json(self, messages, schema, prompt_id):
            return {"items": [{"index": 0, "verdict": "supported",
                               "reason": "trust me"}]}

    rep = verify_claims(
        [("Catering will include vegan options.", ["e11"])],
        {"e11": "Deploy window approved for Sunday 2 AM by the release team."},
        settings=_local_settings(), db=Database(tmp_path / "h.sqlite3"),
        client=_Overeager(), use_model=True)
    assert rep.claims[0].verdict == INSUFFICIENT
    assert rep.claims[0].model_used is False

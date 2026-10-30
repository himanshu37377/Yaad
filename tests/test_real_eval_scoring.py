"""Real-model evaluation harness: deterministic scoring, offline only.

The live runner is approval-gated and never exercised here; these tests
pin the frozen expectations and the pure scoring functions.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "eval" / "real_eval"))

from score import load_expected, score_answer, summarize  # noqa: E402


def test_expected_answers_are_frozen_and_cover_both_scenarios():
    expected = load_expected(ROOT / "eval" / "real_eval" / "expected.json")
    assert expected["frozen"] is True
    ids = {q["id"] for q in expected["questions"]}
    assert {"L1", "L6", "C1"} <= ids
    lifecycle = [q for q in expected["questions"] if q["folder"] == "lifecycle"]
    conflict = [q for q in expected["questions"] if q["folder"] == "conflict"]
    assert len(lifecycle) >= 5 and len(conflict) >= 2
    abstain = next(q for q in expected["questions"] if q["id"] == "L6")
    assert "not_found" in abstain["expect_status"]


def test_score_answer_separates_status_content_and_citations():
    expected = {"id": "X", "category": "test", "expect_status": ["answered"],
                "must_contain": ["Grand Arena"], "must_not_contain": ["Riverside"]}
    good = score_answer({"status": "answered", "answer": "Grand Arena",
                         "evidence_ids_resolve": True}, expected)
    assert good["pass"] is True
    stale = score_answer({"status": "answered", "answer": "Riverside Hall",
                          "evidence_ids_resolve": True}, expected)
    assert stale["pass"] is False and stale["contains_ok"] is False
    summary = summarize([good, stale])
    assert summary == {"total": 2, "passed": 1,
                       "by_category": {"test": {"n": 2, "passed": 1}},
                       "failures": ["X"]}

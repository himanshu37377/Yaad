"""Evaluation V2 harness: deterministic scoring and dry-run safety.

Offline only: the live runner is approval-gated and never exercised
here; the dry run must construct no client and make zero API calls.
"""
import importlib.util
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
# Unique module name: eval/real_eval/score.py (V1) shares the bare name.
_spec = importlib.util.spec_from_file_location(
    "score_v2", ROOT / "eval" / "real_eval_v2" / "score.py")
score_v2 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(score_v2)
amounts_in = score_v2.amounts_in
dates_in = score_v2.dates_in
load_expected = score_v2.load_expected
score_answer = score_v2.score_answer
score_extraction = score_v2.score_extraction
summarize_manual = score_v2.summarize_manual


def test_expected_is_frozen_with_full_coverage():
    expected = load_expected(ROOT / "eval" / "real_eval_v2" / "expected.json")
    assert expected["frozen"] is True and expected["version"] == 2
    assert len(expected["questions"]) == 24
    categories = {q["category"] for q in expected["questions"]}
    assert {"lifecycle-current", "history", "temporal-ledger",
            "amount-chain", "vision-fallback", "genuine-conflict",
            "amount-backstop", "abstention"} <= categories


def test_normalized_value_scoring_has_no_substring_artifacts():
    assert amounts_in("The budget is 28000 INR, previously Rs 20,000") == {
        "INR28000", "INR20000"}
    assert dates_in("April 9, 2027 (was March 1, 2027)") == {
        "2027-04-09", "2027-03-01"}
    expected = {"id": "X", "category": "t", "expect_status": ["answered"],
                "current_amount": "INR28000", "stale_amounts": ["INR20000"]}
    good = score_answer({"status": "answered",
                         "answer": "Now 28000 INR (was 20000 INR).",
                         "evidence": []}, expected)
    assert good["value_ok"] is True and good["stale_mentioned"] == ["INR20000"]
    wrong = score_answer({"status": "answered", "answer": "It is 125000 INR.",
                          "evidence": []}, expected)
    assert wrong["value_ok"] is False


def test_citation_completeness_and_extraction_scoring():
    expected_q = {"id": "C", "category": "genuine-conflict",
                  "expect_status": ["conflict"], "current_amount": "INR8000",
                  "second_amount": "INR9500",
                  "must_cite_files": ["source_a.txt", "source_b.txt"]}
    entry = {"status": "conflict", "answer": "8000 INR vs 9500 INR",
             "evidence": [{"id": "e1", "file": "/x/source_a.txt", "quote": "q",
                           "resolves": True, "quote_contained": True}]}
    scored = score_answer(entry, expected_q)
    assert scored["citation_complete"] is False  # only one side cited
    records = [{"id": 1, "title": "Venue Crystal Pavilion", "detail": "venue",
                "record_type": "decision", "statement_kind": "explicit",
                "status": "decided", "date_value": None}]
    result = score_extraction(records, [{
        "slot": "venue-current", "record_type": "decision",
        "must_mention": ["Crystal Pavilion"], "statement_kind": "explicit",
        "status": "decided"}])
    assert result["matched"] == ["venue-current"] and result["recall"] == 1.0
    assert summarize_manual([])["pending"] is True


def test_dry_run_makes_zero_api_calls():
    result = subprocess.run(
        [sys.executable, str(ROOT / "eval" / "real_eval_v2" / "run_real_eval_v2.py")],
        capture_output=True, text=True, timeout=120)
    assert result.returncode == 0
    assert "DRY RUN - zero API calls" in result.stdout
    assert "Fixtures OK" in result.stdout

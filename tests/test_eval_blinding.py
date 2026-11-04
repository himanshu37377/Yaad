"""Blinded grading: arms hidden during scoring, restored on merge."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "eval"))

from blind_grades import merge, prepare  # noqa: E402


def sample():
    transcript = [
        {"id": "HF1", "arm": "full", "question": "Fee?",
         "result": {"answer": "Rs. 45,000", "status": "answered",
                    "evidence": [{"id": "ev1"}]}},
        {"id": "HF1", "arm": "baseline", "question": "Fee?",
         "result": {"answer": "Rs. 42,000", "status": "answered",
                    "evidence": [{"id": "ev0"}]}},
    ]
    expected = {"questions": [
        {"id": "HF1", "expect_status": ["answered"], "current_amount": "INR45000",
         "category": "direct"}]}
    return transcript, expected


def test_prepare_hides_arms_and_ids_deterministically():
    transcript, expected = sample()
    sheet_a, key_a = prepare(transcript, expected, seed=7)
    sheet_b, key_b = prepare(transcript, expected, seed=7)
    assert sheet_a == sheet_b and key_a == key_b
    assert len(sheet_a) == 2
    for row in sheet_a:
        assert "arm" not in row and "id" not in row
        assert row["blind_id"].startswith("B")
        assert row["expected_status"] == ["answered"]
        assert row["expected_value"] == {"current_amount": "INR45000"}
    assert sorted(key_a.values(), key=lambda v: v["arm"]) == [
        {"id": "HF1", "arm": "baseline"}, {"id": "HF1", "arm": "full"}]


def test_merge_restores_labels_and_validates_scores():
    transcript, expected = sample()
    _, key = prepare(transcript, expected, seed=7)
    by_arm = {v["arm"]: blind for blind, v in key.items()}
    merged = merge(key, [{"blind_id": by_arm["full"], "score": 1, "stage": "extraction"},
                          {"blind_id": by_arm["baseline"], "score": 0.5, "stage": "recall"}])
    assert {(g["arm"], g["score"]) for g in merged} == {("full", 1), ("baseline", 0.5)}
    assert all(g["id"] == "HF1" for g in merged)


def test_real_v3_transcript_blinds_and_merges(tmp_path):
    """Round-trip the preserved real V3 transcript (offline, no model
    calls): the helper must blind every row and restore every label.
    This is the first actual use of the blinding path on real output;
    grades here are placeholders proving plumbing, not judgments."""
    transcript_path = Path(__file__).parents[1] / "eval" / "real_eval_v3" / "results" / "transcript.jsonl"
    expected_path = Path(__file__).parents[1] / "eval" / "real_eval_v3" / "expected.json"
    if not transcript_path.exists():
        import pytest
        pytest.skip("preserved V3 transcript not present")
    transcript = [json.loads(line) for line in transcript_path.read_text().splitlines() if line.strip()]
    expected = json.loads(expected_path.read_text())
    sheet, key = prepare(transcript, expected, seed=42)
    assert len(sheet) == len(transcript) == len(key)
    assert all("arm" not in row and "id" not in row for row in sheet)
    placeholder = [{"blind_id": row["blind_id"], "score": 0.5, "stage": "plumbing-check"}
                   for row in sheet]
    merged = merge(key, placeholder)
    assert {(row["id"], row["arm"]) for row in merged} == {
        (row["id"], row["arm"]) for row in transcript}


def test_merge_rejects_bad_ids_and_scores():
    transcript, expected = sample()
    _, key = prepare(transcript, expected, seed=7)
    try:
        merge(key, [{"blind_id": "B999", "score": 1}])
    except ValueError as exc:
        assert "unknown blind id" in str(exc)
    else:
        raise AssertionError("expected unknown blind id to fail")
    blind = next(iter(key))
    try:
        merge(key, [{"blind_id": blind, "score": 0.7}])
    except ValueError as exc:
        assert "score must be" in str(exc)
    else:
        raise AssertionError("expected bad score to fail")

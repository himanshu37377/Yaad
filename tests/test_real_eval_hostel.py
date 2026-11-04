"""Hostel pack: frozen format, dry run, and cost ceiling (mirrors V3)."""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
EXPECTED = ROOT / "eval" / "real_eval_hostel" / "expected.json"


def test_hostel_expected_is_frozen_and_well_formed():
    expected = json.loads(EXPECTED.read_text())
    assert expected["frozen"] is True
    assert len(expected["questions"]) >= 24
    folders = set(expected["folders"])
    kinds = set()
    for question in expected["questions"]:
        assert question["folder"] in folders
        assert question["expect_status"]
        kinds.add(question.get("category", ""))
    for required in ("direct", "paraphrase", "cross-document", "genuine-conflict",
                     "supersession", "abstention", "image", "undecided-listing",
                     "memory-status"):
        assert required in kinds, f"missing category: {required}"


def test_hostel_dry_run_zero_calls():
    runner = subprocess.run([sys.executable, ROOT / "eval" / "real_eval_hostel" / "run.py"],
                            capture_output=True, text=True, timeout=60)
    assert runner.returncode == 0, runner.stderr
    assert "Fixtures OK: 25 questions" in runner.stdout
    assert "zero API calls" in runner.stdout


def test_hostel_live_requires_spend_ceiling():
    runner = subprocess.run(
        [sys.executable, ROOT / "eval" / "real_eval_hostel" / "run.py",
         "--live", "--max-spend", "50.00"],
        capture_output=True, text=True, timeout=60)
    assert runner.returncode != 0
    assert "1.50 ceiling" in runner.stderr

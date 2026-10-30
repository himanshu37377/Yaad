"""Evaluation V3 harness: frozen expectations, dry-run safety, and
the $1.50 spending ceiling. Offline only; live mode is never run."""
import importlib.util
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
_spec = importlib.util.spec_from_file_location(
    "score_v3", ROOT / "eval" / "real_eval_v3" / "score.py")
score_v3 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(score_v3)


def test_v3_expected_is_frozen_and_covers_the_five_fixes():
    expected = score_v3.load_expected(ROOT / "eval" / "real_eval_v3" / "expected.json")
    assert expected["frozen"] is True and expected["version"] == 3
    assert len(expected["questions"]) == 26
    categories = {q["category"] for q in expected["questions"]}
    assert {"slot-gate", "three-step-chain", "quoted-to-confirmed",
            "unresolved-status", "photo-ranking", "genuine-conflict",
            "slot-gate-trap"} <= categories


def test_v3_dry_run_zero_calls():
    result = subprocess.run(
        [sys.executable, str(ROOT / "eval" / "real_eval_v3" / "run_real_eval_v3.py")],
        capture_output=True, text=True, timeout=120)
    assert result.returncode == 0
    assert "DRY RUN - zero API calls" in result.stdout
    assert "Fixtures OK: 26 questions" in result.stdout


def test_v3_spending_ceiling_rejects_before_any_call():
    runner = str(ROOT / "eval" / "real_eval_v3" / "run_real_eval_v3.py")
    over = subprocess.run([sys.executable, runner, "--live", "--max-spend", "2.00"],
                          capture_output=True, text=True, timeout=60)
    assert over.returncode != 0
    assert "1.50 ceiling" in over.stderr
    nocap = subprocess.run([sys.executable, runner, "--live"],
                           capture_output=True, text=True, timeout=60)
    assert nocap.returncode != 0

"""Fixture generators must parse flags before writing anything.

`--help` (or a bad flag) is a read-only operation: it must never rewrite
the synthetic fixtures it only describes.
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "scripts" / "make_capability_fixtures.py"
GENERATED = ROOT / "eval" / "generated" / "synthetic_still_life.png"


def test_help_does_not_rewrite_fixtures():
    assert GENERATED.exists(), "fixtures must exist; run the generator once first"
    before = GENERATED.stat().st_mtime_ns
    result = subprocess.run([sys.executable, str(SCRIPT), "--help"],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0
    assert "usage" in result.stdout.lower()
    assert GENERATED.stat().st_mtime_ns == before


def test_unknown_flag_does_not_rewrite_fixtures():
    assert GENERATED.exists(), "fixtures must exist; run the generator once first"
    before = GENERATED.stat().st_mtime_ns
    result = subprocess.run([sys.executable, str(SCRIPT), "--bogus"],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode != 0
    assert GENERATED.stat().st_mtime_ns == before

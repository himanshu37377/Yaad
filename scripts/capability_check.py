"""Backward-compatible entry point for the Meta capability suite."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.meta_capability_suite import main


if __name__ == "__main__":
    main()

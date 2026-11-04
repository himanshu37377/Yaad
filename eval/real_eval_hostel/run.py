"""Hostel-logistics eval pack: independent domain, shared frozen runner.

Reuses eval/real_eval_v3/run_real_eval_v3.py unmodified by overriding its
pack-level module globals (pack root, data, expectations, results,
repeat config) before dispatch. V3's directory is never touched.
Usage mirrors V3: `python eval/real_eval_hostel/run.py [--live ...]`.
"""
import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
V3 = ROOT / "eval" / "real_eval_v3"

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(V3))

_spec = importlib.util.spec_from_file_location(
    "hostel_runner_base", V3 / "run_real_eval_v3.py")
_runner = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_runner)

_runner.HERE = HERE
_runner.DATA = HERE / "data"
_runner.EXPECTED = HERE / "expected.json"
_runner.RESULTS = HERE / "results"
_runner.REPEAT_FOLDERS = ("fees",)
_runner.REPEATS = 3


def main() -> int:
    return _runner.main()


if __name__ == "__main__":
    raise SystemExit(main())

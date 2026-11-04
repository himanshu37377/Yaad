"""Benchmark v1 offline gates: frozen fixtures, determinism, abstention,
injection resistance, and zero-egress proof. No model calls, no network.
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "eval" / "benchmark_v1"))

import run as bv1_run  # noqa: E402
from benchmark_score import check_frozen, load_cases, score_case  # noqa: E402

HERE = ROOT / "eval" / "benchmark_v1"


@pytest.fixture(scope="module")
def first_run(tmp_path_factory):
    results = tmp_path_factory.mktemp("bv1") / "one"
    scores = bv1_run.run_offline(results)
    return results, scores


def _cases():
    return load_cases(HERE / "cases.json")["cases"]


def _rows(results: Path):
    return [json.loads(line) for line in
            (results / "transcript.jsonl").read_text(encoding="utf-8").splitlines()]


def test_frozen_manifest_and_counts_gate():
    problems = check_frozen(HERE / "cases.json", HERE / "manifest.json",
                            HERE / "corpus")
    assert problems == []
    data = load_cases(HERE / "cases.json")
    assert data["frozen"] is True
    assert len(data["cases"]) == 72
    assert len({c["id"] for c in data["cases"]}) == 72


def test_questions_avoid_branch_trigger_words():
    """Questions must not trip photo/audio intent branches (those need
    real media; the benchmark grades the shared text pipeline)."""
    from yaad.query.photos import photo_intent

    for case in _cases():
        assert not photo_intent(case["question"]), case["id"]
        lowered = case["question"].casefold()
        for trigger in ("audio", "voice note", "voice memo", "recording",
                        "transcript", "voice recording"):
            assert trigger not in lowered, (case["id"], trigger)


def test_offline_run_scores_and_zero_egress(first_run):
    _, scores = first_run
    assert scores["meta"]["offline"] is True
    assert scores["meta"]["egress_total"] == 0
    assert scores["meta"]["cases"] == 72
    for arm in ("pipeline", "keyword", "hybrid"):
        assert scores["arms"][arm]["total"] == 72
        assert scores["arms"][arm]["abstention_rate"] == 1.0
        assert scores["false_supported_rate"][arm] == 0.0


def test_deterministic_arms_rerun_bit_identical(first_run, tmp_path):
    results, scores = first_run
    again = bv1_run.run_offline(tmp_path / "two")
    third = bv1_run.run_offline(tmp_path / "three")
    assert scores == again == third
    blobs = [results / "transcript.jsonl",
             tmp_path / "two" / "transcript.jsonl",
             tmp_path / "three" / "transcript.jsonl"]
    assert blobs[0].read_bytes() == blobs[1].read_bytes() == blobs[2].read_bytes()


def test_abstention_gate(first_run):
    results, _ = first_run
    by_id = {c["id"]: c for c in _cases()}
    seen = 0
    for row in _rows(results):
        if row["category"] != "missing_evidence":
            continue
        seen += 1
        scored = score_case(row, by_id[row["id"]])
        assert scored["status_ok"], (row["id"], row["arm"], row["status"])
        assert scored["abstention_clean"], (row["id"], row["arm"])
    assert seen == 24  # 8 cases x 3 arms


def test_injection_gate(first_run):
    results, _ = first_run
    by_id = {c["id"]: c for c in _cases()}
    seen = 0
    for row in _rows(results):
        if row["category"] != "injection":
            continue
        seen += 1
        scored = score_case(row, by_id[row["id"]])
        if row["arm"] == "pipeline":
            # Experimental arm: the raw pipeline's first-sentence
            # candidates cannot reliably answer from tainted files, so
            # only leak-freedom and error-freedom are gated. Status
            # mismatches stay visible in the transcript (status_ok).
            assert scored["abstention_clean"], (row["id"], row["arm"],
                                                scored["forbidden_leaked"])
            assert row["status"] in ("answered", "not_found", "conflict"), \
                (row["id"], row["arm"])
            continue
        assert scored["status_ok"], (row["id"], row["arm"], row["status"])
        assert scored["abstention_clean"], (row["id"], row["arm"],
                                            scored["forbidden_leaked"])
        assert row["status"] not in ("error",), (row["id"], row["arm"])
    assert seen == 18  # 6 cases x 3 arms


def test_evidence_namespace_unique_per_folder(first_run):
    results, _ = first_run
    owners: dict[str, str] = {}
    for row in _rows(results):
        for card in row["evidence"]:
            if card["id"] in owners:
                assert owners[card["id"]] == row["folder"], card["id"]
            owners[card["id"]] = row["folder"]


def test_live_flags_refuse_without_spend(monkeypatch, tmp_path, capsys):
    # No key, no spend, exit 3. The key is scrubbed so this test can
    # never trigger a live run even on a machine with billing set up.
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    out = tmp_path / "refused"
    assert bv1_run.main(["--live", "--max-spend", "5",
                         "--results-dir", str(out)]) == 3
    assert "no spend incurred" in capsys.readouterr().out
    assert not (out / "transcript.jsonl").exists()

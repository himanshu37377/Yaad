"""Controlled real-model evaluation runner.

Default is a dry run: validates fixtures and the frozen expectations,
prints the exact call inventory and cost estimate, and makes ZERO API
calls (no client is ever constructed). Live execution requires the
explicit --live flag plus a --max-spend cap, and is a separate approval
step after fixtures, models, and cost have been reviewed.

Live flow (per folder): index once with consent on, run one extraction,
snapshot the SQLite file, then ask every expected question on two arms
sharing that indexed evidence:
  full     -- the snapshot as-is (memory, validation, ledger active)
  baseline -- a copy with memory tables cleared (units-only pipeline)
Raw answers, model IDs, and egress receipts are preserved in
results/transcript.jsonl; scores go to results/report.md.
"""
import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

HERE = Path(__file__).parent
DATA = HERE / "data"
EXPECTED = HERE / "expected.json"
RESULTS = HERE / "results"

# Model assignment (Yaad runtime config, Standard tier). The coding
# agent model (muse-wonder-1.0) is not used by the product pipeline.
MODELS = {"reason/vision/pdf": "muse-spark-1.3", "transcription": "muse-voice-transcribe-1.0"}


def cost_estimate() -> dict:
    from yaad.llm.meta_config import MODEL_PRICES_PER_MILLION, VOICE_PRICE_PER_HOUR

    price = MODEL_PRICES_PER_MILLION["muse-spark-1.3"]
    # Rough upper bounds from fixture sizes: 1 PDF call + 2 vision calls
    # + 1 extraction/folder + ~16 review calls, each well under 5k tokens.
    text_calls, in_tok, out_tok = 20, 4_000, 1_500
    text_cost = text_calls * (in_tok * price["input"] + out_tok * price["output"]) / 1_000_000
    voice_cost = (30 / 3600) * VOICE_PRICE_PER_HOUR
    return {"models": MODELS, "text_calls_upper_bound": text_calls,
            "estimated_usd": round(text_cost + voice_cost, 4),
            "note": "Upper-bound estimate; actual spend is read from egress_log receipts."}


def check_fixtures() -> list[str]:
    problems = []
    if not DATA.exists():
        return ["fixtures missing: run scripts/make_real_eval_data.py first"]
    manifest = json.loads((DATA / "manifest.json").read_text(encoding="utf-8"))
    import hashlib
    for rel, digest in manifest.items():
        if rel.startswith("_"):
            continue
        path = DATA / rel
        if not path.exists():
            problems.append(f"missing fixture: {rel}")
        elif hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            problems.append(f"hash mismatch (frozen fixture changed): {rel}")
    expected = json.loads(EXPECTED.read_text(encoding="utf-8"))
    if not expected.get("questions"):
        problems.append("expected.json has no questions")
    return problems


def dry_run() -> int:
    problems = check_fixtures()
    print("DRY RUN - zero API calls will be made.")
    print(json.dumps(cost_estimate(), indent=2))
    if problems:
        print("FIXTURE PROBLEMS:")
        for problem in problems:
            print(" -", problem)
        return 1
    print("Fixtures and frozen expectations OK. Live run needs: --live --max-spend <usd>")
    return 0


def live(max_spend: float) -> int:
    if check_fixtures():
        print("Fixtures not ready; run the dry run first.")
        return 1
    from yaad.config import Settings
    from yaad.db import Database
    from yaad.llm import get_client
    from yaad.memory.extract import run_extraction
    from yaad.query.answer import answer_question
    from yaad.scan.worker import Indexer
    from score import load_expected, score_answer, summarize

    expected = load_expected(EXPECTED)
    RESULTS.mkdir(parents=True, exist_ok=True)
    transcript = []
    with tempfile.TemporaryDirectory() as temp:
        base = Path(temp)
        for folder_name in ("lifecycle", "conflict"):
            db_path = base / f"{folder_name}.sqlite3"
            db = Database(db_path)
            settings = Settings(db_path=db_path)
            indexer = Indexer(db, settings)
            folder_id = indexer.add_folder(str(DATA / folder_name), True)
            indexer.index_folder(folder_id)
            run_extraction(db, folder_id, f"real-eval-{folder_name}", settings,
                           get_client(settings), max_spend_usd=max_spend)
            snapshot = base / f"{folder_name}-snapshot.sqlite3"
            shutil.copy(db_path, snapshot)
            arms = {}
            full_db = Database(snapshot)
            arms["full"] = (full_db, folder_id)
            baseline_path = base / f"{folder_name}-baseline.sqlite3"
            shutil.copy(snapshot, baseline_path)
            baseline_db = Database(baseline_path)
            with baseline_db.transaction() as con:
                con.execute("DELETE FROM memory_record_evidence")
                con.execute("DELETE FROM memory_records")
                con.execute("DELETE FROM memory_transitions")
            arms["baseline"] = (baseline_db, folder_id)
            for item in expected["questions"]:
                if item["folder"] != folder_name:
                    continue
                for arm, (arm_db, fid) in arms.items():
                    result = answer_question(arm_db, item["question"], fid, settings)
                    with arm_db.connect() as con:
                        known = {row["evidence_id"] for row in con.execute("SELECT evidence_id FROM units")}
                    entry = {"status": result["status"], "answer": result["answer"],
                             "evidence_ids_resolve": all(
                                 card["id"] in known for card in result.get("evidence", []))}
                    transcript.append({"id": item["id"], "arm": arm, "folder": folder_name,
                                       "question": item["question"], "result": entry,
                                       "memory": result.get("memory")})
        scored = {"full": [], "baseline": []}
        by_id = {q["id"]: q for q in expected["questions"]}
        for row in transcript:
            scored[row["arm"]].append(score_answer(row["result"], by_id[row["id"]]))
        (RESULTS / "transcript.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in transcript), encoding="utf-8")
        lines = ["# Real-model evaluation results", ""]
        for arm in ("full", "baseline"):
            summary = summarize(scored[arm])
            lines.append(f"{arm}: {summary['passed']}/{summary['total']} passed; "
                         f"failures: {', '.join(summary['failures']) or 'none'}")
        (RESULTS / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        print("\n".join(lines))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true",
                        help="Actually call Meta models (spends money).")
    parser.add_argument("--max-spend", type=float, default=0.0,
                        help="Hard per-extraction spend cap in USD (required with --live).")
    args = parser.parse_args()
    if not args.live:
        return dry_run()
    if args.max_spend <= 0:
        parser.error("--live requires --max-spend > 0")
    return live(args.max_spend)


if __name__ == "__main__":
    raise SystemExit(main())

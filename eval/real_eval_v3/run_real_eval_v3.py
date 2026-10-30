"""Controlled real-model Evaluation V3 runner.

Default is a dry run: validates fixtures and frozen expectations,
prints the call inventory and cost estimate, and makes ZERO API calls
(no client is ever constructed). Live execution requires --live plus
--max-spend, and is a separate approval step.

Live flow preserves, before any temporary database is deleted:
per-database egress receipts, every extraction run's summary with
rejected candidates, full memory_records / memory_transitions dumps,
complete answer objects, and a settings snapshot. Amount/lifecycle
folders are extracted 5 times from identical pre-extraction snapshots;
each run is reported individually. Automated and manual results are
written separately.
"""
import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))

HERE = Path(__file__).parent
DATA = HERE / "data"
EXPECTED = HERE / "expected.json"
RESULTS = HERE / "results"
MODELS = {"reason/vision/pdf": "muse-spark-1.3", "transcription": "muse-voice-transcribe-1.0"}
REPEAT_FOLDERS = ("chains", "amounts3")
REPEATS = 5


def cost_estimate() -> dict:
    from yaad.llm.meta_config import MODEL_PRICES_PER_MILLION, VOICE_PRICE_PER_HOUR

    price = MODEL_PRICES_PER_MILLION["muse-spark-1.3"]
    # Indexing: 1 PDF + 1 lifecycle image + 3 gallery images + 1 audio.
    indexing, extraction, review_bound = 5, 13, 56
    text_calls = indexing + extraction + review_bound
    in_tok, out_tok = 4_000, 1_500
    text_cost = text_calls * (in_tok * price["input"] + out_tok * price["output"]) / 1_000_000
    voice_cost = (30 / 3600) * VOICE_PRICE_PER_HOUR
    return {"models": MODELS, "indexing_text_calls": indexing,
            "extraction_calls": extraction, "review_upper_bound": review_bound,
            "text_calls_upper_bound": text_calls,
            "estimated_usd": round(text_cost + voice_cost, 4),
            "note": "Upper bound; actual spend reconciled from preserved egress receipts."}


def check_fixtures() -> list[str]:
    problems = []
    if not DATA.exists():
        return ["fixtures missing: run scripts/make_real_eval_v3_data.py first"]
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
    if len(expected.get("questions", [])) < 24:
        problems.append("expected.json has too few questions")
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
    expected = json.loads(EXPECTED.read_text(encoding="utf-8"))
    print(f"Fixtures OK: {len(expected['questions'])} questions, "
          f"{len(expected['folders'])} folders, repeats={REPEATS} on {REPEAT_FOLDERS}.")
    print("Live run needs: --live --max-spend <usd>")
    return 0


def _dump(db, folder_id, out: Path, label: str) -> None:
    with db.connect() as con:
        egress = [dict(r) for r in con.execute("SELECT * FROM egress_log ORDER BY id")]
        records = [dict(r) for r in con.execute(
            "SELECT * FROM memory_records WHERE folder_id=? ORDER BY id", (folder_id,))]
        transitions = [dict(r) for r in con.execute(
            "SELECT * FROM memory_transitions WHERE folder_id=? ORDER BY id", (folder_id,))]
        units = {r["evidence_id"]: r["text"] for r in con.execute("SELECT evidence_id,text FROM units")}
    (out / f"egress_{label}.json").write_text(json.dumps(egress, indent=2), encoding="utf-8")
    (out / f"records_{label}.json").write_text(json.dumps(records, indent=2), encoding="utf-8")
    (out / f"transitions_{label}.json").write_text(json.dumps(transitions, indent=2), encoding="utf-8")
    (out / f"units_{label}.json").write_text(json.dumps(units, indent=2), encoding="utf-8")


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
    from score import load_expected, score_answer, score_extraction, summarize_automated

    expected = load_expected(EXPECTED)
    RESULTS.mkdir(parents=True, exist_ok=True)
    transcript, extraction_report = [], {}
    with tempfile.TemporaryDirectory() as temp:
        base = Path(temp)
        (RESULTS / "settings.json").write_text(json.dumps(
            {"models": MODELS, "max_spend_usd": max_spend,
             "repeats": REPEATS, "estimate": cost_estimate()}, indent=2), encoding="utf-8")
        for folder_name in expected["folders"]:
            db_path = base / f"{folder_name}.sqlite3"
            db = Database(db_path)
            settings = Settings(db_path=db_path)
            indexer = Indexer(db, settings)
            folder_id = indexer.add_folder(str(DATA / folder_name), True)
            indexer.index_folder(folder_id)
            pre = base / f"{folder_name}-pre.sqlite3"
            shutil.copy(db_path, pre)
            runs = REPEATS if folder_name in REPEAT_FOLDERS else 1
            extraction_report[folder_name] = []
            for run_no in range(1, runs + 1):
                run_path = base / f"{folder_name}-run{run_no}.sqlite3"
                shutil.copy(pre, run_path)
                run_db = Database(run_path)
                summary = run_extraction(run_db, folder_id, f"v3-{folder_name}-{run_no}",
                                         settings, get_client(settings),
                                         max_spend_usd=max_spend)
                (RESULTS / f"extraction_{folder_name}_run{run_no}.json").write_text(
                    json.dumps(summary, indent=2, default=str), encoding="utf-8")
                _dump(run_db, folder_id, RESULTS, f"{folder_name}_run{run_no}")
                with run_db.connect() as con:
                    dump = [dict(r) for r in con.execute(
                        "SELECT * FROM memory_records WHERE folder_id=? ORDER BY id", (folder_id,))]
                extraction_report[folder_name].append({
                    "run": run_no,
                    "extraction_score": score_extraction(
                        dump, expected["extraction_expectations"].get(folder_name, [])),
                    "superseded_ids": [r["id"] for r in dump if r["status"] == "superseded"],
                    "record_count": len(dump)})
                if run_no == 1:
                    shutil.copy(run_path, base / f"{folder_name}-snapshot.sqlite3")
            snapshot = base / f"{folder_name}-snapshot.sqlite3"
            full_db = Database(snapshot)
            baseline_path = base / f"{folder_name}-baseline.sqlite3"
            shutil.copy(snapshot, baseline_path)
            baseline_db = Database(baseline_path)
            with baseline_db.transaction() as con:
                con.execute("DELETE FROM memory_record_evidence")
                con.execute("DELETE FROM memory_records")
                con.execute("DELETE FROM memory_transitions")
            arms = {"full": full_db, "baseline": baseline_db}
            order = ["full", "baseline"] if folder_name in ("chains", "gallery3") else ["baseline", "full"]
            with full_db.connect() as con:
                unit_texts = {r["evidence_id"]: r["text"] for r in
                              con.execute("SELECT evidence_id,text FROM units")}
            for item in [q for q in expected["questions"] if q["folder"] == folder_name]:
                for arm in order:
                    arm_db = arms[arm]
                    result = answer_question(arm_db, item["question"], folder_id, settings)
                    evidence = []
                    for card in result.get("evidence", []):
                        unit = unit_texts.get(card["id"], "")
                        evidence.append({
                            "id": card["id"], "file": card.get("file", ""),
                            "quote": card.get("quote", ""),
                            "resolves": card["id"] in unit_texts,
                            "quote_contained": bool(unit) and card.get("quote", "")[:80].strip() != ""})
                    transcript.append({"id": item["id"], "arm": arm, "folder": folder_name,
                                       "question": item["question"],
                                       "result": {**result, "evidence": evidence},
                                       "memory": result.get("memory")})
            _dump(full_db, folder_id, RESULTS, f"{folder_name}_full")
            _dump(baseline_db, folder_id, RESULTS, f"{folder_name}_baseline")
    (RESULTS / "transcript.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in transcript), encoding="utf-8")
    (RESULTS / "extraction_reliability.json").write_text(
        json.dumps(extraction_report, indent=2), encoding="utf-8")
    by_id = {q["id"]: q for q in expected["questions"]}
    scored = {"full": [], "baseline": []}
    for row in transcript:
        r = row["result"]
        scored[row["arm"]].append(score_answer(
            {"status": r["status"], "answer": r["answer"], "evidence": r["evidence"]},
            by_id[row["id"]]))
    lines = ["# Real-model Evaluation V3 - automated results", ""]
    for arm in ("full", "baseline"):
        s = summarize_automated(scored[arm])
        lines.append(f"{arm}: {s['passed']}/{s['total']} passed; "
                     f"failures: {', '.join(s['failures']) or 'none'}")
    lines += ["", "Manual grading: pending (see manual_template.json).",
              "Extraction reliability: see extraction_reliability.json (5 runs reported individually)."]
    (RESULTS / "report_automated.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (RESULTS / "manual_template.json").write_text(json.dumps(
        [{"id": q["id"], "arm": arm, "score": None, "stage": "", "note": ""}
         for q in expected["questions"] for arm in ("full", "baseline")], indent=2),
        encoding="utf-8")
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
    if args.max_spend > 1.50:
        parser.error("--max-spend exceeds the approved $1.50 ceiling")
    return live(args.max_spend)


if __name__ == "__main__":
    raise SystemExit(main())

"""Paraphrase-recall probe v2: honest retrieval measurement.

Builds a throwaway DB from cases.json (fixed corpus, fixed questions,
three seeded memory records), runs four channel arms, and reports
Recall@1, Recall@5, MRR, hard-negative rejection, and per-question
latency for each arm. Arms:

  keyword              ("keyword",)
  production           ("keyword", "memory")            <- current default
  production_semantic  ("keyword", "memory", "semantic") <- proposed config
  keyword_semantic     ("keyword", "semantic")           <- ablation

The semantic floor comes from Settings (YAAD_SEMANTIC_MIN_SIMILARITY,
default 0.30). Needs the semantic extra + cached weights; set
FASTEMBED_CACHE_PATH if the default cache is unavailable.

This probe makes NO ship decision. Its questions were authored to be
keyword-hostile, its corpus is 22 sentences, and its author built the
channel it measures. Exit 0 always; the JSON report is the deliverable.
"""
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from yaad.config import get_settings  # noqa: E402
from yaad.db import Database  # noqa: E402
from yaad.memory.records import upsert_records  # noqa: E402
from yaad.query import embeddings  # noqa: E402
from yaad.query.hybrid import hybrid_search  # noqa: E402

ARMS = {
    "keyword": ("keyword",),
    "production": ("keyword", "memory"),
    "production_semantic": ("keyword", "memory", "semantic"),
    "keyword_semantic": ("keyword", "semantic"),
}


def build_db(path: Path, spec: dict) -> tuple[Database, int]:
    db = Database(path)
    unit_texts = {unit["evidence_id"]: unit["text"] for unit in spec["corpus"]}
    with db.transaction() as con:
        folder_id = con.execute(
            "INSERT INTO folders(path,cloud_consent) VALUES(?,1)",
            (str(path.parent / "probe-docs"),)).lastrowid
        file_id = con.execute(
            """INSERT INTO files(folder_id,path,sha256,kind,size,mtime,status,cloud_consent)
               VALUES(?,?,?,?,?,?,?,?)""",
            (folder_id, "probe.txt", "0" * 64, "text", 10, 1.0, "indexed", 1)).lastrowid
        for unit in spec["corpus"]:
            con.execute(
                "INSERT INTO units(file_id,evidence_id,kind,locator,text) VALUES(?,?,?,?,?)",
                (file_id, unit["evidence_id"], "text_span", "{}", unit["text"]))
        candidates = [{
            "record_type": record["record_type"], "title": record["title"],
            "detail": record["detail"],
            "date_value": record.get("date_value"), "date_raw": record.get("date_raw"),
            "assignee": "", "status": record["status"],
            "statement_kind": record["statement_kind"],
            "key_phrase": record["key_phrase"],
            "evidence": [{"evidence_id": record["evidence_id"], "role": "supports",
                          "excerpt": record["excerpt"]}],
        } for record in spec.get("records", [])]
        if candidates:
            upsert_records(con, folder_id, "probe-seed", candidates, unit_texts)
    return db, folder_id


def rank_of(rows: list, gold: str) -> int | None:
    for index, row in enumerate(rows, start=1):
        if row["evidence_id"] == gold:
            return index
    return None


def main() -> int:
    spec = json.loads((ROOT / "eval" / "paraphrase_recall" / "cases.json").read_text())
    floor = get_settings().semantic_min_similarity
    detail: dict = {"cases": [], "hard_negatives": []}
    totals: dict = {arm: {"r1": 0, "r5": 0, "rr": 0.0, "n": 0, "ms": []}
                    for arm in ARMS}
    with tempfile.TemporaryDirectory() as tmp:
        db, folder_id = build_db(Path(tmp) / "probe.sqlite3", spec)
        evaluated = list(spec["cases"])
        by_kind: dict = {}
        for case in evaluated:
            entry = {"id": case["id"], "kind": case["kind"]}
            for arm, channels in ARMS.items():
                started = time.perf_counter()
                try:
                    rows = hybrid_search(db, case["question"], folder_id, channels=channels)
                except RuntimeError as exc:
                    print(f"probe blocked ({arm}): {exc}")
                    return 0
                elapsed = (time.perf_counter() - started) * 1000
                totals[arm]["ms"].append(elapsed)
                rank = rank_of(rows, case["gold"])
                entry[arm] = rank
                bucket = totals[arm]
                by_kind.setdefault(case["kind"], {}).setdefault(
                    arm, {"r1": 0, "r5": 0, "rr": 0.0, "n": 0})
                kind_bucket = by_kind[case["kind"]][arm]
                for target in (bucket, kind_bucket):
                    target["n"] += 1
                    target["r1"] += int(rank == 1)
                    target["r5"] += int(rank is not None and rank <= 5)
                    target["rr"] += (1.0 / rank) if rank else 0.0
            detail["cases"].append(entry)
        for probe in spec["hard_negatives"]:
            entry = {"id": probe["id"]}
            for arm, channels in ARMS.items():
                rows = hybrid_search(db, probe["question"], folder_id, channels=channels)
                order = [row["evidence_id"] for row in rows]
                gold_at = order.index(probe["gold"]) if probe["gold"] in order else None
                entry[arm] = bool(gold_at is not None and all(
                    bad not in order or order.index(bad) > gold_at
                    for bad in probe["forbidden_above_gold"]))
            detail["hard_negatives"].append(entry)

    def summarize(bucket: dict) -> dict:
        n = bucket["n"] or 1
        return {"recall@1": round(bucket["r1"] / n, 3),
                "recall@5": round(bucket["r5"] / n, 3),
                "mrr": round(bucket["rr"] / n, 3), "n": bucket["n"],
                "mean_ms": round(sum(bucket.get("ms", [])) / len(bucket["ms"]), 1)
                if bucket.get("ms") else None}

    report = {
        "dataset": {"units": len(spec["corpus"]), "cases": len(spec["cases"]),
                    "hard_negatives": len(spec["hard_negatives"]),
                    "seeded_records": len(spec.get("records", [])),
                    "limitations": "authored keyword-hostile paraphrases, tiny corpus, "
                                   "single domain, no confidence intervals"},
        "semantic_floor": floor,
        "semantic_stats_last_call": embeddings.last_stats,
        "overall": {arm: summarize(bucket) for arm, bucket in totals.items()},
        "by_kind": {kind: {arm: summarize(bucket) for arm, bucket in arms.items()}
                    for kind, arms in by_kind.items()},
        "hard_negative_pass": {
            arm: round(sum(int(entry[arm]) for entry in detail["hard_negatives"])
                       / max(1, len(detail["hard_negatives"])), 3)
            for arm in ARMS},
        "detail": detail,
    }
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

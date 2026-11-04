"""Frozen-suite dry-run runner (offline, deterministic).

Builds each suite corpus in a temp DB, runs the deterministic answer
pipeline (no model calls), records retrieval traces + claim checks,
and writes machine-readable results plus a human summary. Live model
grading is NOT run here; blinded grading helper consumes the traces.
"""
import hashlib
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from yaad.config import Settings
from yaad.db import Database
from yaad.query import hybrid as H
from yaad.query.pipeline import run_pipeline

SUITES = ["suite_academic.json", "suite_project.json", "suite_multimodal.json"]


def freeze_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def build_suite_db(suite: dict, path: Path) -> Database:
    db = Database(path)
    with db.transaction() as con:
        con.execute("INSERT INTO folders(id,path,cloud_consent) VALUES(1,'/suite',0)")
        fid = 0
        for finfo in suite["files"]:
            fid += 1
            kind = finfo.get("kind", "text")
            con.execute("""INSERT INTO files(id,folder_id,path,sha256,kind,size,mtime,status)
                           VALUES(?,1,?,'h',?,10,1.0,'indexed')""",
                        (fid, f"/suite/{finfo['name']}", kind))
            for unit in finfo["units"]:
                con.execute("""INSERT INTO units(file_id,evidence_id,kind,locator,text)
                               VALUES(?,?, 'text','{}',?)""",
                            (fid, unit["evidence_id"], unit["text"]))
    return db


def run_suite(suite_path: Path, out_dir: Path) -> dict:
    suite = json.loads(suite_path.read_text())
    settings = Settings()
    settings.cloud_enabled = False
    with tempfile.TemporaryDirectory() as tmp:
        db = build_suite_db(suite, Path(tmp) / "s.sqlite3")
        traces = []
        for item in suite["questions"]:
            res = run_pipeline(db, item["question"], 1, settings, use_model=False)
            ranked = {ch: H.retrieve_channel(db, item["question"], ch, 10, 1)
                      for ch in ("keyword", "memory")}
            traces.append({
                "id": item["id"], "question": item["question"],
                "expected_status": item["expected_status"],
                "pipeline_status": res.status,
                "answer": res.answer,
                "citations": res.citations,
                "retrieval": ranked,
                "relevant": item.get("relevant", []),
                "stages": [{"stage": s.stage, "model": s.model_used, "ms": s.ms,
                            "detail": s.detail} for s in res.stages],
            })
    payload = {"suite": suite_path.name, "freeze": freeze_hash(suite_path),
               "n": len(traces), "traces": traces,
               "note": "dry-run: deterministic pipeline only; no model calls, no grades"}
    (out_dir / (suite_path.stem + "_dryrun.json")).write_text(json.dumps(payload, indent=2))
    return {"suite": suite_path.name, "freeze": freeze_hash(suite_path), "n": len(traces)}


def main():
    root = Path(__file__).resolve().parents[1]
    out = root / "eval" / "suites" / "results"
    out.mkdir(parents=True, exist_ok=True)
    summary = [run_suite(root / "eval" / "suites" / name, out) for name in SUITES]
    print(json.dumps({"suites": summary}, indent=2))


if __name__ == "__main__":
    main()

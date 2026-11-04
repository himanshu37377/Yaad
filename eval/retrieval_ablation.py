"""Offline retrieval ablation: keyword vs keyword+memory vs +semantic.

Builds a tiny frozen corpus in a temp DB, runs each channel combo
through hybrid_search, and reports rank metrics + latency + cache
stats. Semantic arm runs only when fastembed is installed AND
YAAD_SEMANTIC_RETRIEVAL=true; otherwise it is reported as disabled
rather than faked.
"""
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from yaad.config import Settings
from yaad.db import Database
from yaad.query import hybrid as H
from yaad.query.retrieval_metrics import evaluate_run

CORPUS = [
    ("f1.txt", "e1", "Autumn Fest 2026 will be held at Riverside Hall on November 12."),
    ("f2.txt", "e2", "Revised venue is Grand Arena for Autumn Fest."),
    ("f3.txt", "e3", "The workshop registration fee is Rs 150 per person."),
    ("f4.txt", "e4", "Hostel mess dinner timing is 7:30 PM on weekdays."),
    ("f5.txt", "e5", "Project standup moved from Monday to Tuesday morning."),
    ("f6.txt", "e6", "The library closes at 9 PM during examinations."),
]

CASES = [
    {"q": "Where will Autumn Fest be held?", "relevant": {"e1", "e2"}},
    {"q": "What is the new venue for the fest?", "relevant": {"e2"}},
    {"q": "How much is the workshop registration fee?", "relevant": {"e3"}},
    {"q": "When is dinner in the hostel mess?", "relevant": {"e4"}},
    {"q": "When is the project standup now?", "relevant": {"e5"}},
    {"q": "What time does the library shut during exams?", "relevant": {"e6"}},
    {"q": "What is the event coordinator contact number?", "relevant": set()},
]


def build_db(path):
    db = Database(path)
    with db.transaction() as con:
        con.execute("INSERT INTO folders(id,path,cloud_consent) VALUES(1,'/abl',0)")
        for idx, (name, eid, text) in enumerate(CORPUS, start=1):
            con.execute("""INSERT INTO files(id,folder_id,path,sha256,kind,size,mtime,status)
                           VALUES(?,1,?, 'h','text',10,1.0,'indexed')""", (idx, f"/abl/{name}"))
            con.execute("INSERT INTO units(file_id,evidence_id,kind,locator,text) VALUES(?,?,'text','{}',?)",
                        (idx, eid, text))
    return db


def run_arm(db, channels):
    runs, lat = [], []
    for case in CASES:
        t0 = time.perf_counter()
        rows = H.hybrid_search(db, case["q"], 1, channels=channels)
        lat.append((time.perf_counter() - t0) * 1000)
        runs.append({"ranked": [r["evidence_id"] for r in rows],
                     "relevant": set(case["relevant"]), "cutoff": 5})
    metrics = evaluate_run(runs)
    metrics["p50_latency_ms"] = round(sorted(lat)[len(lat) // 2], 2)
    metrics["channels"] = list(channels)
    return metrics


def main():
    settings = Settings()
    with tempfile.TemporaryDirectory() as tmp:
        db = build_db(Path(tmp) / "abl.sqlite3")
        arms = {"keyword_only": ("keyword",), "keyword_memory": ("keyword", "memory")}
        if settings.semantic_retrieval:
            try:
                import fastembed  # noqa
                arms["keyword_memory_semantic"] = ("keyword", "memory", "semantic")
                arms["_semantic_note"] = "enabled"
            except ImportError:
                arms["_semantic_note"] = "disabled: fastembed not installed"
        else:
            arms["_semantic_note"] = "disabled: YAAD_SEMANTIC_RETRIEVAL=false (default)"
        note = arms.pop("_semantic_note")
        report = {"n_cases": len(CASES), "semantic": note, "arms": {}}
        for name, channels in arms.items():
            report["arms"][name] = run_arm(db, channels)
        try:
            from yaad.query import embeddings as E
            report["embedding_cache"] = dict(E.last_stats)
        except Exception:
            report["embedding_cache"] = {}
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()

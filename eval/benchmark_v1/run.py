"""Frozen benchmark v1 runner (offline-first).

Default mode is a full offline run: validates the frozen fixtures and
freeze hash, builds one temporary database per corpus folder, seeds
2-4 memory records per folder, runs three deterministic arms with cloud
disabled ($0 spend, zero model calls), and writes transcript.jsonl plus
per-arm scores. --live adds two model arms with a spend gate:
"model" (real run_pipeline with model stages on) and "production"
(cloud indexing + live extraction + hybrid answering with semantic
review). Every model step is pre-flighted; the run stops an arm
rather than exceed --max-spend.

Arms: "pipeline" (the real run_pipeline, deterministic), "keyword"
(answer_question over the keyword channel only), "hybrid" (full
answer_question over keyword+memory).
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
CORPUS = HERE / "corpus"
CASES_PATH = HERE / "cases.json"
MANIFEST_PATH = HERE / "manifest.json"
DEFAULT_RESULTS = HERE / "results"

ARMS = ("pipeline", "keyword", "hybrid")
LIVE_ARMS = ("model", "production")

# Pre-flight uppers per live step (documented pessimistic bounds, not
# predictions; observed verify/review calls run ~$0.004). A step runs
# only when its upper fits the remaining budget.
MODEL_CASE_PREFLIGHT_USD = 0.02
PROD_EXTRACT_FOLDER_PREFLIGHT_USD = 0.06
PROD_CASE_PREFLIGHT_USD = 0.02
PROD_EXTRACT_FOLDER_CAP_USD = 0.10

# Seeded memory records per folder: 2-4 each, exercising Tier A serving.
# key_phrase words must appear in the cited unit text(s); matters are kept
# disjoint within a folder so unrelated records never auto-rival. Records
# marked "superseded" are flipped after validated intake (history taint).
SEEDS = {
    "paraphrase": [
        {"files": ["venue_notice.txt"], "record_type": "decision", "status": "decided",
         "statement_kind": "explicit", "title": "Spring Fest venue",
         "detail": "The Spring Fest venue is Crystal Pavilion.",
         "key_phrase": "spring venue pavilion"},
        {"files": ["venue_notice.txt"], "record_type": "decision", "status": "decided",
         "statement_kind": "explicit", "title": "Spring Fest budget",
         "detail": "The Spring Fest budget is Rs 28,000.",
         "key_phrase": "fest budget"},
        {"files": ["venue_notice.txt"], "record_type": "decision", "status": "decided",
         "statement_kind": "explicit", "title": "Registration deadline",
         "detail": "Registration will close on the deadline date 2027-04-09.",
         "key_phrase": "registration deadline", "date_raw": "2027-04-09"},
        {"files": ["old_draft.txt"], "record_type": "decision", "status": "decided",
         "statement_kind": "explicit", "title": "Superseded fest draft",
         "detail": "The superseded draft put Spring Fest at Riverside Hall with Rs 25,000, closing 2027-03-01.",
         "key_phrase": "riverside draft", "flip": "superseded"},
    ],
    "near_dup": [
        {"files": ["venues.txt"], "record_type": "decision", "status": "decided",
         "statement_kind": "explicit", "title": "Spring Fest venue",
         "detail": "Spring Fest takes place at Crystal Pavilion.",
         "key_phrase": "pavilion venue spring"},
        {"files": ["people.txt"], "record_type": "decision", "status": "decided",
         "statement_kind": "explicit", "title": "Fest people",
         "detail": "Priya Nair is the fest treasurer; Priya Menon is the volunteer handling decorations; Dr. Rao heads the council.",
         "key_phrase": "nair treasurer menon volunteer council", "assignee": "Priya Nair"},
        {"files": ["fees.txt"], "record_type": "decision", "status": "decided",
         "statement_kind": "explicit", "title": "Stall fee",
         "detail": "The Spring Fest stall fee is Rs 1,200 per stall.",
         "key_phrase": "stall fee"},
        {"files": ["fees.txt"], "record_type": "decision", "status": "decided",
         "statement_kind": "explicit", "title": "Late registration fee",
         "detail": "The late registration fee is Rs 1,500.",
         "key_phrase": "late registration"},
    ],
    "contradiction": [
        {"files": ["source_a.txt"], "record_type": "decision",
         "status": "decided", "statement_kind": "explicit", "title": "Catering cost",
         "detail": "The catering quote from the caterer is Rs 8,000 for Spring Fest.",
         "key_phrase": "spring catering",
         "excerpt": "The caterer quotes Rs 8,000 for Spring Fest catering."},
        {"files": ["source_b.txt"], "record_type": "decision",
         "status": "decided", "statement_kind": "explicit", "title": "Catering cost",
         "detail": "The catering quote from the committee is Rs 9,500 for Spring Fest.",
         "key_phrase": "spring catering",
         "excerpt": "The committee notes Rs 9,500 for Spring Fest catering."},
        {"files": ["source_a.txt"], "record_type": "decision",
         "status": "decided", "statement_kind": "explicit", "title": "Venue hire cost",
         "detail": "Venue hire is Rs 18,000 for Spring Fest.",
         "key_phrase": "venue hire",
         "excerpt": "Venue hire is Rs 18,000."},
        {"files": ["source_b.txt"], "record_type": "decision",
         "status": "decided", "statement_kind": "explicit", "title": "Venue hire cost",
         "detail": "Venue hire is Rs 21,000 for Spring Fest.",
         "key_phrase": "venue hire",
         "excerpt": "Venue hire is Rs 21,000."},
        {"files": ["source_a.txt"], "record_type": "decision",
         "status": "decided", "statement_kind": "explicit", "title": "Registration closing date",
         "detail": "Registration will close 2027-04-09 for Spring Fest.",
         "key_phrase": "registration closes", "date_value": "2027-04-09",
         "date_raw": "2027-04-09",
         "excerpt": "Registration closes 2027-04-09."},
        {"files": ["source_b.txt"], "record_type": "decision",
         "status": "decided", "statement_kind": "explicit", "title": "Registration closing date",
         "detail": "Registration will close 2027-04-11 for Spring Fest.",
         "key_phrase": "registration closes", "date_value": "2027-04-11",
         "date_raw": "2027-04-11",
         "excerpt": "Registration closes 2027-04-11."},
        {"files": ["memo.txt"], "record_type": "decision", "status": "decided",
         "statement_kind": "explicit", "title": "Snack budget",
         "detail": "The committee decided the snack budget is Rs 3,000.",
         "key_phrase": "snack budget"},
    ],
    "temporal": [
        {"files": ["final.txt"], "record_type": "decision", "status": "decided",
         "statement_kind": "explicit", "title": "Spring Fest venue",
         "detail": "The Spring Fest venue is Crystal Pavilion.",
         "key_phrase": "spring venue pavilion"},
        {"files": ["final.txt"], "record_type": "decision", "status": "decided",
         "statement_kind": "explicit", "title": "Spring Fest budget",
         "detail": "The Spring Fest budget is still Rs 28,000.",
         "key_phrase": "fest budget"},
        {"files": ["final.txt"], "record_type": "decision", "status": "decided",
         "statement_kind": "explicit", "title": "Registration deadline",
         "detail": "Registration will close on the deadline date 2027-04-09; registration is no longer closing in March.",
         "key_phrase": "registration deadline", "date_raw": "2027-04-09"},
        {"files": ["history.txt", "update.txt"], "record_type": "decision",
         "status": "decided", "statement_kind": "explicit",
         "title": "Original and interim fest deadlines",
         "detail": "The original registration deadline was 2027-03-01; the interim update set 2027-04-02.",
         "key_phrase": "original riverside march grand",
         "date_value": "2027-03-01", "date_raw": "2027-03-01", "flip": "superseded"},
    ],
    "multi_doc": [
        {"files": ["hire.txt"], "record_type": "decision", "status": "decided",
         "statement_kind": "explicit", "title": "Venue hire cost",
         "detail": "The venue hire cost is Rs 18,000 for Spring Fest.",
         "key_phrase": "venue hire"},
        {"files": ["catering.txt"], "record_type": "decision", "status": "decided",
         "statement_kind": "explicit", "title": "Catering cost",
         "detail": "Spring Fest catering cost is Rs 8,000 through the fest caterer.",
         "key_phrase": "fest catering"},
        {"files": ["schedule.txt"], "record_type": "decision", "status": "decided",
         "statement_kind": "explicit", "title": "Poster session",
         "detail": "The poster session opens at 10am in Seminar Room B.",
         "key_phrase": "poster session"},
        {"files": ["sound.txt"], "record_type": "decision", "status": "decided",
         "statement_kind": "explicit", "title": "Sound setup",
         "detail": "Arjun handles the sound and audio setup for Spring Fest.",
         "key_phrase": "sound setup", "assignee": "Arjun"},
    ],
    "missing_evidence": [
        {"files": ["basics.txt"], "record_type": "decision", "status": "decided",
         "statement_kind": "explicit", "title": "Spring Fest venue",
         "detail": "Spring Fest takes place at the Crystal Pavilion venue.",
         "key_phrase": "spring venue pavilion"},
        {"files": ["basics.txt"], "record_type": "decision", "status": "decided",
         "statement_kind": "explicit", "title": "Registration deadline",
         "detail": "The registration deadline is 2027-04-09.",
         "key_phrase": "registration deadline", "date_raw": "2027-04-09"},
    ],
    "injection": [
        {"files": ["notice.txt"], "record_type": "decision", "status": "decided",
         "statement_kind": "explicit", "title": "Spring Fest venue",
         "detail": "Spring Fest takes place at Crystal Pavilion.",
         "key_phrase": "spring venue pavilion"},
        {"files": ["volunteer.txt"], "record_type": "decision", "status": "decided",
         "statement_kind": "explicit", "title": "Spring Fest budget",
         "detail": "Priya confirmed the Spring Fest budget is Rs 28,000.",
         "key_phrase": "fest budget"},
        {"files": ["notice.txt"], "record_type": "decision", "status": "decided",
         "statement_kind": "explicit", "title": "Registration deadline",
         "detail": "Registration closes 2027-04-09; it will close on that date.",
         "key_phrase": "registration closes", "date_raw": "2027-04-09"},
        {"files": ["logistics.txt"], "record_type": "decision", "status": "decided",
         "statement_kind": "explicit", "title": "Poster session",
         "detail": "The poster session opens at 10am in Seminar Room B.",
         "key_phrase": "poster session"},
    ],
    "multimodal": [
        {"files": ["mm_poster_pdf.txt"], "record_type": "decision", "status": "decided",
         "statement_kind": "explicit", "title": "Fest poster",
         "detail": "The Spring Fest poster shows food stalls at the Grand Arena annex; the headline act starts at 7pm.",
         "key_phrase": "food stalls headline act poster"},
        {"files": ["mm_gallery_image.txt"], "record_type": "decision", "status": "decided",
         "statement_kind": "explicit", "title": "Rally caption",
         "detail": "The caption shows a bicycle rally passing Riverside Hall; visible text reads RIDE 2027.",
         "key_phrase": "bicycle rally"},
        {"files": ["mm_voice_memo.txt"], "record_type": "decision", "status": "decided",
         "statement_kind": "explicit", "title": "Voice memo",
         "detail": "Arjun confirms the sound check at 9am. In this voice memo, Priya approves the fest budget of Rs 28,000.",
         "key_phrase": "sound budget", "assignee": "Arjun"},
        {"files": ["mm_schedule_pdf.txt"], "record_type": "decision", "status": "decided",
         "statement_kind": "explicit", "title": "Poster session",
         "detail": "The poster session opens at 10am in Seminar Room B.",
         "key_phrase": "seminar session opens"},
    ],
}


def run_keyword_arm(db, question: str, folder_id: int | None, settings,
                    use_model: bool = False) -> dict:
    """Keyword-only answer path (arm "keyword").

    Keyword-channel retrieval plus the local answer path with cloud
    disabled; never constructs a model client. use_model=True is the
    reserved live path and always refuses in this offline benchmark.
    """
    if use_model:
        raise RuntimeError("live model runs are reserved for later benchmark use")
    from yaad.query.answer import answer_question

    return answer_question(db, question, folder_id, settings,
                           channels=("keyword",))


def run_pipeline_arm(db, question: str, folder_id: int | None, settings,
                     unit_texts: dict[str, str], file_by_eid: dict[str, str],
                     use_model: bool = False, client=None) -> dict:
    """The real bounded pipeline (arm "pipeline", or "model" live).

    Shapes the PipelineResult into the transcript's answer format so
    all arms grade under one rubric. Live use requires a client and a
    pre-flighted budget (see run_live); this function never spends.
    """
    from yaad.query.pipeline import run_pipeline

    res = run_pipeline(db, question, folder_id, settings, client=client,
                       use_model=use_model)
    return {"status": res.status, "answer": res.answer, "memory": {"used": False},
            "model_calls": res.model_calls,
            "evidence": [{"id": eid, "file": file_by_eid.get(eid, ""),
                          "quote": (unit_texts.get(eid, "") or "")[:600]}
                         for eid in res.citations]}


def seed_folder(db, folder_id: int, folder_name: str, run_id: str) -> int:
    """Validated intake of the folder's seed records; returns the count."""
    from yaad.memory.ledger import log_transition, record_state, supports_snapshot
    from yaad.memory.records import upsert_records

    specs = SEEDS[folder_name]
    # Contradiction seeds one record per rival side (three pairs plus a
    # control); every other folder seeds 2-4 records.
    assert 2 <= len(specs) <= 7, f"seed budget violated for {folder_name}"
    with db.connect() as con:
        units = {Path(row["path"]).name: (row["evidence_id"], row["text"])
                 for row in con.execute(
                     "SELECT u.evidence_id,u.text,f.path FROM units u "
                     "JOIN files f ON f.id=u.file_id WHERE f.folder_id=?",
                     (folder_id,))}
    candidates = []
    for spec in specs:
        evidence = []
        for name in spec["files"]:
            eid, text = units[name]
            excerpt = spec.get("excerpt", text)
            evidence.append({"evidence_id": eid, "role": "supports",
                             "excerpt": excerpt})
        candidate = {
            "record_type": spec["record_type"], "status": spec["status"],
            "statement_kind": spec["statement_kind"], "title": spec["title"],
            "detail": spec["detail"], "key_phrase": spec["key_phrase"],
            "date_value": spec.get("date_value"),
            "date_raw": spec.get("date_raw"),
            "assignee": spec.get("assignee"), "evidence": evidence,
        }
        candidates.append(candidate)
    with db.transaction() as con:
        unit_texts = {eid: text for _, (eid, text) in
                      ((n, units[n]) for n in
                       {n for s in specs for n in s["files"]})}
        ids = upsert_records(con, folder_id, run_id, candidates, unit_texts)
        for spec in specs:
            if spec.get("flip") != "superseded":
                continue
            record_id = _record_id_for(con, folder_id, spec["title"])
            row = con.execute("SELECT * FROM memory_records WHERE id=?",
                              (record_id,)).fetchone()
            prev = record_state(row)
            con.execute("UPDATE memory_records SET status='superseded' WHERE id=?",
                        (record_id,))
            fresh = con.execute("SELECT * FROM memory_records WHERE id=?",
                                (record_id,)).fetchone()
            log_transition(con, folder_id=folder_id, record_id=record_id,
                           transition="superseded", prev_state=prev,
                           new_state=record_state(fresh),
                           reason="benchmark v1 seed: stale generation replaced",
                           evidence=supports_snapshot(con, record_id),
                           source=f"benchmark-v1-seed:{run_id}")
    return len(specs)


def _record_id_for(con, folder_id: int, title: str) -> int:
    row = con.execute("SELECT id FROM memory_records WHERE folder_id=? AND title=?",
                      (folder_id, title)).fetchone()
    return row["id"]


def _retrieval_ranking(db, question: str, folder_id: int, channels) -> list[str]:
    from yaad.query.hybrid import hybrid_search

    names: list[str] = []
    for row in hybrid_search(db, question, folder_id, channels=channels):
        name = Path(row["path"]).name
        if name not in names:
            names.append(name)
        if len(names) >= 10:
            break
    return names


def _evidence_cards(result: dict, unit_texts: dict[str, str]) -> list[dict]:
    from yaad.memory.records import normalize_text

    cards = []
    for item in result.get("evidence", []):
        unit = unit_texts.get(item.get("id", ""), "")
        quote = item.get("quote", "") or ""
        cards.append({
            "id": item.get("id", ""),
            "file": Path(item.get("file", "")).name,
            "quote": quote,
            "resolves": item.get("id", "") in unit_texts,
            "quote_contained": bool(quote.strip()) and normalize_text(quote) in normalize_text(unit),
        })
    return cards


def run_offline(results_dir: Path, here: Path = HERE,
                folders: list[str] | None = None) -> dict:
    """Full offline run: temp DBs, seeded records, both arms, scored."""
    from yaad.config import Settings
    from yaad.db import Database
    from yaad.query.answer import answer_question
    from yaad.scan.worker import Indexer
    from benchmark_score import check_frozen, load_cases, score_case, summarize_arm

    sys.path.insert(0, str(here))
    problems = check_frozen(here / "cases.json", here / "manifest.json",
                            here / "corpus")
    if problems:
        raise RuntimeError("frozen check failed: " + "; ".join(problems))
    cases_data = load_cases(here / "cases.json")
    cases = cases_data["cases"]
    by_id = {c["id"]: c for c in cases}
    transcript: list[dict] = []
    seeded: dict[str, int] = {}
    egress_total = 0
    wanted = set(folders) if folders else None
    with tempfile.TemporaryDirectory(prefix="benchmark-v1-") as temp:
        base = Path(temp)
        for folder_name in cases_data["folders"]:
            if wanted is not None and folder_name not in wanted:
                continue
            db_path = base / f"{folder_name}.sqlite3"
            db = Database(db_path)
            settings = Settings(db_path=db_path, cloud_enabled=False,
                                model_provider="cloud", model_api_key=None)
            indexer = Indexer(db, settings)
            folder_id = indexer.add_folder(str(here / "corpus" / folder_name), True)
            indexer.index_folder(folder_id)
            seeded[folder_name] = seed_folder(db, folder_id, folder_name,
                                              f"benchmark-v1-{folder_name}")
            with db.connect() as con:
                unit_rows = con.execute(
                    "SELECT u.evidence_id,u.text,f.path FROM units u "
                    "JOIN files f ON f.id=u.file_id").fetchall()
                unit_texts = {r["evidence_id"]: r["text"] for r in unit_rows}
                file_by_eid = {r["evidence_id"]: r["path"] for r in unit_rows}
            for case in [c for c in cases if c["folder"] == folder_name]:
                question = case["question"]
                arms = {
                    "pipeline": run_pipeline_arm(db, question, folder_id, settings,
                                                 unit_texts, file_by_eid,
                                                 use_model=False),
                    "keyword": run_keyword_arm(db, question, folder_id, settings,
                                               use_model=False),
                    "hybrid": answer_question(db, question, folder_id, settings),
                }
                rankings = {
                    "pipeline": _retrieval_ranking(db, question, folder_id, None),
                    "keyword": _retrieval_ranking(db, question, folder_id, ("keyword",)),
                    "hybrid": _retrieval_ranking(db, question, folder_id, None),
                }
                for arm in ARMS:
                    result = arms[arm]
                    memory = result.get("memory") or {}
                    transcript.append({
                        "id": case["id"], "arm": arm, "category": case["category"],
                        "folder": folder_name, "question": question,
                        "status": result.get("status"),
                        "answer": result.get("answer"),
                        "evidence": _evidence_cards(result, unit_texts),
                        "retrieval": rankings[arm],
                        "memory_used": bool(memory.get("used")),
                    })
            with db.connect() as con:
                egress_total += con.execute("SELECT COUNT(*) AS n FROM egress_log").fetchone()["n"]
    if egress_total:
        raise RuntimeError(f"offline violation: {egress_total} egress receipts logged")
    scored = {arm: [score_case(row, by_id[row["id"]])
                    for row in transcript if row["arm"] == arm] for arm in ARMS}
    summaries = {arm: summarize_arm(scored[arm], cases) for arm in ARMS}
    scores = {
        "arms": summaries,
        "false_supported_rate": {
            arm: _false_supported(transcript, arm) for arm in ARMS},
        "meta": {"benchmark": "v1", "frozen": True,
                 "freeze_hash": cases_data["freeze_hash"],
                 "cases": len(cases), "seeded_records": seeded,
                 "egress_total": egress_total, "offline": True},
    }
    results_dir = Path(results_dir)
    results_dir.mkdir(parents=True, exist_ok=True)
    (results_dir / "transcript.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in transcript),
        encoding="utf-8")
    (results_dir / "scores.json").write_text(
        json.dumps(scores, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (results_dir / "report.md").write_text(_report(scores), encoding="utf-8")
    return scores


def _ledger(db) -> float:
    with db.connect() as con:
        return float(con.execute(
            "SELECT COALESCE(SUM(estimated_cost_usd),0) AS s FROM egress_log"
        ).fetchone()["s"])


def run_live(results_dir: Path, max_spend: float, here: Path = HERE,
           folders: list[str] | None = None,
           live_arms: list[str] | None = None) -> dict:
    """Live run: model + production arms with pre-flight spend gates.

    Raises RuntimeError (no spend) when the API key is missing. Stops
    an arm rather than exceed max_spend; stopped arms keep partial
    transcripts and the report says where and why.
    """
    import os

    from benchmark_score import check_frozen, load_cases, score_case, summarize_arm
    from yaad.config import Settings
    from yaad.db import Database
    from yaad.llm.meta_client import MetaClient
    from yaad.memory.extract import run_extraction
    from yaad.query.answer import answer_question
    from yaad.scan.worker import Indexer

    sys.path.insert(0, str(here))
    problems = check_frozen(here / "cases.json", here / "manifest.json",
                            here / "corpus")
    if problems:
        raise RuntimeError("frozen check failed: " + "; ".join(problems))
    if not os.environ.get("MODEL_API_KEY"):
        raise RuntimeError("live run refused: MODEL_API_KEY is not set")
    cases_data = load_cases(here / "cases.json")
    cases = cases_data["cases"]
    by_id = {c["id"]: c for c in cases}
    transcript: list[dict] = []
    spent = 0.0
    stopped: dict[str, str] = {}
    wanted = set(folders) if folders else None
    arms = list(live_arms) if live_arms else list(LIVE_ARMS)

    def fits(upper: float) -> bool:
        return spent + upper <= max_spend

    with tempfile.TemporaryDirectory(prefix="benchmark-v1-live-") as temp:
        base = Path(temp)
        for folder_name in cases_data["folders"]:
            if wanted is not None and folder_name not in wanted:
                continue
            db_path = base / f"{folder_name}.sqlite3"
            db = Database(db_path)
            settings = Settings(db_path=db_path, cloud_enabled=True,
                                model_provider="cloud",
                                model_api_key=os.environ["MODEL_API_KEY"],
                                model="muse-spark-1.3",
                                cloud_spend_cap_usd=max_spend)
            client = MetaClient(settings)
            indexer = Indexer(db, settings)
            folder_id = indexer.add_folder(str(here / "corpus" / folder_name), True)
            indexer.index_folder(folder_id)
            seed_folder(db, folder_id, folder_name, f"benchmark-v1-{folder_name}")
            folder_cases = [c for c in cases if c["folder"] == folder_name]
            # --- model arm: real pipeline with model stages on ---
            if "model" in arms and "model" not in stopped:
                for case in folder_cases:
                    if not fits(MODEL_CASE_PREFLIGHT_USD):
                        stopped["model"] = (
                            f"budget: ${spent:.4f} spent, next pre-flight "
                            f"${MODEL_CASE_PREFLIGHT_USD} exceeds ${max_spend} cap")
                        break
                    question = case["question"]
                    before = _ledger(db)
                    try:
                        result = run_pipeline_arm(
                            db, question, folder_id, settings,
                            _unit_texts(db), _file_by_eid(db),
                            use_model=True, client=client)
                    except Exception as exc:
                        result = {"status": "error", "answer": "",
                                  "evidence": [],
                                  "error": f"{type(exc).__name__}: {exc}"[:200]}
                    spent += _ledger(db) - before
                    transcript.append(_live_row(
                        case, "model", folder_name, question, result, db,
                        _ledger(db) - before))
            # --- production arm: live extraction + hybrid answer w/ review ---
            if "production" in arms and "production" not in stopped:
                if not fits(PROD_EXTRACT_FOLDER_PREFLIGHT_USD):
                    stopped["production"] = (
                        f"budget: ${spent:.4f} spent, extraction pre-flight "
                        f"${PROD_EXTRACT_FOLDER_PREFLIGHT_USD} exceeds "
                        f"${max_spend} cap")
                else:
                    before = _ledger(db)
                    try:
                        run_extraction(db, folder_id, f"benchmark-v1-live-{folder_name}",
                                       settings, client,
                                       max_spend_usd=PROD_EXTRACT_FOLDER_CAP_USD)
                    except Exception as exc:
                        stopped.setdefault(
                            "production_extraction",
                            f"{folder_name}: {type(exc).__name__}: {exc}"[:200])
                    spent += _ledger(db) - before
                    for case in folder_cases:
                        if not fits(PROD_CASE_PREFLIGHT_USD):
                            stopped["production"] = (
                                f"budget: ${spent:.4f} spent, next pre-flight "
                                f"${PROD_CASE_PREFLIGHT_USD} exceeds ${max_spend} cap")
                            break
                        question = case["question"]
                        before = _ledger(db)
                        try:
                            result = answer_question(db, question, folder_id, settings)
                        except Exception as exc:
                            result = {"status": "error", "answer": "",
                                      "evidence": [],
                                      "error": f"{type(exc).__name__}: {exc}"[:200]}
                        delta = _ledger(db) - before
                        spent += delta
                        transcript.append(_live_row(
                            case, "production", folder_name, question, result,
                            db, delta))
    scored = {arm: [score_case(row, by_id[row["id"]])
                    for row in transcript if row["arm"] == arm] for arm in arms}
    summaries = {arm: summarize_arm(scored[arm], cases) for arm in arms}
    scores = {
        "arms": summaries,
        "false_supported_rate": {
            arm: _false_supported(transcript, arm) for arm in arms},
        "meta": {"benchmark": "v1", "frozen": True,
                 "freeze_hash": cases_data["freeze_hash"],
                 "cases": len(cases), "offline": False,
                 "model": "muse-spark-1.3", "max_spend_usd": max_spend,
                 "spent_usd": round(spent, 6), "stopped": stopped},
    }
    results_dir = Path(results_dir)
    results_dir.mkdir(parents=True, exist_ok=True)
    (results_dir / "transcript.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in transcript),
        encoding="utf-8")
    (results_dir / "scores.json").write_text(
        json.dumps(scores, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (results_dir / "report.md").write_text(_live_report(scores), encoding="utf-8")
    return scores


def _unit_texts(db) -> dict[str, str]:
    with db.connect() as con:
        return {r["evidence_id"]: r["text"] for r in con.execute(
            "SELECT evidence_id,text FROM units")}


def _file_by_eid(db) -> dict[str, str]:
    with db.connect() as con:
        return {r["evidence_id"]: r["path"] for r in con.execute(
            "SELECT u.evidence_id,f.path AS path FROM units u "
            "JOIN files f ON f.id=u.file_id")}


def _live_row(case, arm, folder_name, question, result, db, spend_usd) -> dict:
    with db.connect() as con:
        unit_texts = {r["evidence_id"]: r["text"] for r in con.execute(
            "SELECT evidence_id,text FROM units")}
    return {
        "id": case["id"], "arm": arm, "category": case["category"],
        "folder": folder_name, "question": question,
        "status": result.get("status"), "answer": result.get("answer"),
        "evidence": _evidence_cards(result, unit_texts),
        "retrieval": _retrieval_ranking(db, question,
                                        _folder_id(db, folder_name), None),
        "memory_used": bool((result.get("memory") or {}).get("used")),
        "model_calls": result.get("model_calls", 0),
        "spend_usd": round(spend_usd, 6),
        "live": True,
    }


def _folder_id(db, folder_name: str) -> int | None:
    with db.connect() as con:
        row = con.execute("SELECT id FROM folders WHERE path LIKE ?",
                          (f"%{folder_name}",)).fetchone()
        return row["id"] if row else None


def _live_report(scores: dict) -> str:
    meta = scores["meta"]
    lines = ["# Benchmark v1 - live results", ""]
    lines.append(f"freeze: `{meta['freeze_hash']}` - {meta['cases']} cases, "
                 f"model {meta['model']}, spent ${meta['spent_usd']} "
                 f"(cap ${meta['max_spend_usd']}).")
    if meta["stopped"]:
        lines.append("")
        for arm, reason in meta["stopped"].items():
            lines.append(f"STOPPED {arm}: {reason}")
    lines.append("")
    for arm in scores["arms"]:
        summary = scores["arms"][arm]
        lines.append(f"## {arm}: {summary['passed']}/{summary['total']} "
                     f"({summary['accuracy']:.1%})")
        for category, bucket in sorted(summary["by_category"].items()):
            lines.append(f"- {category}: {bucket['passed']}/{bucket['n']}")
        retrieval = summary["retrieval"]

        def _fmt(value) -> str:
            return "n/a" if value is None else f"{value:.3f}"

        lines.append(f"- retrieval: Recall@1 {_fmt(retrieval['recall_at_1'])}, "
                     f"Recall@5 {_fmt(retrieval['recall_at_5'])}, "
                     f"MRR {_fmt(retrieval['mrr'])}, "
                     f"hard-negative pass {_fmt(retrieval['hard_negative_pass'])}")
        lines.append(f"- abstention rate: {_fmt(summary['abstention_rate'])}; "
                     f"false-supported rate: "
                     f"{_fmt(scores['false_supported_rate'][arm])}")
        lines.append(f"- failures: {', '.join(summary['failures']) or 'none'}")
        lines.append("")
    return "\n".join(lines)


def _false_supported(transcript: list[dict], arm: str) -> float | None:
    rows = [r for r in transcript if r["arm"] == arm and r["category"] == "missing_evidence"]
    if not rows:
        return None
    return sum(1 for r in rows if r["status"] in ("answered", "conflict")) / len(rows)


def _report(scores: dict) -> str:
    lines = ["# Benchmark v1 - offline results", ""]
    lines.append(f"freeze: `{scores['meta']['freeze_hash']}` - "
                 f"{scores['meta']['cases']} cases, egress: "
                 f"{scores['meta']['egress_total']} (offline).")
    lines.append("")
    for arm in ARMS:
        summary = scores["arms"][arm]
        lines.append(f"## {arm}: {summary['passed']}/{summary['total']} "
                     f"({summary['accuracy']:.1%})")
        for category, bucket in sorted(summary["by_category"].items()):
            lines.append(f"- {category}: {bucket['passed']}/{bucket['n']}")
        retrieval = summary["retrieval"]
        lines.append(f"- retrieval: Recall@1 {retrieval['recall_at_1']:.3f}, "
                     f"Recall@5 {retrieval['recall_at_5']:.3f}, "
                     f"MRR {retrieval['mrr']:.3f}, "
                     f"hard-negative pass {retrieval['hard_negative_pass']:.3f}")
        lines.append(f"- citations: resolve {summary['citation_resolves_rate']:.3f}, "
                     f"contained {summary['citation_contained_rate']:.3f}, "
                     f"complete {summary['citation_complete_rate']} "
                     f"(n={summary['citation_complete_n']})")
        lines.append(f"- abstention rate: {summary['abstention_rate']:.3f}; "
                     f"false-supported rate: "
                     f"{scores['false_supported_rate'][arm]:.3f}")
        lines.append(f"- failures: {', '.join(summary['failures']) or 'none'}")
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true",
                        help="Reserved: live model runs (not implemented offline).")
    parser.add_argument("--max-spend", type=float, default=0.0,
                        help="Hard spend cap in USD (required with --live).")
    parser.add_argument("--results-dir", type=Path, default=DEFAULT_RESULTS,
                        help="Where to write transcript.jsonl and scores.")
    parser.add_argument("--folders", type=str, default="",
                        help="Comma-separated folder subset (live smoke tests).")
    parser.add_argument("--live-arms", type=str, default="model,production",
                        help="Comma-separated live arms to run.")
    args = parser.parse_args(argv)
    if not args.live:
        from benchmark_score import check_frozen

        sys.path.insert(0, str(HERE))
        problems = check_frozen(CASES_PATH, MANIFEST_PATH, CORPUS)
        if problems:
            print("FROZEN CHECK FAILED:")
            for problem in problems:
                print(" -", problem)
            return 1
        print("Frozen check OK: 72 cases, 8 folders, $0 offline run.")
        only = [f.strip() for f in args.folders.split(",") if f.strip()] or None
        scores = run_offline(args.results_dir, folders=only)
        for arm in ARMS:
            summary = scores["arms"][arm]
            print(f"{arm}: {summary['passed']}/{summary['total']} "
                  f"passed; failures: {', '.join(summary['failures']) or 'none'}")
        print(f"Wrote {args.results_dir}/transcript.jsonl, scores.json, report.md")
        return 0
    if args.max_spend <= 0:
        parser.error("--live requires --max-spend > 0")
    only = [f.strip() for f in args.folders.split(",") if f.strip()] or None
    arms = [a.strip() for a in args.live_arms.split(",") if a.strip()]
    unknown = [a for a in arms if a not in LIVE_ARMS]
    if unknown:
        parser.error(f"unknown live arms: {unknown}")
    try:
        scores = run_live(args.results_dir, args.max_spend, folders=only,
                          live_arms=arms)
    except RuntimeError as exc:
        print(f"Live run refused: {exc} (no spend incurred).")
        return 3
    for arm in scores["arms"]:
        summary = scores["arms"][arm]
        print(f"{arm}: {summary['passed']}/{summary['total']} "
              f"passed; failures: {', '.join(summary['failures']) or 'none'}")
    print(f"spent ${scores['meta']['spent_usd']} (cap ${args.max_spend})")
    print(f"Wrote {args.results_dir}/transcript.jsonl, scores.json, report.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

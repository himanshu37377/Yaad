"""Tiny development harness. Results are not broad reliability claims."""
import json
import tempfile
from pathlib import Path
from yaad.config import Settings
from yaad.db import Database
from yaad.scan.worker import Indexer
from yaad.query.answer import answer_question

def main():
    base = Path("eval/generated")
    questions = base / "questions.jsonl"
    if not questions.exists():
        raise SystemExit("Run python scripts/make_synthetic_data.py first.")
    with tempfile.TemporaryDirectory() as temp:
        db = Database(Path(temp) / "eval.sqlite3")
        indexer = Indexer(db, Settings(db_path=Path(temp)/"eval.sqlite3"))
        folder_id = indexer.add_folder(str(base), False)
        indexer.index_folder(folder_id)
        rows = [json.loads(line) for line in questions.read_text().splitlines()]
        correct = abstain = 0
        for item in rows:
            answer = answer_question(db, item["question"], folder_id)
            if item["value"] is None:
                abstain += answer["status"] == "not_found"
            else:
                correct += any(item["value"] in e["quote"] and Path(e["file"]).name == item["file"] for e in answer["evidence"])
        answerable = sum(q["value"] is not None for q in rows)
        unanswerable = len(rows)-answerable
        print(f"PDF/text retrieval evidence match: {correct}/{answerable} (n={answerable})")
        print(f"Abstention: {abstain}/{unanswerable} (n={unanswerable})")
        print("Development data only; no model output or real-user data included.")

if __name__ == "__main__":
    main()

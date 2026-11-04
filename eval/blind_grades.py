"""Blinded manual grading for eval transcripts.

`prepare` strips arm labels and question ids from a transcript, shuffles
with a seed, and emits a grading sheet plus a sealed key. `merge`
reattaches human grades to their questions. Pre-registered expectations
travel with the sheet, so graders judge against frozen criteria — never
against whichever arm they prefer. Flags are parsed first; both
commands only read their inputs.
"""
import argparse
import json
import random
import sys
from pathlib import Path


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def prepare(transcript: list[dict], expected: dict, seed: int) -> tuple[list[dict], dict]:
    questions = {q["id"]: q for q in expected["questions"]}
    rows = []
    for index, row in enumerate(transcript):
        question = questions.get(row["id"], {})
        rows.append({
            "blind_id": f"B{index + 1:03d}",
            "question": row["question"],
            "answer": (row.get("result") or {}).get("answer", ""),
            "status": (row.get("result") or {}).get("status", ""),
            "evidence": (row.get("result") or {}).get("evidence", []),
            "expected_status": question.get("expect_status", []),
            "expected_value": {key: question[key] for key in
                               ("current_value", "current_amount", "current_date")
                               if key in question},
            "category": question.get("category", ""),
        })
    rng = random.Random(seed)
    order = list(range(len(rows)))
    rng.shuffle(order)
    sheet = [rows[i] for i in order]
    key = {rows[i]["blind_id"]: {"id": transcript[i]["id"], "arm": transcript[i]["arm"]}
           for i in order}
    return sheet, key


def merge(key: dict, grades: list[dict]) -> list[dict]:
    merged = []
    for grade in grades:
        if grade["blind_id"] not in key:
            raise ValueError(f"unknown blind id: {grade['blind_id']}")
        if grade.get("score") not in (0, 0.5, 1):
            raise ValueError(f"score must be 0, 0.5, or 1: {grade!r}")
        merged.append({"id": key[grade["blind_id"]]["id"],
                       "arm": key[grade["blind_id"]]["arm"],
                       "score": grade["score"],
                       "stage": grade.get("stage", ""),
                       "note": grade.get("note", "")})
    return merged


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Blind and merge manual eval grades.")
    sub = parser.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prepare", help="emit a shuffled blind grading sheet")
    prep.add_argument("transcript", type=Path)
    prep.add_argument("expected", type=Path)
    prep.add_argument("out_dir", type=Path)
    prep.add_argument("--seed", type=int, default=7)
    mg = sub.add_parser("merge", help="reattach grades to questions")
    mg.add_argument("key", type=Path)
    mg.add_argument("grades", type=Path)
    mg.add_argument("out", type=Path)
    args = parser.parse_args(argv)
    if args.command == "prepare":
        expected = json.loads(args.expected.read_text())
        sheet, key = prepare(load_jsonl(args.transcript), expected, args.seed)
        args.out_dir.mkdir(parents=True, exist_ok=True)
        (args.out_dir / "grading_sheet.json").write_text(json.dumps(sheet, indent=2))
        (args.out_dir / "blind_key.json").write_text(json.dumps(key, indent=2))
        print(f"sheet: {len(sheet)} blinded answers in {args.out_dir}")
    else:
        merged = merge(json.loads(args.key.read_text()),
                       json.loads(args.grades.read_text()))
        args.out.write_text(json.dumps(merged, indent=2))
        print(f"merged: {len(merged)} grades in {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

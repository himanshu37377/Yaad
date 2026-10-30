"""Deterministic scoring for the real-model evaluation.

Pure functions over recorded transcript entries; no model calls, no
network. Scores answer status, required/forbidden content, and citation
validity separately so synthesis errors are not confused with retrieval
or extraction errors.
"""
import json
from pathlib import Path


def load_expected(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def score_answer(entry: dict, expected: dict) -> dict:
    """entry: {status, answer, evidence_ids_resolve: bool}."""
    answer = (entry.get("answer") or "").casefold()
    status_ok = entry.get("status") in expected.get("expect_status", [])
    contains = all(term.casefold() in answer for term in expected.get("must_contain", []))
    clean = all(term.casefold() not in answer for term in expected.get("must_not_contain", []))
    return {"id": expected["id"], "category": expected.get("category", ""),
            "status_ok": status_ok, "contains_ok": contains,
            "no_forbidden": clean,
            "citations_ok": bool(entry.get("evidence_ids_resolve", False)),
            "pass": bool(status_ok and contains and clean)}


def summarize(scored: list[dict]) -> dict:
    by_category: dict[str, dict] = {}
    for item in scored:
        bucket = by_category.setdefault(item["category"], {"n": 0, "passed": 0})
        bucket["n"] += 1
        bucket["passed"] += int(item["pass"])
    return {"total": len(scored), "passed": sum(int(s["pass"]) for s in scored),
            "by_category": by_category,
            "failures": [s["id"] for s in scored if not s["pass"]]}

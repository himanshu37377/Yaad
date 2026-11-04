"""Rank-based retrieval metrics (offline, deterministic)."""
from __future__ import annotations


def recall_at_k(ranked: list[str], relevant: set[str], k: int) -> float:
    if not relevant:
        return 1.0
    return len(set(ranked[:k]) & relevant) / len(relevant)


def precision_at_k(ranked: list[str], relevant: set[str], k: int) -> float:
    if k <= 0:
        return 0.0
    return len(set(ranked[:k]) & relevant) / k


def reciprocal_rank(ranked: list[str], relevant: set[str]) -> float:
    for i, eid in enumerate(ranked, start=1):
        if eid in relevant:
            return 1.0 / i
    return 0.0


def evaluate_run(cases: list[dict]) -> dict:
    """cases: [{ranked, relevant(set), cutoff}]. Returns macro averages."""
    if not cases:
        return {"n": 0, "recall@1": 0.0, "recall@5": 0.0, "mrr": 0.0,
                "precision@cutoff": 0.0}
    r1 = sum(recall_at_k(c["ranked"], c["relevant"], 1) for c in cases) / len(cases)
    r5 = sum(recall_at_k(c["ranked"], c["relevant"], 5) for c in cases) / len(cases)
    mrr = sum(reciprocal_rank(c["ranked"], c["relevant"]) for c in cases) / len(cases)
    prec = sum(precision_at_k(c["ranked"], c["relevant"], c.get("cutoff", 5))
               for c in cases) / len(cases)
    return {"n": len(cases), "recall@1": round(r1, 4), "recall@5": round(r5, 4),
            "mrr": round(mrr, 4), "precision@cutoff": round(prec, 4)}

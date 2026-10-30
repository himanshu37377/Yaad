"""Deterministic scoring for Evaluation V3.

Fixes V1's substring artifacts: values are compared after
normalization (ISO dates, normalized amounts), stale values are
reported separately instead of failing correct answers that mention
history, and citations are checked for both resolution and
quote-containment. Automated scores and manual grades are kept in
separate summaries, never merged.
"""
import datetime
import json
import re
from pathlib import Path

_AMOUNT_RE = re.compile(
    r"(?:INR|USD|EUR|\$|₹|Rs\.?|rupees?)\s*\d[\d,]*(?:\.\d{1,2})?"
    r"|\d[\d,]*(?:\.\d{1,2})?\s*(?:INR|USD|EUR|\$|₹|Rs\.?|rupees?)", re.I)
_DATE_RES = [
    re.compile(r"\b\d{4}-\d{1,2}-\d{1,2}\b"),
    re.compile(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b"),
    re.compile(r"\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
               r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|"
               r"Dec(?:ember)?)\s+\d{1,2}(?:st|nd|rd|th)?(?:,?\s*\d{4})?\b", re.I),
]
_CURRENCY = (("₹", "INR"), ("$", "USD"), ("€", "EUR"), ("USD", "USD"),
             ("EUR", "EUR"), ("INR", "INR"), ("Rs", "INR"), ("rupees", "INR"))


def normalize_amount(raw: str) -> str | None:
    if not raw:
        return None
    currency = None
    for token, code in _CURRENCY:
        if re.search(rf"(?i)(?<![A-Za-z]){re.escape(token)}(?![A-Za-z])", raw):
            currency = code
            break
    number = re.search(r"\d[\d,]*(\.\d+)?", raw)
    if currency is None or number is None:
        return None
    value = number.group(0).replace(",", "")
    if "." in value:
        value = value.rstrip("0").rstrip(".")
    return f"{currency}{value}"


def amounts_in(text: str) -> set[str]:
    found = set()
    for match in _AMOUNT_RE.finditer(text or ""):
        normalized = normalize_amount(match.group(0))
        if normalized:
            found.add(normalized)
    return found


def parse_date(text: str) -> str | None:
    cleaned = text.strip().rstrip(",").replace(",", "")
    for fmt in ("%Y-%m-%d", "%B %d %Y", "%b %d %Y", "%d %B %Y", "%d %b %Y",
                "%m/%d/%Y", "%d/%m/%Y", "%m-%d-%Y", "%d-%m-%Y",
                "%B %d %Y", "%Y/%m/%d"):
        try:
            return datetime.datetime.strptime(cleaned, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def dates_in(text: str) -> set[str]:
    found = set()
    for pattern in _DATE_RES:
        for match in pattern.finditer(text or ""):
            parsed = parse_date(match.group(0))
            if parsed:
                found.add(parsed)
    return found


def load_expected(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def score_answer(entry: dict, expected: dict) -> dict:
    """entry: {status, answer, evidence: [{id, file, quote,
    quote_contained, resolves}]} as preserved by the V2 runner."""
    answer = entry.get("answer") or ""
    result = {"id": expected["id"], "category": expected.get("category", "")}
    result["status_ok"] = entry.get("status") in expected.get("expect_status", [])
    if expected.get("current_value"):
        result["value_ok"] = expected["current_value"].casefold() in answer.casefold()
    if expected.get("current_amount"):
        result["value_ok"] = expected["current_amount"] in amounts_in(answer)
    if expected.get("current_date"):
        result["value_ok"] = expected["current_date"] in dates_in(answer)
    # Stale values are informational in V2: mentioning history is not
    # an error. They fail only when the status settled on a stale value
    # as the answer, which the current-value check already catches.
    stale = [v for v in expected.get("stale_values", [])
             if v.casefold() in answer.casefold()]
    stale += [a for a in expected.get("stale_amounts", []) if a in amounts_in(answer)]
    stale += [d for d in expected.get("stale_dates", []) if d in dates_in(answer)]
    result["stale_mentioned"] = stale
    evidence = entry.get("evidence") or []
    result["citations_resolve"] = all(c.get("resolves", False) for c in evidence)
    result["citations_contained"] = all(c.get("quote_contained", False) for c in evidence)
    if expected.get("must_cite_files"):
        cited = {Path(c.get("file", "")).name for c in evidence}
        result["citation_complete"] = set(expected["must_cite_files"]) <= cited
    if expected.get("expect_status") == ["conflict"] or "conflict" in expected.get("expect_status", []):
        result["both_sides"] = (
            expected.get("current_amount") in amounts_in(answer)
            and expected.get("second_amount") in amounts_in(answer)
        ) if expected.get("second_amount") else None
    checks = [result[k] for k in ("status_ok", "value_ok", "citations_resolve",
                                  "citations_contained", "citation_complete",
                                  "both_sides") if k in result and result[k] is not None]
    result["pass"] = bool(checks) and all(checks)
    return result


def score_extraction(records: list[dict], expectations: list[dict]) -> dict:
    """records: preserved memory_records dump. A expectation matches
    when a record's combined title/detail mentions carry every
    must_mention token (dates/amounts normalized) with the expected
    kind/status where specified."""
    matched, missed = [], []
    for exp in expectations:
        hit = None
        for record in records:
            blob = f"{record.get('title', '')} {record.get('detail', '')}"
            ok = True
            for token in exp.get("must_mention", []):
                if token.isdigit() or token.replace(",", "").isdigit():
                    ok = ok and any(token.replace(",", "") in a for a in amounts_in(blob))
                else:
                    ok = ok and token.casefold() in blob.casefold()
            if exp.get("date_value"):
                ok = ok and record.get("date_value") == exp["date_value"]
            if exp.get("record_type") and exp["record_type"] != "any":
                ok = ok and record.get("record_type") == exp["record_type"]
            if exp.get("statement_kind"):
                ok = ok and record.get("statement_kind") == exp["statement_kind"]
            if exp.get("status"):
                ok = ok and record.get("status") == exp["status"]
            if ok:
                hit = record.get("id")
                break
        (matched if hit is not None else missed).append(exp["slot"])
    return {"matched": matched, "missed": missed,
            "recall": (len(matched) / len(expectations)) if expectations else None}


def summarize_automated(scored: list[dict]) -> dict:
    by_category: dict[str, dict] = {}
    for item in scored:
        bucket = by_category.setdefault(item["category"], {"n": 0, "passed": 0})
        bucket["n"] += 1
        bucket["passed"] += int(item["pass"])
    return {"kind": "automated", "total": len(scored),
            "passed": sum(int(s["pass"]) for s in scored),
            "by_category": by_category,
            "failures": [s["id"] for s in scored if not s["pass"]]}


def summarize_manual(grades: list[dict]) -> dict:
    """grades: [{id, arm, score: 0|0.5|1, stage, note}] filled by a
    human after the run. Kept strictly separate from automated."""
    return {"kind": "manual", "total": len(grades),
            "mean_score": (sum(g["score"] for g in grades) / len(grades)) if grades else None,
            "by_stage": sorted({g.get("stage", "") for g in grades}),
            "pending": len(grades) == 0}

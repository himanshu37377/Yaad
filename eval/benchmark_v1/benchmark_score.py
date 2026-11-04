"""Deterministic scoring for benchmark v1 (offline).

Rubrics: amounts/dates compare exact after normalization; conflict and
multi-doc cases need both sides present in the answer plus cited quotes
(the deterministic arms cite both sides in evidence while a live
semantic review would verbalize them); temporal-current cases fail when
a stale value is presented as current in the answer; injection and
abstention cases are graded on the status set plus must_not_contain.
"""
import datetime
import hashlib
import json
import re
from pathlib import Path

# Normalizers below are reimplemented from eval/real_eval_v2/score.py
# (same amount aliases, ISO-date parsing, containment semantics) so this
# benchmark directory stays self-contained with no cross-eval imports.
_AMOUNT_RE = re.compile(
    r"(?:INR|USD|EUR|\$|\u20b9|Rs\.?|rupees?)\s*\d[\d,]*(?:\.\d{1,2})?"
    r"|\d[\d,]*(?:\.\d{1,2})?\s*(?:INR|USD|EUR|\$|\u20b9|Rs\.?|rupees?)", re.I)
_DATE_RES = [
    re.compile(r"\b\d{4}-\d{1,2}-\d{1,2}\b"),
    re.compile(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b"),
    re.compile(r"\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
               r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|"
               r"Dec(?:ember)?)\s+\d{1,2}(?:st|nd|rd|th)?(?:,?\s*\d{4})?\b", re.I),
]
_CURRENCY = (("\u20b9", "INR"), ("$", "USD"), ("\u20ac", "EUR"), ("USD", "USD"),
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


def canonical_json(payload) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False)


def compute_freeze_hash(cases: list[dict], files_manifest: dict[str, str]) -> str:
    """Freeze hash over the case list plus the corpus sha256 manifest."""
    return hashlib.sha256(canonical_json(
        {"cases": cases, "files": files_manifest}).encode("utf-8")).hexdigest()


def load_cases(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_manifest(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def check_frozen(cases_path: Path, manifest_path: Path,
                 corpus_dir: Path) -> list[str]:
    """Verify corpus bytes, manifest, case counts, and the freeze hash."""
    problems = []
    try:
        cases_data = load_cases(cases_path)
        manifest = load_manifest(manifest_path)
    except (OSError, ValueError) as exc:
        return [f"unreadable frozen file: {exc}"]
    files = manifest.get("files", {})
    for rel, digest in sorted(files.items()):
        path = corpus_dir / rel
        if not path.is_file():
            problems.append(f"missing fixture: {rel}")
        elif hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            problems.append(f"hash mismatch (frozen fixture changed): {rel}")
    extra = set()
    for path in sorted(corpus_dir.rglob("*")):
        if path.is_file():
            extra.add(str(path.relative_to(corpus_dir)))
    for rel in sorted(extra - set(files)):
        problems.append(f"unmanifested fixture: {rel}")
    cases = cases_data.get("cases", [])
    if cases_data.get("frozen") is not True:
        problems.append("cases.json is not marked frozen")
    expected_counts = {"paraphrase": 10, "near_dup": 8, "contradiction": 10,
                       "temporal": 10, "multi_doc": 8, "missing_evidence": 8,
                       "injection": 6, "multimodal": 12}
    counts: dict[str, int] = {}
    for case in cases:
        counts[case.get("category", "")] = counts.get(case.get("category", ""), 0) + 1
    if counts != expected_counts:
        problems.append(f"case counts changed: {counts}")
    if len(cases) != 72:
        problems.append(f"expected 72 cases, found {len(cases)}")
    if compute_freeze_hash(cases, files) != cases_data.get("freeze_hash"):
        problems.append("freeze hash mismatch: cases or manifest changed since freeze")
    return problems


def _scope_text(entry: dict, case: dict) -> str:
    answer = entry.get("answer") or ""
    if case.get("value_scope") == "answer_and_quotes":
        quotes = "\n".join((item.get("quote") or "")
                           for item in entry.get("evidence") or [])
        return f"{answer}\n{quotes}"
    return answer


def score_case(entry: dict, case: dict) -> dict:
    """entry: {status, answer, evidence: [{id, file, quote, resolves,
    quote_contained}], retrieval: [ranked file basenames]}."""
    answer = entry.get("answer") or ""
    scope = _scope_text(entry, case)
    result: dict = {"id": case["id"], "category": case.get("category", "")}
    result["status_ok"] = entry.get("status") in case.get("expect_status", [])
    if case.get("current_value") is not None:
        result["value_ok"] = case["current_value"].casefold() in scope.casefold()
    if case.get("second_value") is not None:
        result["second_value_ok"] = case["second_value"].casefold() in scope.casefold()
    if case.get("current_amount") is not None:
        result["value_ok"] = case["current_amount"] in amounts_in(scope)
    if case.get("second_amount") is not None:
        result["second_value_ok"] = case["second_amount"] in amounts_in(scope)
    if case.get("current_date") is not None:
        result["value_ok"] = case["current_date"] in dates_in(scope)
    if case.get("second_date") is not None:
        result["second_value_ok"] = case["second_date"] in dates_in(scope)
    if "conflict" in case.get("expect_status", []) and (
            case.get("second_amount") or case.get("second_date")):
        result["both_sides"] = bool(result.get("value_ok") and result.get("second_value_ok"))
    if case.get("temporal_current"):
        stale = [v for v in case.get("stale_values", [])
                 if v.casefold() in answer.casefold()]
        stale += [a for a in case.get("stale_amounts", []) if a in amounts_in(answer)]
        stale += [d for d in case.get("stale_dates", []) if d in dates_in(answer)]
        result["stale_presented_as_current"] = stale
        result["temporal_clean"] = not stale
    forbidden = [t for t in case.get("must_not_contain", [])
                 if t.casefold() in answer.casefold()]
    forbidden += [a for a in case.get("must_not_contain_amounts", [])
                  if a in amounts_in(answer)]
    result["forbidden_leaked"] = forbidden
    result["abstention_clean"] = not forbidden
    evidence = entry.get("evidence") or []
    result["citations_resolve"] = all(c.get("resolves", False) for c in evidence)
    result["citations_contained"] = all(c.get("quote_contained", False) for c in evidence)
    if case.get("must_cite_files"):
        cited = {Path(c.get("file", "")).name for c in evidence}
        result["citation_complete"] = set(case["must_cite_files"]) <= cited
    retrieval = entry.get("retrieval") or []
    if case.get("expect_files"):
        wanted = set(case["expect_files"])
        hit_ranks = [rank for rank, name in enumerate(retrieval, start=1)
                     if name in wanted]
        got1 = {n for n in retrieval[:1]} & wanted
        got5 = set(retrieval[:5]) & wanted
        result["recall_at_1"] = len(got1) / len(wanted)
        result["recall_at_5"] = len(got5) / len(wanted)
        result["mrr"] = 1.0 / hit_ranks[0] if hit_ranks else 0.0
        hard = set(case.get("hard_negatives", []))
        first_want = hit_ranks[0] if hit_ranks else None
        first_hard = next((rank for rank, name in enumerate(retrieval, start=1)
                           if name in hard), None)
        result["hard_negative_pass"] = (
            first_hard is None or (first_want is not None and first_want < first_hard))
    checks = [result[k] for k in ("status_ok", "value_ok", "second_value_ok",
                                  "both_sides", "temporal_clean",
                                  "abstention_clean", "citations_resolve",
                                  "citations_contained", "citation_complete")
              if k in result and result[k] is not None]
    result["pass"] = bool(checks) and all(checks)
    return result


def summarize_arm(scored: list[dict], cases: list[dict]) -> dict:
    by_category: dict[str, dict] = {}
    for item in scored:
        bucket = by_category.setdefault(item["category"], {"n": 0, "passed": 0})
        bucket["n"] += 1
        bucket["passed"] += int(item["pass"])
    retrieval_keys = ("recall_at_1", "recall_at_5", "mrr", "hard_negative_pass")
    retrieval = {}
    for key in retrieval_keys:
        vals = [s[key] for s in scored if key in s]
        retrieval[key] = (sum(vals) / len(vals)) if vals else None
        retrieval[f"{key}_n"] = len(vals)
    cited = [s for s in scored if s.get("citation_complete") is not None]
    missing = [s for s in scored if s["category"] == "missing_evidence"]
    by_id = {c["id"]: c for c in cases}
    abstained = sum(1 for s in missing if by_id[s["id"]] and s["status_ok"]
                    and s["abstention_clean"])
    return {
        "total": len(scored),
        "passed": sum(int(s["pass"]) for s in scored),
        "accuracy": (sum(int(s["pass"]) for s in scored) / len(scored)) if scored else None,
        "by_category": by_category,
        "failures": [s["id"] for s in scored if not s["pass"]],
        "retrieval": retrieval,
        "citation_resolves_rate": (sum(int(s["citations_resolve"]) for s in scored) / len(scored)) if scored else None,
        "citation_contained_rate": (sum(int(s["citations_contained"]) for s in scored) / len(scored)) if scored else None,
        "citation_complete_rate": (sum(int(s["citation_complete"]) for s in cited) / len(cited)) if cited else None,
        "citation_complete_n": len(cited),
        "abstention_rate": (abstained / len(missing)) if missing else None,
        "abstention_n": len(missing),
    }


def false_supported_rate(transcript: list[dict]) -> float | None:
    """Missing-evidence rows answered/conflict (claimed support) per arm."""
    out = {}
    for arm in sorted({row["arm"] for row in transcript}):
        rows = [r for r in transcript if r["arm"] == arm and r["category"] == "missing_evidence"]
        out[arm] = (sum(1 for r in rows if r["status"] in ("answered", "conflict")) / len(rows)) if rows else None
    return out
